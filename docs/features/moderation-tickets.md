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
| `TICKET_MOD_ROLE_ID` | Mod role pinged on new tickets and allowed to close them | (none) |
| `TICKET_LOG_CHANNEL_ID` | Mod-only channel where transcripts are posted on close | (none) |

If the toggle is on but `TICKET_CHANNEL_ID` is missing, opening a ticket fails with a user-friendly message and a logged warning. If `TICKET_LOG_CHANNEL_ID` is unset, tickets still close cleanly, just without a saved transcript.

## How it works

### Entry points

- **`/ticket`** slash command, usable anywhere in the server.
- **A persistent panel**: an embed with an "Open a Ticket" button. A mod posts it with **`/ticket_panel`** (mod-only, gated by `manage_messages`), which drops the panel into `TICKET_CHANNEL_ID`.

Both routes call the same `handle_open` logic.

### Opening a ticket

1. **Duplicate guard** (no DB): scans the support channel's active (non-archived) private threads and checks membership via `fetch_members`. If the user already has an open ticket, they get a link to it instead of a second thread.
2. Creates a private thread named `ticket-{username}` in `TICKET_CHANNEL_ID` with `invitable=False` (members added to a ticket can't pull in others) and a 7-day auto-archive.
3. Adds the opener, posts a starter embed, and pings `TICKET_MOD_ROLE_ID` + the opener via scoped `allowed_mentions` (role + user only, never @everyone).
4. Replies ephemerally to the opener with a link to their new thread.

### Closing a ticket

The starter message carries a persistent **Close Ticket** button.

- **Mods only.** Close is gated by `_is_mod` (either `manage_messages` permission or the `TICKET_MOD_ROLE_ID` role). If the opener clicks it, they get an ephemeral "moderators only" reply.
- On close: if `TICKET_LOG_CHANNEL_ID` is set, the full thread history is dumped to a `.txt` transcript and posted there with a summary embed. Then the thread is **locked and archived** (locked + archived, not deleted, so mods can still read or unarchive it later).

### Persistence across restarts

The panel button and close button are persistent views (`timeout=None`, static `custom_id`s `ticket:open` / `ticket:close`), re-registered in `cog_load` via `bot.add_view`. Their callbacks resolve the cog live from `interaction.client.get_cog`, so they keep working after a restart with no stored state. The cog loads regardless of the toggle; only the open/close handlers are gated by `ENABLE_TICKETS`.

## Required bot permissions

In the ticket channel the bot needs: **Create Private Threads**, **Send Messages in Threads**, **Manage Threads** (to add users, lock, and archive), and **Read Message History** (to build transcripts). In the log channel it needs **Send Messages** and **Attach Files**.

## Gotchas and Pitfalls

- **Auto-archive is not deletion.** Private threads auto-archive after 7 days of inactivity (or immediately on close). Archived tickets are preserved and any mod can unarchive them. History is never lost unless a mod manually deletes the thread.
- **Mods see tickets via permission, not membership.** Only the opener (and the bot) are added as thread *members*; mods read tickets through *Manage Threads*. This is why the duplicate-open scan keys on the opener's membership, not on the mod role.
- **`TICKET_CHANNEL_ID` must be a normal text channel**, not a forum or a category. Private threads are created on a text channel.
- **Role ping requires the role ID, not the name.** Enable Developer Mode in Discord and right-click the role to copy its ID.
- **PlatPursuit is not involved.** Tickets are pure-Discord moderation and do not touch the PlatPursuit API. If ticket analytics are ever wanted, logging metadata to the API on close is a clean future add-on.
