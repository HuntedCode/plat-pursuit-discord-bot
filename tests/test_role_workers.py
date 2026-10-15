"""bot.apply_role_change: the one worker behind /assign-role and /remove-role.

The two workers used to be near copies, and both carried the same two bugs: a success on the
last attempt still logged "Max retries exceeded ... Dropping request", and every Discord error
that was not a 429 was retried five times back to back, including a 403 that can never succeed.
"""
import logging
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

import bot
from conftest import fake_role

HOME_GUILD = 111


def http_error(status, retry_after='0'):
    response = MagicMock(status=status, reason='test', headers={'Retry-After': retry_after})
    return discord.HTTPException(response, f'HTTP {status}')


@pytest.fixture
def waits(monkeypatch):
    """Every asyncio.sleep the worker asks for, without actually waiting."""
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(bot.asyncio, 'sleep', fake_sleep)
    return recorded


@pytest.fixture
def server(monkeypatch):
    """A home guild holding one plain role and one member who already has it."""
    role = fake_role()
    member = MagicMock(spec=discord.Member)
    member.name = 'member'
    member.roles = [role]
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    guild.get_role.return_value = role
    monkeypatch.setattr(bot.bot, 'get_guild', lambda guild_id: guild if guild_id == HOME_GUILD else None)
    monkeypatch.setattr(bot, 'GUILD_ID', HOME_GUILD)
    return member, role


REQUEST = {'user_id': 1, 'role_id': 2}


@pytest.mark.parametrize('adding, action', [(True, 'add_roles'), (False, 'remove_roles')])
async def test_a_change_succeeds_first_time(server, waits, caplog, adding, action):
    member, role = server
    await bot.apply_role_change(REQUEST, adding)
    getattr(member, action).assert_awaited_once_with(role)
    assert waits == []
    assert 'Gave up' not in caplog.text


async def test_success_on_the_last_attempt_is_not_reported_as_dropped(server, waits, caplog):
    """The old loop counted attempts in a `finally`, so success on attempt five still read as
    "Max retries exceeded ... Dropping request"."""
    caplog.set_level(logging.INFO)
    member, _ = server
    member.add_roles.side_effect = [http_error(429)] * (bot.ROLE_MAX_ATTEMPTS - 1) + [None]

    await bot.apply_role_change(REQUEST, adding=True)

    assert member.add_roles.await_count == bot.ROLE_MAX_ATTEMPTS
    assert 'Gave up' not in caplog.text
    assert 'Assigned role' in caplog.text


async def test_a_rate_limit_waits_for_retry_after(server, waits):
    member, _ = server
    member.add_roles.side_effect = [http_error(429, retry_after='2.5'), None]

    await bot.apply_role_change(REQUEST, adding=True)

    assert waits == [3.0], 'Retry-After plus half a second'
    assert member.add_roles.await_count == 2


@pytest.mark.parametrize('status', [403, 404, 400])
async def test_a_client_error_is_dropped_without_retrying(server, waits, caplog, status):
    """A role above the bot's own, or a missing permission, cannot succeed on retry."""
    member, _ = server
    member.add_roles.side_effect = http_error(status)

    await bot.apply_role_change(REQUEST, adding=True)

    assert member.add_roles.await_count == 1
    assert waits == []
    assert f'Discord {status}, not retrying' in caplog.text


async def test_a_server_error_is_retried_with_a_growing_wait(server, waits):
    member, _ = server
    member.add_roles.side_effect = [http_error(503), http_error(502), None]

    await bot.apply_role_change(REQUEST, adding=True)

    assert member.add_roles.await_count == 3
    assert waits == [1, 2]


async def test_persistent_server_errors_give_up_after_the_last_attempt(server, waits, caplog):
    member, _ = server
    member.add_roles.side_effect = http_error(503)

    await bot.apply_role_change(REQUEST, adding=True)

    assert member.add_roles.await_count == bot.ROLE_MAX_ATTEMPTS
    assert waits == [1, 2, 4, 8], 'no wait after the last attempt'
    assert 'Gave up on role assign' in caplog.text


async def test_a_network_error_is_retried_like_a_server_error(server, waits):
    member, _ = server
    member.add_roles.side_effect = [OSError('connection reset'), None]

    await bot.apply_role_change(REQUEST, adding=True)

    assert member.add_roles.await_count == 2
    assert waits == [1]


async def test_removing_a_role_the_member_lacks_is_skipped(server, waits, caplog):
    caplog.set_level(logging.INFO)
    member, _ = server
    member.roles = []

    await bot.apply_role_change(REQUEST, adding=False)

    member.remove_roles.assert_not_awaited()
    assert 'skipping removal' in caplog.text


async def test_a_member_who_left_is_dropped(server, waits, caplog):
    guild = bot.bot.get_guild(HOME_GUILD)
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404, reason='x'), 'gone'))

    await bot.apply_role_change(REQUEST, adding=True)

    assert 'Member not found' in caplog.text
    assert waits == []
