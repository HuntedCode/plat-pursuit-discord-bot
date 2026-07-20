"""Cog-level tests for /unlink, covering the confirm button's role-swap wiring.

The confirm handler is a closure created inside the command, so these tests run the
command first and then invoke the button's callback off the View it sent.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aioresponses import aioresponses

from commands.unlink import UnlinkCog
from utils.roles import RoleSwapError

VERIFIED_ID = 111
UNVERIFIED_ID = 222


@pytest.fixture
def unlink_bot(api_bot):
    api_bot.verified_role_id = VERIFIED_ID
    api_bot.unverified_role_id = UNVERIFIED_ID
    return api_bot


async def open_confirm(cog, interaction) -> AsyncMock:
    """Run /unlink for a linked user and return its 'Yes, Unlink' callback."""
    with aioresponses() as mocked:
        mocked.get(
            'https://api.test/check-linked/?discord_id=7',
            payload={'linked': True, 'psn_username': 'CoolPSN'},
        )
        await UnlinkCog.unlink.callback(cog, interaction)
    view = interaction.followup.send.call_args.kwargs['view']
    return view.children[0].callback


def button_interaction(user):
    interaction = MagicMock(name='ButtonInteraction')
    interaction.user = user
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


def _result(interaction) -> str:
    return interaction.edit_original_response.call_args.kwargs['content']


async def test_confirm_defers_then_reports_success(unlink_bot, user_factory, interaction_factory, monkeypatch):
    swap = AsyncMock()
    monkeypatch.setattr('commands.unlink.apply_verification_roles', swap)
    cog = UnlinkCog(unlink_bot)
    confirm = await open_confirm(cog, interaction_factory(user=user_factory(7)))
    press = button_interaction(user_factory(7))

    with aioresponses() as mocked:
        mocked.post('https://api.test/unlink/', payload={'success': True})
        await confirm(press)

    press.response.defer.assert_awaited_once()
    assert _result(press) == 'Unlinked successfully!'
    assert swap.await_args.kwargs['verified'] is False
    assert swap.await_args.kwargs['unverified_role_id'] == UNVERIFIED_ID


async def test_confirm_surfaces_role_swap_failure(unlink_bot, user_factory, interaction_factory, monkeypatch):
    monkeypatch.setattr(
        'commands.unlink.apply_verification_roles',
        AsyncMock(side_effect=RoleSwapError('the Unverified role was not found in this server')),
    )
    cog = UnlinkCog(unlink_bot)
    confirm = await open_confirm(cog, interaction_factory(user=user_factory(7)))
    press = button_interaction(user_factory(7))

    with aioresponses() as mocked:
        mocked.post('https://api.test/unlink/', payload={'success': True})
        await confirm(press)

    assert 'Unlink succeeded but the Unverified role was not found' in _result(press)


async def test_confirm_skips_role_swap_when_api_fails(unlink_bot, user_factory, interaction_factory, monkeypatch):
    swap = AsyncMock()
    monkeypatch.setattr('commands.unlink.apply_verification_roles', swap)
    cog = UnlinkCog(unlink_bot)
    confirm = await open_confirm(cog, interaction_factory(user=user_factory(7)))
    press = button_interaction(user_factory(7))

    with aioresponses() as mocked:
        mocked.post('https://api.test/unlink/', status=500)
        await confirm(press)

    swap.assert_not_awaited()
    assert 'Unlink failed' in _result(press)
