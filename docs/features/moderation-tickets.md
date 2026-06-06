# Moderation Tickets

Bot-side documentation for the private-thread support ticket system. Lives in [commands/tickets.py](../../commands/tickets.py).

## What it is

A way for members to open a **private conversation with the moderation team inside the server** instead of DMing mods individually. Each ticket is a Discord **private thread**: visible only to the member who opened it and to staff. This keeps mod conversations in the server (where the whole team has tooling and history) rather than scattered across individual DMs.

Toggle via `ENABLE_TICKETS`. Off by default.

## Why private threads (and not channels or a database)

PlatBot has no local database. A ticket system normally needs to persist ticket state (owner, status, history). Private threads let **Discord itself be the datastore**:

- The thread *is* the ticket. Its membership is the access control, its name is the label, its message history is the record.
- A **private thread** is visible only to members explicitly added plus anyone with *Manage Threads*. So the opener sees only their own ticket; other regular members can't see it at all; mods see and can search every ticket, including archived ones.
- Threads avoid Discord's 500-channel-per-guild cap that a channel-per-ticket approach would hit.
- Nothing is persisted outside Discord. No DB, no state file, consistent with the rest of the bot.

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `ENABLE_TICKETS` | Master toggle | `False` |
| `TICKET_CHANNEL_ID` | Support channel where the panel lives and private threads are created | (none) |
| `TICKET_MOD_ROLE_ID` | Mod role pinged in the alert channel on new self-serve tickets, and allowed to close tickets | (none) |
| `TICKET_LOG_CHANNEL_ID` | Mod-only channel where transcripts are posted on close | (none) |
| `TICKET_ALERT_CHANNEL_ID` | Staff-only channel where an alert is posted (and pinged) when a ticket opens, and edited to "closed" when it closes | (none) |

If the toggle is on but `TICKET_CHANNEL_ID` is missing, opening a ticket fails with a user-friendly message and a logged warning. If `TICKET_LOG_CHANNEL_ID` is unset, tickets still close cleanly, just without a saved transcript. **If `TICKET_ALERT_CHANNEL_ID` is unset, mods are not notified when a ticket opens** (see the alert-channel section below for why this is the notification mechanism), so it should be set for the system to be useful.

## How it works

### Entry points

- **`/ticket`** slash command, usable anywhere in the server (self-serve).
- **A persistent panel**: an embed with an "Open a Ticket" button. A mod posts it with **`/ticket_panel`** (mod-only, gated by `manage_messages`), which drops the panel into `TICKET_CHANNEL_ID`.
- **`/ticket_user`** (mod-only): a moderator opens a ticket and **pulls in** a target user, plus up to two more (`user2`, `user3`), with an optional `reason`. Used to start a staff-driven conversation rather than waiting for the member to reach out.

The self-serve routes call the shared `handle_open` logic; both flows ultimately build a thread through the shared `_create_ticket` helper.

### Self-serve vs mod-initiated, and the name-prefix invariant

Threads are named by origin: self-serve tickets are `ticket-<username>-<userid>`, mod-initiated tickets are `modticket-<username>`. The owner id is baked into the self-serve name so the duplicate guard can identify the opener from the name alone, without fetching thread members. Membership can't be trusted to identify the owner because a mod who responds to a ticket auto-joins it as a member. Without the prefix distinction, a user pulled into a mod ticket would also be treated as already having an open ticket and blocked from using `/ticket`.

So the rules are:
- **Self-serve (`/ticket`)** enforces one open ticket per user, but the duplicate scan only considers `ticket-` threads. A user pulled into a `modticket-` thread can still open their own.
- **Mod-initiated (`/ticket_user`)** never deduplicates: each invocation creates a fresh `modticket-` thread, even if the target already has tickets open. Bots passed as targets are skipped; if a target can't be added (e.g. they left), the thread is still created and the mod is told who was missed.

### Opening a ticket

