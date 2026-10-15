"""The 2026-10 hardening: what the role API may touch, mod-command checks at run time, timeouts.

The bot is public and its webhook API is reachable by anyone who has the URL, so these pin the
limits that hold even if the API key leaks or a server admin loosens a command's permissions.
"""
import asyncio
import importlib
import inspect
import pkgutil
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands
from discord.ext import commands as ext_commands

import bot
import commands
from commands.community_stats import CommunityStatsCog
from commands.recheck_badges_user import RECHECK_TIMEOUT
from utils.command_errors import (
    COOLDOWN_MESSAGE, FAILED_MESSAGE, NO_PERMISSION_MESSAGE, handle_app_command_error,
)
from utils.role_guard import PRIVILEGED_PERMISSIONS, refusal_reason

HOME_GUILD = 111


def fake_channel(name, role_overwrite):
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = name
    channel.overwrites_for.return_value = role_overwrite
    return channel


def fake_role(*, default=False, managed=False, channels=(), **perms):
    role = MagicMock(spec=discord.Role)
    role.name = 'Some Role'
    role.is_default.return_value = default
    role.managed = managed
    role.permissions = discord.Permissions(**perms)
    role.guild = MagicMock(spec=discord.Guild)
    role.guild.channels = list(channels)
    return role


# --- which roles the API may touch ------------------------------------------------------------

def test_a_plain_role_may_be_toggled():
    assert refusal_reason(fake_role(send_messages=True, embed_links=True)) is None


@pytest.mark.parametrize('permission', PRIVILEGED_PERMISSIONS)
def test_a_role_with_power_is_refused(permission):
    reason = refusal_reason(fake_role(**{permission: True}))
    assert reason and permission in reason


@pytest.mark.parametrize('alias', ['manage_emojis', 'manage_emojis_and_stickers', 'manage_permissions'])
def test_permission_aliases_are_caught_too(alias):
    assert refusal_reason(fake_role(**{alias: True})) is not None


def test_everyone_and_integration_roles_are_refused():
    assert refusal_reason(fake_role(default=True)) == 'it is @everyone'
    assert 'integration' in refusal_reason(fake_role(managed=True))


def test_power_granted_in_one_channel_is_refused():
    staff_only = fake_channel('mod-log', discord.PermissionOverwrite(manage_messages=True))
    reason = refusal_reason(fake_role(channels=[fake_channel('general', discord.PermissionOverwrite()), staff_only]))
    assert reason == 'it grants manage_messages in #mod-log'


def test_a_role_may_unlock_a_channel_or_be_denied_power_in_one():
    """Premium roles open private channels; a deny is not a grant."""
    channels = [
        fake_channel('premium', discord.PermissionOverwrite(view_channel=True, send_messages=True)),
        fake_channel('announcements', discord.PermissionOverwrite(manage_messages=False)),
    ]
    assert refusal_reason(fake_role(channels=channels)) is None


def _worker_harness(monkeypatch, queue_name, role):
    # A fresh queue per test, so nothing queued here (or left unfinished) leaks into other tests.
    queue = asyncio.Queue()
    monkeypatch.setattr(bot, queue_name, queue)
    member = MagicMock(spec=discord.Member)
    member.name = 'member'
    member.roles = [role]
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    guild.fetch_member = AsyncMock(return_value=member)
    guild.get_role.return_value = role
    monkeypatch.setattr(bot.bot, 'get_guild', lambda guild_id: guild)
    monkeypatch.setattr(bot, 'GUILD_ID', HOME_GUILD)
    return queue, guild, member


async def _process_one(worker, queue):
    task = asyncio.create_task(worker())
    try:
        await queue.put({'user_id': 1, 'role_id': 2})
        await asyncio.wait_for(queue.join(), timeout=2)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


WORKERS = [
    pytest.param(bot.role_assignment_worker, 'role_queue', 'add_roles', id='assign'),
    pytest.param(bot.role_removal_worker, 'role_removal_queue', 'remove_roles', id='remove'),
]


@pytest.mark.parametrize('worker, queue_name, action', WORKERS)
async def test_the_workers_never_touch_a_powerful_role(monkeypatch, caplog, worker, queue_name, action):
    queue, guild, member = _worker_harness(monkeypatch, queue_name, fake_role(administrator=True))
    await _process_one(worker, queue)
    getattr(member, action).assert_not_awaited()
    assert 'Refused' in caplog.text
    # Refused before the member lookup, so a flood of bad requests costs no Discord API calls.
    guild.get_member.assert_not_called()
    guild.fetch_member.assert_not_awaited()


@pytest.mark.parametrize('worker, queue_name, action', WORKERS)
async def test_the_workers_still_toggle_a_plain_role(monkeypatch, worker, queue_name, action):
    role = fake_role()
    queue, _, member = _worker_harness(monkeypatch, queue_name, role)
    await _process_one(worker, queue)
    getattr(member, action).assert_awaited_once_with(role)


# --- the webhook API --------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(bot, 'API_KEY', 'test-api-key-not-for-production')
    monkeypatch.setattr(bot, 'GUILD_ID', HOME_GUILD)
    return TestClient(bot.app, raise_server_exceptions=False)


@pytest.fixture
def queues(monkeypatch):
    fresh = {'/assign-role': asyncio.Queue(), '/remove-role': asyncio.Queue()}
    monkeypatch.setattr(bot, 'role_queue', fresh['/assign-role'])
    monkeypatch.setattr(bot, 'role_removal_queue', fresh['/remove-role'])
    return fresh


