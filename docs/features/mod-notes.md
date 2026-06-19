# Moderation Notes

Bot-side documentation for the per-user moderator notes system. Lives in [commands/notes.py](../../commands/notes.py), backed by the [db/](../../db/) package.

## What it is

A mod-only, per-user notes system: staff can record notes against a Discord user and read the full history back. Notes are **append-only** and carry **who wrote them and when**, so the log doubles as a tamper-evident audit trail. This is the first feature backed by **PlatBot's own database** rather than the PlatPursuit API.

Commands (mod-only, gated by `manage_messages`, guild-only):

| Command | Purpose |
|---|---|
| `/note add user:<member> note:<text>` | Record a note against a user |
| `/note view user:<member>` | View a user's notes, newest first, each showing author + timestamp |

Both responses are ephemeral (visible only to the requesting mod).

## Why a database (and the standalone goal)

The notes need real persistence (survive restarts), per-user keying, and durable author/timestamp metadata. Discord-as-datastore (the trick the ticket system uses) doesn't fit well for queryable per-user records, so this introduces **PlatBot's first database** — also a step toward PlatBot being able to stand on its own rather than depending entirely on PlatPursuit.

### Storage model

- **ORM**: SQLAlchemy 2.0 async. The engine is created at startup from `DATABASE_URL` and exposed as `bot.db_sessionmaker` (mirroring `bot.api_session`).
- **Engine-agnostic**: the same code runs on Postgres (`postgresql://` / `postgres://`, auto-converted to the asyncpg driver) or SQLite (`sqlite+aiosqlite://`) for local dev. `init_db` also handles `sslmode` in the URL (translated to an asyncpg connect arg) so Render/Heroku-style URLs work.
- **Shared DB, isolated table**: currently points at PlatPursuit's Postgres. PlatBot's table is `platbot_mod_notes`, created and managed only by PlatBot's SQLAlchemy metadata (`create_all` with `checkfirst`), so it never collides with or alters Django's tables.

### Schema (`platbot_mod_notes`)

| Column | Notes |
|---|---|
| `id` | PK |
| `guild_id` | BigInt |
| `target_user_id` | BigInt; the user the note is about |
| `author_id` | BigInt; the mod who wrote it |
| `author_name` | denormalized (`str(user)`) so blame survives if the mod later leaves |
| `content` | the note text (capped at 1000 chars on input) |
| `created_at` | timestamptz, DB `server_default=now()` |

Composite index on `(guild_id, target_user_id)` for fast per-user lookup.

## Behavior

- **Add**: validates non-empty and <= 1000 chars, inserts one row, confirms ephemerally.
- **View**: queries the user's notes for the current guild, newest first. Notes are rendered into one embed description, accumulated up to a ~3800-char budget (under Discord's 4096 limit). If the user has more notes than fit, the footer reports "Showing X of Y (most recent)". (Full pagination is a future enhancement; in practice most users have few notes.)
- **Graceful degradation**: if `DATABASE_URL` is unset (so `bot.db_sessionmaker` is `None`), both commands reply that notes are unavailable rather than erroring.

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy connection URL. `postgres://`/`postgresql://` (converted to asyncpg) or `sqlite+aiosqlite://`. | (none → notes disabled) |

No bot-specific Discord permissions are needed beyond the ability to respond to commands; access is gated by the `manage_messages` default permission on the `/note` group.

## Gotchas and Pitfalls

- **Shared Postgres, PlatBot-owned table.** PlatBot only manages `platbot_`-prefixed tables. Never register these in PlatPursuit's Django models or point Django migrations at them, and never have PlatBot write to Django's tables.
- **Append-only by design.** There is intentionally no edit/delete in v1 so the author/timestamp trail stays trustworthy. If a remove is added later, log the removal so it stays "in the open."
- **`DATABASE_URL` driver/scheme.** `init_db` converts `postgres://`/`postgresql://` to `postgresql+asyncpg://` and strips libpq-only query params (`sslmode`, `channel_binding`) that asyncpg rejects, translating `sslmode` into an SSL connect arg. If you hit SSL errors, check whether you're using the internal vs external Postgres host.
- **View is bounded by an embed-size budget**, not a hard note cap: very long histories show only the most recent that fit (the footer says so). Raise `VIEW_CHAR_BUDGET` / add pagination if needed.
- **Schema changes**: applied via `create_all` today (it only adds missing tables/columns it knows about — it does NOT alter existing columns). Introduce Alembic before making non-additive schema changes.
