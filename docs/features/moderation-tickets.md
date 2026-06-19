# Moderation Tickets

Bot-side documentation for the per-channel support ticket system. Lives in [commands/tickets.py](../../commands/tickets.py).

## What it is

A way for members to open a **private conversation with the moderation team inside the server** instead of DMing mods individually. Each ticket is a dedicated **text channel** created inside a **mod-only category**: staff see every ticket automatically (via the category), and the requesting member gets a per-channel permission overwrite so they can see only their own.

Toggle via `ENABLE_TICKETS`. Off by default.

## Why channels in a mod-only category (and not threads or a database)

Tickets deliberately do not use PlatBot's database; ticket state (owner, status, history) lives entirely in Discord (the channel itself, plus the transcript saved to the log channel on close). This keeps tickets lightweight and resilient. (PlatBot does have a database now, but only for bot-owned records like mod notes.)

This used to use private threads, but private threads have unreliable visibility: even with *Manage Threads*, mods often can't see or list private threads they aren't members of, which defeated the "whole team can help" goal. **Channels have predictable, inheritable permissions**, so they solve that cleanly:

- A **mod-only category** (`@everyone` View denied, mod role View allowed) holds all ticket channels.
- New channels created in the category **inherit (sync)** its permissions, so every ticket is instantly visible to staff with zero per-mod setup.
- The bot then adds a **per-user overwrite** on the one channel for the requesting member, so they see only their ticket. Other members see nothing.
- The channel name and the staff alert hold the metadata; the transcript (saved on close) is the permanent record. No DB, no state file.

The tradeoff is Discord's **500-channel-per-guild cap**: tickets are deleted on close (after a transcript is saved) so they don't accumulate.

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `ENABLE_TICKETS` | Master toggle | `False` |
| `TICKET_CHANNEL_ID` | Public channel where the "Open a Ticket" panel lives (panel location only) | (none) |
| `TICKET_CATEGORY_ID` | Mod-only category where ticket channels are created | (none) |
| `TICKET_MOD_ROLE_ID` | Mod role pinged in the channel on self-serve opens, allowed to close tickets, and the role you grant category access | (none) |
| `TICKET_LOG_CHANNEL_ID` | Mod-only channel where transcripts are posted on close | (none) |
| `TICKET_ALERT_CHANNEL_ID` | Staff-only channel where an alert embed is posted on open and edited to "closed" on close (no ping) | (none) |

If the toggle is on but `TICKET_CATEGORY_ID` is missing/invalid, opening a ticket fails with a user-friendly message and a logged warning. `TICKET_LOG_CHANNEL_ID` and `TICKET_ALERT_CHANNEL_ID` are optional: without them, tickets still open and close, just without a transcript / staff alert respectively.

## How it works

### Entry points

- **`/ticket`** slash command, usable anywhere in the server (self-serve).
- **A persistent panel**: an embed with an "Open a Ticket" button. A mod posts it with **`/ticket_panel`** (mod-only, gated by `manage_messages`), which drops the panel into `TICKET_CHANNEL_ID`.
- **`/ticket_user`** (mod-only): a moderator opens a ticket and **pulls in** a target user, plus up to two more (`user2`, `user3`), with an optional `reason`.

All routes build the channel through the shared `_create_ticket` helper.

### Self-serve vs mod-initiated, and the name-prefix invariant

Ticket channels are named by origin: self-serve are `ticket-<username>-<userid>`, mod-initiated are `modticket-<username>` (Discord lowercases/sanitizes channel names; the numeric id suffix survives). The owner id is baked into the self-serve name so the duplicate guard can identify the opener from the name alone.

- **Self-serve (`/ticket`)** enforces one open ticket per user: the guard scans the category for a `ticket-` channel ending in `-<userid>`. `modticket-` channels are ignored, so a user pulled into a mod ticket can still open their own.
- **Mod-initiated (`/ticket_user`)** never deduplicates: each invocation creates a fresh `modticket-` channel. Bots passed as targets are skipped; if a target can't be granted access, the channel is still created and the mod is told who was missed.

### Opening a ticket