AUTH = {'Authorization': 'Bearer test-api-key-not-for-production'}
ROLE_ROUTES = ['/assign-role', '/remove-role']


@pytest.mark.parametrize('path', ['/docs', '/redoc', '/openapi.json'])
def test_the_api_publishes_no_docs_or_schema(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize('path', ROLE_ROUTES)
def test_another_server_is_refused(client, queues, path):
    resp = client.post(path, json={'user_id': 1, 'role_id': 2, 'guild_id': 999}, headers=AUTH)
    assert resp.status_code == 403
    assert queues[path].empty()


@pytest.mark.parametrize('path', ROLE_ROUTES)
@pytest.mark.parametrize('body', [{'user_id': 1, 'role_id': 2}, {'user_id': 1, 'role_id': 2, 'guild_id': HOME_GUILD}])
def test_the_home_server_is_accepted(client, queues, path, body):
    resp = client.post(path, json=body, headers=AUTH)
    assert resp.status_code == 200
    assert queues[path].qsize() == 1


async def test_api_calls_have_a_timeout():
    session = bot.make_api_session({})
    try:
        assert session.timeout.total == bot.API_TIMEOUT_SECONDS
    finally:
        await session.close()


def test_a_full_badge_recheck_gets_longer_than_the_default():
    assert RECHECK_TIMEOUT.total > bot.API_TIMEOUT_SECONDS


# --- mod commands check permissions when they run ---------------------------------------------

def _cog_classes():
    for info in pkgutil.iter_modules(commands.__path__):
        module = importlib.import_module(f'commands.{info.name}')
        for obj in vars(module).values():
            if inspect.isclass(obj) and issubclass(obj, ext_commands.Cog) and obj.__module__ == module.__name__:
                yield obj


def _restricted_commands():
    """Every slash command hidden behind default_permissions (its own or its group's)."""
    found = []
    for cog in _cog_classes():
        for top in cog.__cog_app_commands__:
            leaves = top.walk_commands() if isinstance(top, app_commands.Group) else [top]
            for command in leaves:
                if not isinstance(command, app_commands.Command):
                    continue
                root = command.root_parent
                required = command.default_permissions or (root.default_permissions if root else None)
                if required is not None and required.value:
                    found.append(pytest.param(command, required, id=command.qualified_name))
    return found


RESTRICTED = _restricted_commands()


def test_discovery_finds_the_known_mod_commands():
    names = {param.id for param in RESTRICTED}
    assert {
        'backfill_unverified', 'recheck_badges_user', 'refresh_user', 'sync_roles_user',
        'note add', 'note view', 'ticket_user', 'ticket_panel',
    } <= names


async def _run_checks(command, permissions):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.permissions = permissions
    for check in command.checks:
        result = check(interaction)
        if inspect.isawaitable(result):
            await result


@pytest.mark.parametrize('command, required', RESTRICTED)
async def test_a_mod_command_refuses_a_member_without_the_permission(command, required):
    """default_permissions only hides the command, and a server admin can widen it."""
    assert command.checks, f'/{command.qualified_name} has no runtime check'
    with pytest.raises(app_commands.MissingPermissions):
        await _run_checks(command, discord.Permissions.none())
    await _run_checks(command, required)


def _interaction(done, command=None):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = done
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.command = command
    return interaction


def _sender(interaction, done):
    return interaction.followup.send if done else interaction.response.send_message


@pytest.mark.parametrize('done', [False, True])
async def test_a_failed_check_gets_a_private_reply(done):
    interaction = _interaction(done)
    await handle_app_command_error(interaction, app_commands.MissingPermissions(['manage_messages']))
    _sender(interaction, done).assert_awaited_once_with(NO_PERMISSION_MESSAGE, ephemeral=True)


async def test_a_cooldown_says_so_rather_than_no_permission():
    interaction = _interaction(False)
    cooldown = app_commands.Cooldown(1, 30)
    await handle_app_command_error(interaction, app_commands.CommandOnCooldown(cooldown, 4.2))
    interaction.response.send_message.assert_awaited_once_with(COOLDOWN_MESSAGE.format(seconds=5), ephemeral=True)


@pytest.mark.parametrize('done', [False, True])
async def test_other_errors_are_logged_and_answered(caplog, done):
    """A command that raises would otherwise leave its deferred reply on "thinking..." forever."""
    interaction = _interaction(done)
    await handle_app_command_error(interaction, app_commands.AppCommandError('boom'))
    _sender(interaction, done).assert_awaited_once_with(FAILED_MESSAGE, ephemeral=True)
    assert 'Unhandled error' in caplog.text


async def test_a_command_with_its_own_handler_is_left_alone():
    """/trophystats answers its own cooldowns; a second reply would contradict it."""
    cog = CommunityStatsCog(MagicMock())
    command = next(c for c in cog.walk_app_commands() if isinstance(c, app_commands.Command))
    interaction = _interaction(True, command=command)
    await handle_app_command_error(interaction, app_commands.CommandOnCooldown(app_commands.Cooldown(1, 30), 4.2))
    interaction.followup.send.assert_not_awaited()
    interaction.response.send_message.assert_not_awaited()


async def test_an_expired_interaction_does_not_raise(caplog):
    interaction = _interaction(False)
    interaction.response.send_message.side_effect = discord.NotFound(MagicMock(status=404), 'Unknown interaction')
    await handle_app_command_error(interaction, app_commands.MissingPermissions(['manage_messages']))
    assert 'Could not send the error reply' in caplog.text
