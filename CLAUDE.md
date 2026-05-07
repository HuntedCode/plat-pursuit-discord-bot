# PlatBot — Project CLAUDE.md

> This file contains project-specific standards. See `~/.claude/CLAUDE.md` for universal collaboration, workflow, and quality standards that apply across all projects.

## Project Overview

**PlatBot** is the Discord bot companion to PlatPursuit. It bridges the PlatPursuit web platform with Discord communities, enabling PSN account linking, trophy data display, role synchronization based on achievements, and community engagement features.

- **Relationship to PlatPursuit**: PlatBot is a consumer of PlatPursuit's API. It does not have its own database. All persistent data lives in PlatPursuit's backend. Changes to PlatPursuit's API endpoints, data models, or authentication may require corresponding updates here.
- **Deployment**: Docker containerized (Python 3.12, multi-stage build)

---

## Tech Stack

| Layer             | Technology                          |
| ----------------- | ----------------------------------- |
| Language          | Python 3.12                         |
| Bot Framework     | discord.py 2.6.x                    |
| Web Server        | FastAPI + Uvicorn (webhook receiver) |
| HTTP Client       | aiohttp (async API calls)           |
| Validation        | Pydantic                            |
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

### Worker Queue System

Role assignments and removals use async worker queues with retry logic and rate-limit handling. This prevents Discord API rate limit issues when processing bulk role changes (e.g., after a badge evaluation sweep).

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
│   ├── community_stats.py  # /trophystats today|yesterday|records: community-wide trophy aggregates
│   ├── link_help.py        # !link: public instructions for PSN account linking
│   ├── welcome.py          # Event listener: posts a welcome embed when a member joins
│   ├── member_events.py    # Event listener: auto-unlinks PSN profile on member leave (toggle: ENABLE_UNLINK_ON_LEAVE)
│   ├── audit_log.py        # Event listener: member join/leave logging to audit channel
│   └── x_announcements.py  # Background task: polls an X RSS feed and announces new posts to a channel (toggle: ENABLE_X_ANNOUNCEMENTS)
├── utils/                  # Shared utilities
│   └── formatting.py       # Number/string formatters shared across cogs
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
| `VERIFIED_ROLE_ID` | Role assigned after PSN verification |
| `WELCOME_CHANNEL_ID` | Channel for welcome messages |
| `ENABLE_WELCOME_PINGS` | Toggle welcome ping behavior |
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
| `PORT`, `BOT_API_HOST` | FastAPI server configuration |
| `PROXY_URL` | Optional outbound proxy |
| Emoji IDs | Various custom emoji references for rich embeds |

---

## Git Commit Scopes

Scopes for this project: `commands`, `api`, `roles`, `embeds`, `config`, `docker`

---

## Important Gotchas

- **Rate limiting**: Discord aggressively rate-limits role operations. Always use the worker queue system in `bot.py` rather than making direct role assignment calls.
- **API dependency**: PlatBot has no local database. If the PlatPursuit API is down, all data-dependent commands will fail. Commands should handle API errors gracefully with user-friendly messages.
- **Emoji IDs**: Custom Discord emojis are server-specific. Emoji ID environment variables must be updated if the bot moves to a different server or emojis are recreated.
- **Slash command sync**: After adding/modifying commands, Discord may take up to an hour to propagate slash command changes globally. Use guild-specific sync during development for instant updates.