1. **Duplicate guard** (no DB): scans the support channel's active (non-archived) private threads for one whose name marks this user as the owner (a `ticket-` thread ending in `-<userid>`). If found, they get a link to it instead of a second thread.
2. Creates a private thread named `ticket-<username>-<userid>` in `TICKET_CHANNEL_ID` with `invitable=False` (members added to a ticket can't pull in others) and a 7-day auto-archive.
3. Adds only the opener (mod-initiated tickets add the target users), posts a starter embed, and mentions the added members so they are notified. **Mods are not added to the thread** and are not pinged inside it.
4. Posts an alert to `TICKET_ALERT_CHANNEL_ID` (see below) and replies ephemerally to the opener with a link to their new thread.

### Staff alert channel (how mods are notified)

Mods are notified through a dedicated staff channel, not inside the ticket thread. This is deliberate: a role @mention inside a private thread does **not** notify role members who aren't already in the thread (see the gotcha below), and adding every mod to every ticket clutters their sidebars. A normal staff channel sidesteps both problems: role pings work there as expected.

- **On open** (`_post_alert`): an embed is posted to the alert channel with the opener, a clickable jump link to the thread, and (for mod tickets) the reason. Self-serve tickets **ping `TICKET_MOD_ROLE_ID`**; mod-initiated tickets are posted **without a ping** (the acting mod already knows, so it is just a log entry for the team). Mods click through, and replying in the thread auto-joins them.
- **On close** (`_close_alert`): the original alert is **edited in place** to a muted color with a "Closed by @mod" line, and the ping text is removed.
- **No-DB linkage**: the alert stashes its thread id in the embed footer (`Ticket thread ID: <id>`). On close, the bot scans the last `ALERT_SCAN_LIMIT` (200) messages of the alert channel for the matching footer and edits that message. If it isn't found (e.g. an alert older than the scan window), the close still completes; only the alert update is skipped (logged).

### Closing a ticket

The starter message carries a persistent **Close Ticket** button.

- **Mods only.** Close is gated by `_is_mod` (either `manage_messages` permission or the `TICKET_MOD_ROLE_ID` role). If the opener clicks it, they get an ephemeral "moderators only" reply.
- On close: the full thread history is dumped to a `.txt` transcript in `TICKET_LOG_CHANNEL_ID` (if set), the staff alert is edited to "closed", a closing notice is posted in the thread, and the thread is **locked and archived** (locked + archived, not deleted, so mods can still read or unarchive it later).

### Persistence across restarts

The panel button and close button are persistent views (`timeout=None`, static `custom_id`s `ticket:open` / `ticket:close`), re-registered in `cog_load` via `bot.add_view`. Their callbacks resolve the cog live from `interaction.client.get_cog`, so they keep working after a restart with no stored state. The cog loads regardless of the toggle; only the open/close handlers are gated by `ENABLE_TICKETS`.

## Required bot permissions

In the ticket channel the bot needs: **Create Private Threads**, **Send Messages in Threads**, **Manage Threads** (to add users, lock, and archive), and **Read Message History** (to build transcripts). In the log channel it needs **Send Messages** and **Attach Files**. In the alert channel it needs **Send Messages**, **Embed Links**, and **Read Message History** (to find its own alert to edit on close).

## Gotchas and Pitfalls

- **Auto-archive is not deletion.** Private threads auto-archive after 7 days of inactivity (or immediately on close). Archived tickets are preserved and any mod can unarchive them. History is never lost unless a mod manually deletes the thread.
- **A role @mention does NOT notify non-members of a private thread.** This is the key Discord constraint behind the design, and the reason mods are notified via a separate staff channel rather than a ping inside the thread. Mentioning `@mods` in a private thread only pings role holders who are *already in the thread*; everyone else gets nothing (Discord won't fan a role ping out to people who can't see the thread). Role pings in a normal channel (the alert channel) work as expected. Mentioning a *user* in a private thread does pull them in, but a role does not.
- **Duplicate-open scan keys on the owner id in the thread name, not membership.** A mod who responds to a ticket auto-joins it, so membership can't reliably identify who a ticket belongs to (a mod running `/ticket` could otherwise match a ticket they had replied to). The opener id is encoded in the self-serve thread name (`ticket-<username>-<userid>`) and the guard matches on that, scanning only `ticket-` threads so `modticket-` ones are ignored.
- **The close alert lookup is bounded.** `_close_alert` only scans the most recent `ALERT_SCAN_LIMIT` (200) messages of the alert channel. A ticket left open longer than 200 newer alerts would not have its alert updated on close (the close itself still succeeds). Raise the constant if your ticket volume makes this likely.
- **`TICKET_CHANNEL_ID` must be a normal text channel**, not a forum or a category. Private threads are created on a text channel.
- **Role ping requires the role ID, not the name.** Enable Developer Mode in Discord and right-click the role to copy its ID.
- **PlatPursuit is not involved.** Tickets are pure-Discord moderation and do not touch the PlatPursuit API. If ticket analytics are ever wanted, logging metadata to the API on close is a clean future add-on.
