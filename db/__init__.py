"""PlatBot's own persistence layer (SQLAlchemy async).

PlatBot historically had no database and read everything from PlatPursuit's API. This
package adds a small, self-contained DB layer for bot-owned data (e.g. moderation notes)
so the bot can stand on its own. The engine is configured via DATABASE_URL and is
engine-agnostic (Postgres in production, SQLite for local dev).
"""