1. **Duplicate guard** (self-serve only): scans `TICKET_CATEGORY_ID` for a channel marking this user as owner; if found, links them to it.
2. Creates a text channel in the category (inherits the mod-only permissions).
3. Grants each requested member a per-channel overwrite (View / Send / Read History / Attach / Embed) and posts a starter embed with the **Close** button. The starter message mentions the members so they're notified, and **self-serve tickets also ping the mod role** so staff are alerted. The role ping works here (unlike in private threads) because mods can view the channel via the category. Mod-initiated tickets (`/ticket_user`) do not ping the mod role (the acting mod is already present).
4. Posts an alert to `TICKET_ALERT_CHANNEL_ID` and replies ephemerally to the opener with a link to their channel.

### Staff alert channel

A staff-only feed/log of tickets. **No ping** — the new channel appearing in the mod-only category is the live signal; the alert is the dashboard/log.

- **On open** (`_post_alert`): an embed with the opener, a link to the ticket channel, and (for mod tickets) the reason. The ticket-channel id is stashed in the embed footer (`Ticket channel ID: <id>`).
- **On close** (`_close_alert`): the original alert is found and **edited in place** to a muted color with a "Closed by @mod" line.
- **No-DB linkage**: on close the bot scans the last `ALERT_SCAN_LIMIT` (200) messages of the alert channel for the matching footer. If not found (older than the window), the close still completes; only the alert edit is skipped (logged).

### Closing a ticket

The starter message carries a persistent **Close Ticket** button.

- **Mods only.** Gated by `_is_mod` (`manage_messages` permission or the `TICKET_MOD_ROLE_ID` role). A non-mod clicking it gets an ephemeral rejection.
- Clicking Close shows an **ephemeral confirm/cancel prompt** (`ConfirmCloseView`, 60s timeout) because close is destructive.
- On confirm (`do_close`): the full channel history is saved as a `.txt` transcript to `TICKET_LOG_CHANNEL_ID`, the staff alert is edited to "closed", and the **channel is deleted**. The transcript is the permanent record.

### Persistence across restarts

The panel button and close button are persistent views (`timeout=None`, static `custom_id`s `ticket:open` / `ticket:close`), re-registered in `cog_load` via `bot.add_view`. Their callbacks resolve the cog live from `interaction.client.get_cog`, so they survive restarts with no stored state. The confirm prompt (`ConfirmCloseView`) is ephemeral and created on demand, so it is not persistent. The cog loads regardless of the toggle; only the open/close handlers are gated by `ENABLE_TICKETS`.

## Required bot permissions

- **Guild-level / category**: **Manage Channels** (create and delete ticket channels) and **Manage Roles** (set the per-user channel overwrite). The bot can only grant permissions it holds.
- **Inside the ticket category**, the bot's role must have **View Channel**, **Send Messages**, **Embed Links**, **Read Message History**, and **Attach Files** — otherwise the mod-only category's `@everyone` deny also hides the channels from the bot. Grant the bot's role access to the category explicitly.
- **In the log channel**: **Send Messages** and **Attach Files**. **In the alert channel**: **Send Messages**, **Embed Links**, and **Read Message History** (to find its own alert to edit on close).

## Gotchas and Pitfalls

- **Ticket channels count toward Discord's 500-channel guild cap.** This is why close deletes the channel. The transcript in the log channel is the archive; don't rely on the channels themselves persisting.
- **The category must grant the bot access.** Since the category denies `@everyone` View, the bot's own role needs an allow in the category or it can't see/manage the channels it creates. A missing bot grant looks like "ticket opens but the bot can't post in it / errors."
- **Channel permission overrides ignore role hierarchy.** When setting up category access for the mod role, remember Discord resolves channel overrides by combining all of a member's role overrides (denies then allows; allow wins) — role *position* is irrelevant. An explicit allow on the mod role beats a deny on another role only if it's set to the solid green ✓, not the neutral state.
- **The close alert lookup is bounded** to the most recent `ALERT_SCAN_LIMIT` (200) alert-channel messages. A ticket left open longer than 200 newer alerts won't have its alert updated on close (the close still succeeds). Raise the constant if your volume makes this likely.
- **`TICKET_CHANNEL_ID` is only the panel's home** now; tickets are created in `TICKET_CATEGORY_ID`. Don't point them at the same place.
- **PlatPursuit is not involved.** Tickets are pure-Discord moderation and don't touch the PlatPursuit API.
