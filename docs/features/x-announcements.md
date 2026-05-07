# X Announcements

Bot-side documentation for the X (Twitter) post announcer. Lives in [commands/x_announcements.py](../../commands/x_announcements.py).

## What it is

A background poller that watches an RSS feed of an X profile and posts new entries to a Discord announcement channel with a role ping. Toggle via `ENABLE_X_ANNOUNCEMENTS`. Off by default.

The cog never talks to X directly. It reads an RSS feed produced by an external service (e.g. RSS.app), which is the layer that handles X-side scraping and detection.

## Why RSS, not the X API

X's free API tier is write-only as of 2024. Reading a user's recent posts requires the Basic tier ($100/mo) or higher. RSS.app's free tier converts a public X profile into an RSS feed at ~1hr poll cadence on their side. That sets the practical freshness floor regardless of how often the bot polls.

If RSS.app's free tier ever stops being viable, the swap-out point is the feed URL: any service that produces a standards-compliant RSS feed of the X profile will work without code changes.

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `ENABLE_X_ANNOUNCEMENTS` | Master toggle | `False` |
| `X_RSS_FEED_URL` | RSS feed URL for the target X profile | (none) |
| `X_ANNOUNCEMENT_CHANNEL_ID` | Discord channel to post in | (none) |
| `X_ANNOUNCEMENT_ROLE_ID` | Role to ping per announcement | (none) |
| `X_POLL_INTERVAL_MINUTES` | Bot-side poll cadence | `15` |
| `X_OWN_HANDLE` | X handle (no `@`) whose own tweets should be announced. Retweets are filtered by comparing the entry's link URL. Unset = no filtering. | (none) |
| `X_TEST_ANNOUNCEMENT_CHANNEL_ID` | Test channel (admin/testing server), used by the test admin endpoint | (none) |
| `X_TEST_ANNOUNCEMENT_ROLE_ID` | Test role, used by the test admin endpoint | (none) |

If the toggle is on but any required field is missing, the cog logs a warning and stays idle. It does not raise.

## Admin endpoints

Two FastAPI endpoints on the bot's HTTP server, intended for invocation from the Render shell console via `curl`. Both use the existing `API_KEY` Bearer auth (same as `/assign-role` and `/remove-role`).

| Endpoint | Purpose | Updates baseline? |
|---|---|---|
| `POST /admin/x-announce/test` | Posts the latest feed entry to `X_TEST_ANNOUNCEMENT_CHANNEL_ID` with `X_TEST_ANNOUNCEMENT_ROLE_ID`. Used to preview real content in a test server before flipping the production toggle. | No |
| `POST /admin/x-announce/latest` | Posts the latest feed entry to the production channel/role. Used as a manual recovery if the auto-poller missed a post or errored. | Yes — sets `last_seen_id` to the entry that was just announced, so the auto-poller will not re-announce it on its next tick. |

Both reuse the running cog's session and Discord connection — they do not spin up a new client. Both are safe to invoke even when `ENABLE_X_ANNOUNCEMENTS=False`, since the cog is loaded regardless and only the poll loop is gated by the toggle.

### From the Render shell

```bash
curl -X POST http://localhost:$PORT/admin/x-announce/test \
  -H "Authorization: Bearer $API_KEY"

curl -X POST http://localhost:$PORT/admin/x-announce/latest \
  -H "Authorization: Bearer $API_KEY"
```

Successful responses return `{"status": "ok", "link": "https://x.com/..."}`. Failure modes:

| Status | Meaning |
|---|---|
| 400 | Required env vars (channel/role IDs) not set |
| 401 | Bad or missing API key |
| 502 | Feed fetch failed, feed empty, or Discord rejected the send |
| 503 | Cog not loaded (bot still booting, or extension load failed) |

## Behavior

### Message format

Plain content with role ping and bare URL: `<@&ROLE_ID> New post: https://x.com/.../status/...`. Discord auto-unfurls X URLs into a preview card when its scraper has access; if unfurling is blocked, users see a bare link, which is acceptable.

### Role ping

The send call passes `allowed_mentions=AllowedMentions(roles=True, everyone=False, users=False)`. This means the role pings even if it is *not* set "mentionable" in Discord's role settings, while preventing accidental @everyone or user pings. Keep the announcement role non-mentionable in Discord proper to prevent random members from pinging it.

### Retweet filtering

When `X_OWN_HANDLE` is set, the cog filters feed entries to those whose `<link>` URL begins with `https://x.com/<handle>/status/`. Retweets in RSS.app feeds carry the *original author's* link (e.g. `https://x.com/SomeoneElse/status/...`) rather than the feed owner's, so the URL handle is a reliable, RSS.app-format-independent signal.

The filter is applied uniformly to the auto-poll loop, the `/admin/x-announce/latest` endpoint, and the `/admin/x-announce/test` endpoint — so manual fires also skip retweets and announce the most recent *owned* tweet.

If a feed entry has a link that doesn't match the expected X URL pattern at all (unexpected source format), the cog logs a warning and allows it through rather than silently dropping. This favors not-missing-real-posts over zero-noise; if a future RSS oddity ever causes spam, tighten this to drop-unknowns.

### Skip-on-startup baseline

On the first poll after startup, the cog records the latest feed item ID as `last_seen_id` *without announcing*. This avoids spam when the bot was offline for hours or days.

Tradeoff: any post made during downtime is permanently skipped on this side. Acceptable for a low-frequency announcement use case. If backfill ever matters, persist `last_seen_id` to disk via a Docker volume mount (state file in a container path) and load it in `cog_load`.

### Baseline-drift safety

If the previously-stored `last_seen_id` is no longer present in the current feed (item rolled off the end of the feed window), the cog resets the baseline to the new latest *without announcing the gap*. This prevents the cog from interpreting an entire feed window as "all new" and spamming.

There is also a hard cap of `MAX_ANNOUNCEMENTS_PER_POLL = 5` as a second-line defense. If a real burst exceeds this, only the most recent 5 are announced and the rest are silently dropped.

### Single-process state

`last_seen_id` and `baseline_set` are in-memory on the cog instance. The bot is single-process so this is sufficient. Restart resets the baseline (see above).

## Operational notes

- The cog owns its own `aiohttp.ClientSession` rather than reusing `bot.api_session`, because `bot.api_session` carries PlatPursuit auth headers that should not be sent to RSS.app.
- `feedparser` is sync. The cog calls it via `asyncio.to_thread` to keep the event loop unblocked. Feeds are small enough that this is a non-issue, but the wrapper is cheap insurance.

## Gotchas and Pitfalls

- **Discord X-link unfurling has been intermittent** since X's open-graph access changes. If unfurls disappear, that is an X-side / Discord-side issue, not a bot bug. The link is still posted and clickable.
- **RSS.app free-tier limits**: one feed, ~1hr poll on their side. If you upgrade or switch providers, no code change is needed, only the `X_RSS_FEED_URL` env var.
- **Polling more often than RSS.app refreshes is wasteful but harmless.** A 15-minute bot poll against an hourly source still respects the upstream freshness; the cog just sees no-op polls in between.
- **First post after a long downtime is silently skipped.** This is intentional (see Skip-on-startup baseline). If a moderator notices a missed announcement, they can post the link manually.
- **Role ping requires the role ID, not the role name.** Right-click the role in Discord with Developer Mode on to copy the ID.
