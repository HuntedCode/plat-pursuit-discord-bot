# PlatBot — Project CLAUDE.md

> This file contains project-specific standards. See `~/.claude/CLAUDE.md` for universal collaboration, workflow, and quality standards that apply across all projects.

## Project Overview

**PlatBot** is the Discord bot companion to PlatPursuit. It bridges the PlatPursuit web platform with Discord communities, enabling PSN account linking, trophy data display, role synchronization based on achievements, and community engagement features.

- **Relationship to PlatPursuit**: PlatBot is a consumer of PlatPursuit's API. PlatPursuit owns all trophy/user data, which PlatBot reads via the API. Changes to PlatPursuit's API endpoints, data models, or authentication may require corresponding updates here.
- **Own database (bot-owned data only)**: PlatBot has a small SQLAlchemy-backed database for data it owns itself (currently moderation notes), so it can stand on its own. It uses its own dedicated Postgres instance, configured via `DATABASE_URL` and portable (Postgres in prod, SQLite for local dev). Tables are prefixed `platbot_` by convention. PlatPursuit-owned data still comes from the API, not this database.
- **Deployment**: Docker containerized (Python 3.12, multi-stage build)

---

## Tech Stack

| Layer             | Technology                          |
| ----------------- | ----------------------------------- |
| Language          | Python 3.12                         |
| Bot Framework     | discord.py 2.7.x                    |
| Web Server        | FastAPI + Uvicorn (webhook receiver) |
| HTTP Client       | aiohttp (async API calls)           |
| Validation        | Pydantic                            |
| Database (ORM)    | SQLAlchemy 2.0 (async); asyncpg (Postgres) / aiosqlite (dev) |
| Configuration     | python-dotenv (.env files)          |
| Containerization  | Docker                              |

---

## Architecture

### Dual Runtime

PlatBot runs two services in the same process:
1. **Discord bot** (discord.py): Handles slash commands, events, and Discord interactions
2. **FastAPI server**: Receives webhook calls from PlatPursuit (role assignments, role removals)

### Communication with PlatPursuit

- All API calls go through `API_BASE_URL` with `API_KEY` header authentication
- Key API endpoints consumed: `generate-code/`, `verify/`, `summary/`, `trophy-case/`, `sync-roles/`, `refresh/`, `unlink/`, `check-linked/`, `recheck-badges/`, `community-stats/today/`, `community-stats/<date>/`, `community-stats/records/`
- PlatPursuit triggers role operations by calling PlatBot's `/assign-role` and `/remove-role` FastAPI endpoints
- Outbound API calls share one `aiohttp` session built by `make_api_session`, with a total timeout of `API_TIMEOUT_SECONDS` (30), so a hung PlatPursuit request fails instead of stalling a command forever (the tree error handler then tells the user something went wrong). `/recheck_badges_user` passes its own longer `RECHECK_TIMEOUT` (120), because a full recheck evaluates every badge in one request

### Webhook API Limits

The FastAPI server is public, so it is built to stay safe even if `API_KEY` leaks:

| Limit | Where | Why |
|-------|-------|-----|
| No `/docs`, `/redoc` or `/openapi.json` | `FastAPI(docs_url=None, ...)` in `bot.py` | Nothing advertises the routes or their bodies |
| Home server only | `require_home_guild` in `bot.py` | A role request naming another `guild_id` gets 403. PlatPursuit sends no `guild_id`, which means the home server |
| Not the bot's own roles | `API_PROTECTED_ROLE_IDS` in `bot.py`, checked by `require_api_togglable_role` | Verified, Unverified and the ticket mod role get 403. The bot handles the first two itself (`/link`, `/unlink`, the backfill), and the ticket mod role grants access by ID, not by permission flags, so the role guard would not catch it. PlatPursuit only sends badge, milestone and premium roles |
| No powerful roles | `utils/role_guard.refusal_reason`, checked up front by `require_api_togglable_role` and again by both role workers | The bot never adds or removes @everyone, an integration-managed role, or any role granting a permission in `PRIVILEGED_PERMISSIONS` (administrator, manage_*, kick, ban, timeout, voice moderation, mention_everyone, audit log, insights), server-wide or through any channel's overwrites. Unlocking a private channel is allowed (premium channels need it). A role already in the bot's cache is refused with a 403 and the reason, which PlatPursuit logs; the workers check again when they run (the role can change while queued) and log refusals as warnings. They check the role before the member is looked up, so a refused request costs no Discord API calls |

