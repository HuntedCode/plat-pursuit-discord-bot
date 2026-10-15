"""The 2026-10 hardening: what the role API may touch, mod-command checks at run time, timeouts.

The bot is public and its webhook API is reachable by anyone who has the URL, so these pin the
limits that hold even if the API key leaks or a server admin loosens a command's permissions.
"""
import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

import bot
from commands.backfill_unverified import BackfillUnverifiedCog
from commands.notes import NotesCog
from commands.recheck_badges_user import RecheckBadgesUserCog
from commands.refresh_user import RefreshUserCog
from commands.sync_roles_user import SyncRolesUserCog
from utils.command_errors import NO_PERMISSION_MESSAGE, handle_app_command_error
from utils.role_guard import PRIVILEGED_PERMISSIONS, refusal_reason

HOME_GUILD = 111


def fake_role(*, default=False, managed=False, **perms):
    role = MagicMock(spec=discord.Role)
    role.name = 'Some Role'
    role.is_default.return_value = default
    role.managed = managed
    role.permissions = discord.Permissions(**perms)
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


@pytest.mark.parametrize('worker, queue_name, action', [
    (bot.role_assignment_worker, 'role_queue', 'add_roles'),
    (bot.role_removal_worker, 'role_removal_queue', 'remove_roles'),
])
async def test_the_workers_never_touch_a_powerful_role(monkeypatch, worker, queue_name, action):
    # A fresh queue: the module's own was bound to the TestClient's event loop by the API tests,
    # and an asyncio.Queue only works on the loop that first used it.
    queue = asyncio.Queue()
    monkeypatch.setattr(bot, queue_name, queue)
    role = fake_role(administrator=True)
    member = MagicMock(spec=discord.Member)
    member.roles = [role]
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    guild.get_role.return_value = role
    monkeypatch.setattr(bot.bot, 'get_guild', lambda guild_id: guild)
    monkeypatch.setattr(bot, 'GUILD_ID', HOME_GUILD)

    task = asyncio.create_task(worker())
    try:
        await queue.put({'user_id': 1, 'role_id': 2})
        await asyncio.wait_for(queue.join(), timeout=2)
    finally:
        task.cancel()
    getattr(member, action).assert_not_awaited()


# --- the webhook API --------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(bot, 'API_KEY', 'test-api-key-not-for-production')
    monkeypatch.setattr(bot, 'GUILD_ID', HOME_GUILD)
    return TestClient(bot.app, raise_server_exceptions=False)


AUTH = {'Authorization': 'Bearer test-api-key-not-for-production'}


@pytest.mark.parametrize('path', ['/docs', '/redoc', '/openapi.json'])
def test_the_api_publishes_no_docs_or_schema(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize('path', ['/assign-role', '/remove-role'])
def test_another_server_is_refused(client, path):
    resp = client.post(path, json={'user_id': 1, 'role_id': 2, 'guild_id': 999}, headers=AUTH)
    assert resp.status_code == 403


@pytest.mark.parametrize('body', [{'user_id': 1, 'role_id': 2}, {'user_id': 1, 'role_id': 2, 'guild_id': HOME_GUILD}])
def test_the_home_server_is_accepted(client, body):
    resp = client.post('/assign-role', json=body, headers=AUTH)
    assert resp.status_code == 200
    while not bot.role_queue.empty():
        bot.role_queue.get_nowait()


async def test_api_calls_have_a_timeout():
    session = bot.make_api_session({})
    try:
        assert session.timeout.total == bot.API_TIMEOUT_SECONDS
    finally:
        await session.close()


# --- mod commands check permissions when they run ---------------------------------------------

MOD_COMMANDS = [
    (BackfillUnverifiedCog.backfill_unverified, 'manage_roles'),
    (RecheckBadgesUserCog.recheck_badges_user, 'manage_messages'),
    (RefreshUserCog.refresh_user, 'manage_messages'),
    (SyncRolesUserCog.sync_roles_user, 'manage_messages'),
    (NotesCog.add, 'manage_messages'),
    (NotesCog.view, 'manage_messages'),
]


async def _run_checks(command, permissions):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.permissions = permissions
    for check in command.checks:
        result = check(interaction)
        if inspect.isawaitable(result):
            await result


@pytest.mark.parametrize('command, permission', MOD_COMMANDS, ids=lambda c: getattr(c, 'qualified_name', c))
async def test_a_mod_command_refuses_a_member_without_the_permission(command, permission):
    """default_permissions only hides the command, and a server admin can widen it."""
    assert command.checks, f'/{command.qualified_name} has no runtime check'
    with pytest.raises(app_commands.MissingPermissions):
        await _run_checks(command, discord.Permissions.none())
    await _run_checks(command, discord.Permissions(**{permission: True}))


def _interaction(done):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = done
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.command = None
    return interaction


@pytest.mark.parametrize('done', [False, True])
async def test_a_failed_check_gets_a_private_reply(done):
    interaction = _interaction(done)
    await handle_app_command_error(interaction, app_commands.MissingPermissions(['manage_messages']))
    sender = interaction.followup.send if done else interaction.response.send_message
    sender.assert_awaited_once_with(NO_PERMISSION_MESSAGE, ephemeral=True)


async def test_other_errors_are_logged_not_answered(caplog):
    interaction = _interaction(False)
    await handle_app_command_error(interaction, app_commands.AppCommandError('boom'))
    interaction.response.send_message.assert_not_awaited()
    assert 'Unhandled error' in caplog.text
