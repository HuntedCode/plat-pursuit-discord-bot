"""Root pytest configuration and shared fixtures.

PlatBot has no packaging, so the project root must be importable for `import db`,
`import commands.*`, `import utils.*` to resolve in tests. Having this conftest at the
root puts the root on sys.path (pytest prepend import mode); the explicit insert below
makes that guarantee obvious. Outbound HTTP (the PlatPursuit API) is mocked at the
aiohttp boundary with aioresponses; Discord interactions are mocked with AsyncMock.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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


@pytest_asyncio.fixture
async def api_bot():
    """A minimal bot with a real aiohttp session (intercepted by aioresponses) + api_base_url."""
    import aiohttp

    bot = MagicMock(name='Bot')
    bot.api_base_url = 'https://api.test/'
    session = aiohttp.ClientSession()
    bot.api_session = session
    yield bot
    await session.close()