A new role route must call `require_home_guild` and `require_api_togglable_role` first, and any role change the webhook API triggers must go through the workers so the role guard applies. (Role changes a user triggers themselves, like the Verified swap in `utils/roles.py`, call Discord directly.)

`PRIVILEGED_PERMISSIONS` is a list of named flags, so a permission Discord adds later is allowed until it is named here. When a discord.py upgrade names a new moderation permission, add it (2.7 named `pin_messages` and `bypass_slowmode`, which arrived in 2025). Only names discord.py knows can be checked: it drops unnamed bits when it builds a channel overwrite.

### Worker Queue System

Role assignments and removals go through two async queues (`role_queue`, `role_removal_queue`), each drained by one worker. Both workers run the same `apply_role_change` in `bot.py`, one adding and one removing, with a short pause between items so a bulk change (a badge sweep, the Unverified backfill) stays under Discord's rate limits.

| Discord answers | The worker |
|---|---|
| 429 | Waits for `Retry-After` (1s if it is missing or unreadable), then tries again |
| 5xx, or a network error | Waits 1s, 2s, 4s, 8s between attempts (`ROLE_RETRY_BASE_SECONDS`) |
| Any other 4xx (the role sits above the bot's, a missing permission) | Logs it once and drops the request: a retry cannot succeed |
| Any other exception (a bug) | Logs it with its traceback and drops the request |

Every change gets `ROLE_MAX_ATTEMPTS` (5) attempts, a 429 included; only a request that runs out logs "Gave up". The worker loop itself catches anything a request raises, so one bad request can never end it (a dead worker would stop every role change until a restart, and hang the backfill's `queue.join()`). Tests: `tests/test_role_workers.py`.

These retries sit on top of discord.py's own: the library already retries 429s and Discord's 500/502/504 itself before raising. So a 429 or 5xx that reaches the worker has outlasted those, and a persistent outage can hold one queue item for a couple of minutes.

### Database

PlatBot owns a small SQLAlchemy (async) database for bot-owned data (currently moderation notes). The engine is created at startup from `DATABASE_URL` and exposed as `bot.db_sessionmaker` for cogs (`async with bot.db_sessionmaker() as session:`), mirroring how `bot.api_session` is shared. It uses a dedicated Postgres instance; tables are created/managed by PlatBot's SQLAlchemy metadata (`create_all` with `checkfirst`, so it only adds missing tables and never alters existing ones). The URL is engine-agnostic: `postgresql://`/`postgres://` forms are auto-converted to the asyncpg driver (with `sslmode` handled for Render), and `sqlite+aiosqlite://` works for local dev. If `DATABASE_URL` is unset, DB-backed features degrade gracefully (cogs check `bot.db_sessionmaker`). Schema changes are currently applied via `create_all`; introduce Alembic when the schema grows.

---

## Project Structure

```
PlatBot/
├── bot.py                  # Main entry: bot setup, FastAPI app, worker queues, event handlers, inline /ping
├── commands/               # discord.py Cogs (one feature per file)
│   ├── link.py             # /link: PSN account linking with verification code
│   ├── unlink.py           # /unlink: disconnect PSN account
│   ├── refresh.py          # /refresh: force refresh PSN profile (1/hr cooldown)
│   ├── refresh_user.py     # /refresh_user: mod command to refresh another user
│   ├── summary.py          # /summary: trophy progress, stats, rarest trophies
│   ├── trophy_case.py      # /trophy_case: paginated platinum display
│   ├── sync_roles.py       # /sync-roles: manually sync achievement roles
│   ├── sync_roles_user.py  # /sync_roles_user: mod command to sync another user
│   ├── recheck_badges_user.py # /recheck_badges_user: mod command for full badge audit
│   ├── backfill_unverified.py # /backfill_unverified: one-time transition command giving the Unverified role to members without Verified (delete once run)
│   ├── community_stats.py  # /trophystats today|yesterday|records: community-wide trophy aggregates
│   ├── link_help.py        # !link: public instructions for PSN account linking
│   ├── welcome.py          # Event listeners: welcome embed on join, plus a verified welcome in its own channel when a member completes /link verification
│   ├── member_events.py    # Event listener: auto-unlinks PSN profile on member leave (toggle: ENABLE_UNLINK_ON_LEAVE)
│   ├── audit_log.py        # Event listener: member join/leave logging to audit channel
│   ├── x_announcements.py  # Background task: polls an X RSS feed and announces new posts to a channel (toggle: ENABLE_X_ANNOUNCEMENTS)
│   ├── tickets.py          # /ticket + panel button (self-serve) and /ticket_user (mod-initiated): per-channel moderation tickets in a mod-only category, staff alert-channel notifications, mod-only close (deletes channel after saving transcript) (toggle: ENABLE_TICKETS)
│   └── notes.py            # /note add|view: mod-only per-user moderation notes (append-only, dated + attributed), stored in the database
├── db/                     # SQLAlchemy (async) persistence layer for bot-owned data
│   ├── models.py           # Declarative Base + ModNote model (platbot_mod_notes table)
│   └── engine.py           # Engine/session factory + DATABASE_URL normalization (init_db)
├── utils/                  # Shared utilities
│   ├── api_errors.py       # What a user is told when a PlatPursuit API call fails (api_error_message)
│   ├── command_errors.py   # Error replies for slash commands (tree handler) and buttons (ReplyOnErrorView)
│   ├── formatting.py       # Number/string formatters shared across cogs
│   ├── role_guard.py       # Which roles the webhook API may never touch
│   └── roles.py            # Verified/Unverified role swap shared by /link and /unlink
├── scripts/                # Operational CLI helpers (run inside the container via Render shell)
│   └── x_admin.py          # Wrapper for /admin/x-announce/{test,latest} (curl-free)
├── docs/                   # Feature documentation (see docs/features/)
├── requirements.txt        # Python dependencies
├── Dockerfile              # Multi-stage Docker build
├── .env                    # Environment configuration (not committed)
└── CLAUDE.md               # This file
```

---

## Coding Conventions

### Python Style

- Async/await throughout: all command handlers and API calls are async
- Type hints on all function signatures
- Logging via `logging.getLogger('psn_api')` (shared logger name across modules)

### Naming

| Thing              | Convention           | Example                    |
| ------------------ | -------------------- | -------------------------- |
| Files              | snake_case.py        | `sync_roles.py`            |
| Classes            | PascalCase           | `TrophyCaseCog`            |
| Functions          | snake_case           | `fetch_user_summary`       |
| Constants          | SCREAMING_SNAKE_CASE | `API_BASE_URL`             |
| Cog classes        | PascalCase + `Cog`   | `LinkCog`, `RefreshCog`    |

### Command Patterns

- All slash commands use deferred responses (`interaction.response.defer()`) to avoid Discord's 3-second timeout
- User-facing responses are ephemeral (visible only to the invoking user) unless displaying public data
- Confirmation flows use `discord.ui.View` with button components
- Paginated displays (trophy case) use button-based navigation views
- **Mod commands check permissions twice.** `@app_commands.default_permissions(...)` only hides a command, and a server admin can widen it in Server Settings > Integrations. So every mod command also carries `@app_commands.checks.has_permissions(...)` with the same permission, which runs on each use. A failed check is answered privately by `utils/command_errors.handle_app_command_error` (set as `bot.tree.on_error`), which also answers cooldowns and any other error a command leaves unhandled. It steps aside for a command that has its own handler (a `cog_app_command_error`, as in `community_stats.py`), since discord.py calls the tree handler after it. `tests/test_hardening.py` finds every command with `default_permissions` on its own and fails if one lacks the runtime check
- **API errors go through `utils/api_errors.api_error_message(resp, action=...)`.** On any non-success response, send what it returns; never write a generic "API error". It shows PlatPursuit's own words for a 4xx (and for its readable 502/503s), whatever shape they come in (`message`, `error`, `detail`, serializer errors), and covers the cases that must not show the server's text: a 500, a 429 (PlatPursuit's throttle, shared by every member, see Important Gotchas), and a 401/403 that is the bot's own key failing. Keep a cog's own wording only where it adds something (a 404 that names the user). Errors you do not catch reach the tree handler, which answers a timeout or dropped connection with a connection message (it cannot tell PlatPursuit's from Discord's) and anything else with "Something went wrong" plus a logged traceback, so do not wrap a whole command in `except Exception`
- **Views with buttons subclass `utils.command_errors.ReplyOnErrorView`**, not `discord.ui.View`. A callback's exception never reaches the tree handler, and discord.py's default `View.on_error` only logs it, so the press just shows "This interaction failed". In an error path after the callback has already answered, edit with `edit_reply` (answering twice raises `InteractionResponded`). `tests/test_api_errors.py` checks every view the commands send

### Cog Structure

Each command lives in its own file in `commands/` as a discord.py Cog:

```python
from discord.ext import commands
from discord import app_commands

class ExampleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="example", description="Does a thing")
    async def example(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        # ... command logic ...

async def setup(bot):
    await bot.add_cog(ExampleCog(bot))
```

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `DISCORD_TOKEN` | Bot authentication token |
| `API_BASE_URL` | PlatPursuit API base URL |
| `API_KEY` | API authentication key |
| `DISCORD_GUILD_ID` | Target Discord server |
| `VERIFIED_ROLE_ID` | Role assigned after PSN verification (removed on unlink) |
| `UNVERIFIED_ROLE_ID` | Onboarding role removed on verification and re-added on unlink. Unset = that half of the swap is skipped |
| `WELCOME_CHANNEL_ID` | Channel for the on-join welcome message |
| `VERIFIED_WELCOME_CHANNEL_ID` | Channel for the post-verification welcome announcement. Unset = no verified welcome is posted |
| `ENABLE_WELCOME_PINGS` | Toggle welcome ping behavior (applies to both welcome messages) |
| `WELCOME_DELAY_SECONDS` | Delay before sending welcome message |
| `ENABLE_UNLINK_ON_LEAVE` | Toggle auto-unlink of PSN profile on member leave |
| `AUDIT_LOG_CHANNEL_ID` | Channel for member join/leave audit log embeds |
| `ENABLE_X_ANNOUNCEMENTS` | Toggle the X (Twitter) RSS announcement poller |
| `X_RSS_FEED_URL` | RSS feed URL for the X profile to monitor (e.g. via RSS.app) |
| `X_ANNOUNCEMENT_CHANNEL_ID` | Channel where new X posts are announced |
| `X_ANNOUNCEMENT_ROLE_ID` | Role pinged in announcements (must be mentionable or use allowed_mentions, which the cog does) |
| `X_POLL_INTERVAL_MINUTES` | How often to poll the RSS feed (default 15) |
| `X_OWN_HANDLE` | The X handle (no `@`) whose own tweets should be announced. Filters out retweets by comparing the tweet link's URL handle. Unset = no filter. |
| `X_TEST_ANNOUNCEMENT_CHANNEL_ID` | Channel in the admin/testing server, used by `POST /admin/x-announce/test` |
| `X_TEST_ANNOUNCEMENT_ROLE_ID` | Role in the admin/testing server, used by `POST /admin/x-announce/test` |
| `ENABLE_TICKETS` | Toggle the moderation ticket system |
| `TICKET_CHANNEL_ID` | Public channel where the "Open a Ticket" panel lives (panel location only; tickets are not created here) |
| `TICKET_CATEGORY_ID` | Mod-only category where ticket channels are created (must grant the bot access) |
| `TICKET_MOD_ROLE_ID` | Mod role pinged in the channel on self-serve ticket opens, allowed to close tickets, and granted category access so staff see all tickets |
| `TICKET_LOG_CHANNEL_ID` | Mod-only channel where ticket transcripts are posted on close |
| `TICKET_ALERT_CHANNEL_ID` | Staff-only channel where an alert embed is posted when a ticket opens and edited to "closed" when it closes (no ping) |
| `DATABASE_URL` | SQLAlchemy connection URL for bot-owned data (mod notes). Accepts `postgres://`/`postgresql://` (converted to asyncpg) or `sqlite+aiosqlite://` (local dev only: `aiosqlite` is in `requirements-dev.txt`, not the production image). Unset = DB features disabled |
| `PORT`, `BOT_API_HOST` | FastAPI server configuration |
| `PROXY_URL` | Outbound proxy for all Discord traffic, `scheme://user:password@host:port` (set in production: it keeps the bot off Render's shared IP, whose Discord rate limits other services would share). `utils/proxy.py` splits it: the address goes to discord.py as `proxy`, the credentials as `proxy_auth`. Never pass the whole URL: aiohttp's proxy errors print it, password included, and discord.py logs them on every reconnect. aiohttp 3.14 deprecates the split route, so its two warnings are muted (`tests/test_proxy.py` guards the split, the mute and the no-leak) |
| Emoji IDs | Various custom emoji references for rich embeds |

---

## Testing

Tests use **pytest** + **pytest-asyncio**, mirroring the ecosystem convention (config in `pyproject.toml` `[tool.pytest.ini_options]`, dev deps in `requirements-dev.txt`, shared fixtures in root `conftest.py`).

```bash
pip install -r requirements-dev.txt   # pulls -r requirements.txt + test deps
pytest                                  # run the suite
```

- **Where**: tests live in `tests/`; shared fixtures (FakeUser, mock-interaction factory, in-memory DB sessionmaker, API-bot, webhook `TestClient` with fresh role queues) are in `conftest.py`.
- **Async**: `asyncio_mode = "auto"`, so `async def test_*` and async fixtures need no per-test decorator.
- **Discord boundary**: Discord interactions are mocked with `AsyncMock` (no live gateway). Cog command bodies are invoked directly via `Cog.command.callback(cog, interaction, ...)`.
- **HTTP boundary**: outbound PlatPursuit API calls are answered by `tests/fake_api.py`: the `api_bot` fixture's `api_session` is a `FakeApiSession`, and `with mock_api(cog.bot) as mocked: mocked.post(url, payload=...)` registers a response (see `tests/test_refresh.py`). An unregistered request raises `aiohttp.ClientConnectionError`, as a real network failure would. This replaced `aioresponses` in 2026-10, which broke on aiohttp 3.14.
- **Webhook API**: `tests/test_api.py` drives the FastAPI app in `bot.py` through conftest's `webhook_client` fixture (`TestClient`, needs `httpx2`, dev-only), which pins `TEST_API_KEY` because importing `bot.py` loads the developer's `.env`, and swaps in fresh role queues (`role_queues`) so nothing queued leaks between tests.
- **Database**: DB tests run against a per-test temp SQLite file through the real `init_db`, so the SQLAlchemy models/queries are exercised for real.
- **Coverage today**: db engine + models, notes cog (DB integration), ticket pure-logic helpers, X-announce filters, formatting, the verification role swap (helper + the /link and /unlink cogs that drive it), the Unverified backfill command, both welcome messages, the API-consuming cogs, the webhook API's auth gate, the role workers' retry rules (`tests/test_role_workers.py`), the API error messages and the error replies for unreachable PlatPursuit and failed buttons (`tests/test_api_errors.py`), and the hardening in `tests/test_hardening.py` (role guard, home-server check, no docs routes, API timeout, mod-command runtime checks and the error handler). **Extending**: API cogs follow the `test_refresh.py` `mock_api` pattern; deeper Discord-interaction flows build on the `conftest.py` mocks. The bar is "no new logic without a committed test."

---

## Dependencies and CI

| What | Where | Notes |
|------|-------|-------|
| Production requirements | `requirements.txt` | Fully pinned, transitive dependencies included, so the audit sees exactly what ships. Python 3.12 (the Docker image). |
| Dev and test tools | `requirements-dev.txt` | `-r requirements.txt` plus pytest, `aiosqlite` (the SQLite driver for local dev and tests; production uses asyncpg), `httpx2` (TestClient) and `pip-audit`. |
| CI | `.github/workflows/check.yml` | On every PR, push to main and weekly on main: **Tests** (pytest), **Dependency audit** (`pip-audit -r requirements.txt --no-deps`, then a pin check: install the pins with `--no-deps` and run `pip check`), **Secret scan** (gitleaks over the full history). |
| Dependabot | `.github/dependabot.yml` | Security PRs as soon as an advisory is published (enable "Dependabot security updates" in the repository settings), plus one grouped pip PR and one grouped GitHub Actions PR with every minor and patch update on the 1st of each month (`tzdata`, whose date versions look like a major every year, rides a second group). Each major update opens its own PR. A reminder issue for the monthly review opens on the 2nd. |

**After any change to `requirements.txt`**, CI's pin check fails if a package is missing a pin or a pin is out of range. To find the right pins, install it into a clean Python 3.12 environment (for example `docker run --rm -v "$PWD:/src" python:3.12-slim pip install -r /src/requirements.txt`) and compare `pip freeze` with the pins: every installed package must be pinned (or the audit cannot see it) and every pin installed. A Dependabot bump of one package can need a new transitive pin; FastAPI 0.143 added `opentelemetry-api`, for one.

**Accepting an advisory** with no usable fix: add `--ignore-vuln <ID>` to the audit step in `check.yml` and a line here with the package, the ID, why it does not apply, and what would make you revisit it. None are accepted today.

### Gotchas

- **Audit on Python 3.12, not the local venv's Python.** Production runs 3.12; resolving on another version can pick different transitive versions.
- **`colorama` is pinned with a Windows-only marker** (`; sys_platform == "win32"`): `click` and `uvicorn` only need it on Windows, so the Linux image skips it and a clean-install comparison lists it as "pinned but not installed". That is expected.
- **A test HTTP mock that builds aiohttp internals breaks on aiohttp upgrades.** That is why `tests/fake_api.py` mocks the session's public surface instead.
- **Some Dependabot PRs cannot pass alone.** Each major is its own PR, but some only work together (pytest 9 and pytest-asyncio 1.x need each other), and some wait on another package (aiohttp 3.14 caps `multidict` below 7). Combine the first kind on one branch; close the second with `@dependabot ignore this major version` and revisit when the blocker moves. A grouped PR can also need a pin it did not add: feedparser 6.0.14 swapped `sgmllib3k` for `feedparser-sgmllib`, which the pin check caught.
- **CI's pip-audit version comes from `requirements-dev.txt`.** Bump it there (Dependabot does), not in `check.yml`.
- **GitHub turns off the weekly scheduled run after 60 days with no commits.** If the repo goes quiet, re-enable it under Actions > Check, or the weekly advisory check silently stops (Dependabot alerts still arrive).
- **Deprecations to plan for:** aiohttp 4 removes `BasicAuth` and `proxy_auth`, which the proxy needs (see `PROXY_URL` above; today their warnings are muted in `utils/proxy.py`). Its replacement, proxy headers, is not something discord.py exposes, so an aiohttp 4 upgrade needs a plan for the proxy first. The warnings left in the test run are other packages' own: on Python 3.13, discord.py 2.7.1's `escape_markdown` (`re.sub`'s `count` passed positionally); on 3.12 (production and CI), the stdlib `audioop` discord.py imports. Two traps around the mute: discord.py's own `@deprecated` APIs call `warnings.simplefilter('default')`, which outranks it, so using one brings aiohttp's warnings back (we use none today); and a `ClientHttpProxyError`'s `repr()` carries the `Proxy-Authorization` header, so log proxy errors with `str()`, never `%r`.

---

## Git Commit Scopes

Scopes for this project: `commands`, `api`, `roles`, `embeds`, `config`, `docker`, `db`, `test`

---

## Important Gotchas

- **Rate limiting**: Discord aggressively rate-limits role operations. Always use the worker queue system in `bot.py` rather than making direct role assignment calls.
- **API dependency**: PlatBot reads all trophy/user data from the PlatPursuit API. If the API is down, those data-dependent commands will fail. Commands answer API errors through `api_error_message` (see Command Patterns). (Bot-owned data like mod notes lives in PlatBot's own database, see below, and is independent of the API.)
- **Every member shares one PlatPursuit throttle.** The bot calls PlatPursuit with one API account, and PlatPursuit's DRF throttle (`VisitorUserRateThrottle`, 60/min per user) counts all of it, so on a busy minute every member's commands get 429s together. That is why the 429 message blames the load, not the user. The fix is on PlatPursuit's side: exempt the bot account or give it its own throttle scope.
- **Own database**: PlatBot uses its own dedicated Postgres (separate from PlatPursuit), managed via SQLAlchemy `create_all`. If `DATABASE_URL` is unset, DB-backed cogs degrade gracefully rather than crashing.
- **Emoji IDs**: Custom Discord emojis are server-specific. Emoji ID environment variables must be updated if the bot moves to a different server or emojis are recreated.
- **The container runs as a non-root user** (`platbot`, uid 10001) and cannot write to `/app`. The bot writes nothing to disk today; a feature that needs to must write to a mounted volume or a temp path, not the app directory.
- **Slash command sync**: After adding/modifying commands, Discord may take up to an hour to propagate slash command changes globally. Use guild-specific sync during development for instant updates.
