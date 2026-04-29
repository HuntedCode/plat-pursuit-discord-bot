# Community Trophy Tracker

Bot-side documentation for the `/trophystats` slash command group. The corresponding PlatPursuit-side feature (the daily Discord webhook post and the underlying data model) is documented in the PlatPursuit repo at `docs/features/community-trophy-tracker.md`.

## What it is

`/trophystats` exposes community-wide trophy aggregates on demand, complementing the daily auto-post that PlatPursuit ships at ~12:30 PM ET. Three subcommands:

| Subcommand | Source endpoint | Purpose |
|---|---|---|
| `/trophystats today` | `GET /community-stats/today/` | Live, partial-day totals (60s server-side cache) |
| `/trophystats yesterday` | `GET /community-stats/<yesterday-ET>/` | The most recent canonical daily summary |
| `/trophystats records` | `GET /community-stats/records/` | All-time records per stat with the date set |

Stats tracked: total trophies, total platinums, total Ultra Rares (PSN tier), and a weighted **PP Score** = `trophies + (5 × plats) + (3 × URs)`.

## How it connects to PlatPursuit

These are public, unauthenticated endpoints (`AllowAny` permissions on the PlatPursuit side). The bot's existing `API_BASE_URL` and `API_KEY` are used by convention, but the API key is not required for community-stats endpoints specifically. Eligibility for inclusion: trophies earned by profiles where `discord_id IS NOT NULL`, in non-shovelware games.

The bot is a read-only consumer here — it does not write to or trigger the daily aggregation. PlatPursuit owns scheduling, computation, and the daily webhook post.

## Design decisions

### Grouped command vs. flat commands

Implemented as a single `/trophystats` `app_commands.Group` with three subcommands rather than three top-level commands. Tighter command-tree real estate, clearer mental model ("all community stats live under one command").

### Ephemeral with opt-in publish

All three subcommands respond ephemerally by default, with a "Publish to Channel" button. This matches `/summary`'s pattern: the user gets to decide whether the result becomes part of the channel conversation. Public visibility is valuable for community engagement, but pushing every invocation publicly would be noisy.

### Two-tier rate limiting

- **Slash command level**: 3 invocations per 30 seconds per user (`app_commands.checks.cooldown`). Prevents trivial spam of the API endpoints.
- **Publish button level**: 60-second per-user window enforced in-process. The publish button is the channel-spam vector (one click → one public embed), so it gets a stricter limit than the ephemeral query itself.
- **Publish button is invoker-locked**: only the user who ran the slash command can publish its result. Other users clicking the button get an ephemeral "only the invoker can publish" reply.

The publish-button rate limit is process-local (a `dict[user_id, monotonic_timestamp]` on the cog instance). This is acceptable because the bot is single-process and the cooldown window is short; if either ever changes, this needs revisiting.

### Embed colors

- `0x003791` (PlatPursuit brand blue) for `today` and `yesterday`.
- `0xFFD700` (gold) for `records`. Mirrors the daily auto-post's behavior of switching to gold on record-setting days.

### ET timezone math

`/trophystats yesterday` computes "yesterday in ET" using `zoneinfo.ZoneInfo("America/New_York")` (Python 3.12 stdlib). This handles DST transitions correctly, unlike a naive UTC offset. The `tzdata` package is pinned in `requirements.txt` for Windows compatibility (Linux containers ship IANA data already).

### Number formatting

Uses the shared `format_number` helper in `utils/formatting.py`, also consumed by `/summary`. Returns `"0"` for `None`, which is appropriate for day-summary fields (always populated by the API). The `records` endpoint's per-stat `null` sentinel is handled by a local `record_line()` helper that renders `_no data yet_` instead.

## Gotchas and Pitfalls

- **404 on yesterday's date**: possible if the daily cron hasn't run yet, or failed for that day. The cog returns a generic "no data found" message. If the daily cron is consistently delayed, consider a friendlier "the daily summary hasn't gone out yet" copy.
- **Pre-launch records**: before any `CommunityTrophyDay` rows exist, `/trophystats records` returns `null` per stat. The cog renders these as `_no data yet_` rather than `0`.
- **Sync lag on `today`**: live totals reflect only synced data. A user who earned a platinum minutes ago may not see it counted yet. The cog surfaces the API's `data_freshness_note` in the embed footer to set this expectation.
- **API down**: every cog command depends on PlatPursuit. The shared `_get_json` helper translates network failures and non-2xx responses into ephemeral user-friendly messages.
- **Discord slash-command propagation**: after registering or editing a subcommand, global sync can take up to an hour. Use guild-specific sync during dev.
- **`tzdata` on Windows**: `ZoneInfo("America/New_York")` raises `ZoneInfoNotFoundError` on Windows without the `tzdata` PyPI package. Pinned in `requirements.txt`. The Docker image (Linux) doesn't need it, but it's installed everywhere for consistency.

## Future ideas (not in scope)

- Weekly summary aggregating the last 7 daily rows.
- Per-user contribution ("you contributed N trophies to yesterday's total"). Would require a new PlatPursuit endpoint keyed on `discord_id`.
- Auto-react / pin behavior when a record is set on the daily auto-post.
