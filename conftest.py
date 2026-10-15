"""Root pytest configuration and shared fixtures.

PlatBot has no packaging, so the project root must be importable for `import db`,
`import commands.*`, `import utils.*` to resolve in tests. Having this conftest at the
root puts the root on sys.path (pytest prepend import mode); the explicit insert below
makes that guarantee obvious. Outbound HTTP (the PlatPursuit API) is mocked at the
session boundary with tests/fake_api.py; Discord interactions are mocked with AsyncMock.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import pytest_asyncio

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeUser:
    """Minimal stand-in for a discord.Member/User in unit tests."""

    def __init__(self, id: int, name: str = 'tester'):
        self.id = id
        self.name = name
        self.display_name = name
        self.mention = f"<@{id}>"
        self.bot = False

    def __str__(self):
        return f"{self.name}#0001"


def _make_interaction(*, guild_id: int = 1000, user: FakeUser | None = None):
    """Build a mock discord.Interaction with async response/followup methods."""
    interaction = MagicMock(name='Interaction')
    interaction.guild_id = guild_id
    interaction.user = user if user is not None else FakeUser(7, 'modA')
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.fixture
def user_factory():
    """Factory for FakeUser: user_factory(42, 'baduser')."""
    return FakeUser


@pytest.fixture
def interaction_factory():
    """Factory for a mock interaction: interaction_factory(guild_id=..., user=...)."""
    return _make_interaction


@pytest_asyncio.fixture
async def db_sessionmaker(tmp_path):
    """A fresh SQLite-backed async sessionmaker per test, via the real init_db path."""
    from db.engine import init_db

    url = f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine, sessionmaker = await init_db(url)
    yield sessionmaker
    await engine.dispose()


@pytest_asyncio.fixture
async def notes_bot(db_sessionmaker):
    """A minimal bot object exposing db_sessionmaker for the notes cog."""
    bot = MagicMock(name='Bot')
    bot.db_sessionmaker = db_sessionmaker
    return bot


def fake_channel(name: str, role_overwrite) -> MagicMock:
    """A text channel whose overwrite for any role is `role_overwrite`."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = name
    channel.overwrites_for.return_value = role_overwrite
    return channel


def fake_role(*, default: bool = False, managed: bool = False, channels=(), **perms) -> MagicMock:
    """A role with the given permission flags, in a guild holding `channels` (role guard, workers)."""
    role = MagicMock(spec=discord.Role)
    role.name = 'Some Role'
    role.is_default.return_value = default
    role.managed = managed
    role.permissions = discord.Permissions(**perms)
    role.guild = MagicMock(spec=discord.Guild)
    role.guild.channels = list(channels)
    return role


#: The API key the webhook tests pin, because importing bot.py loads the developer's .env.
TEST_API_KEY = 'test-api-key-not-for-production'


@pytest.fixture
def role_queues(monkeypatch):
    """Fresh role queues, so nothing a test queues (or leaves unfinished) reaches another test."""
    import bot

    fresh = {'/assign-role': asyncio.Queue(), '/remove-role': asyncio.Queue()}
    monkeypatch.setattr(bot, 'role_queue', fresh['/assign-role'])
    monkeypatch.setattr(bot, 'role_removal_queue', fresh['/remove-role'])
    return fresh


@pytest.fixture
def webhook_client(monkeypatch, role_queues):
    """A TestClient for the FastAPI app in bot.py, with TEST_API_KEY as the key."""
    import bot
    from fastapi.testclient import TestClient

    monkeypatch.setattr(bot, 'API_KEY', TEST_API_KEY)
    # raise_server_exceptions=False: a server error must come back as a 500, as it would live.
    return TestClient(bot.app, raise_server_exceptions=False)


@pytest_asyncio.fixture
async def api_bot():
    """A minimal bot with a fake API session (register responses with tests.fake_api.mock_api)."""
    from tests.fake_api import FakeApiSession

    bot = MagicMock(name='Bot')
    bot.api_base_url = 'https://api.test/'
    bot.api_session = FakeApiSession()
    yield bot
