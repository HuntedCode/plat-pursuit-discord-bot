"""Cog-level tests for /link's verify button: role swap wiring and sync-roles ordering.

Like /unlink, the verify handler is a closure, so these tests run the command and
then invoke the button's callback off the View it sent.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aioresponses import aioresponses

from commands.link import LinkCog
from utils.roles import RoleSwapError

VERIFIED_ID = 111
UNVERIFIED_ID = 222


@pytest.fixture
def link_bot(api_bot):
    api_bot.verified_role_id = VERIFIED_ID
    api_bot.unverified_role_id = UNVERIFIED_ID
    return api_bot


async def open_verify(cog, interaction):
    """Run /link and return its 'Verify Now' callback."""
    with aioresponses() as mocked:
        mocked.post('https://api.test/generate-code/', payload={'code': 'ABC123'})
        await LinkCog.link.callback(cog, interaction, 'coolpsn')
    view = interaction.followup.send.call_args.kwargs['view']
    return view.children[0].callback


def button_interaction(user):
    interaction = MagicMock(name='ButtonInteraction')
    interaction.user = user
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.original_response = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return interaction


def _sent(interaction) -> str:
    args, kwargs = interaction.followup.send.call_args
    return args[0] if args else kwargs.get('content', '')


async def test_verify_success_swaps_roles_and_syncs(link_bot, user_factory, interaction_factory, monkeypatch):
    swap = AsyncMock()
    sync = AsyncMock()
    monkeypatch.setattr('commands.link.apply_verification_roles', swap)
    monkeypatch.setattr(LinkCog, '_sync_roles_fire_and_forget', sync)
    cog = LinkCog(link_bot)
    press = button_interaction(user_factory(7))

    verify = await open_verify(cog, interaction_factory(user=user_factory(7)))
    with aioresponses() as mocked:
        mocked.post('https://api.test/verify/', payload={'success': True, 'psn_username': 'CoolPSN'})
        await verify(press)
        await asyncio.sleep(0)  # let the fire-and-forget sync task start

    assert 'Success!' in _sent(press)
    assert swap.await_args.kwargs['verified'] is True
    assert swap.await_args.kwargs['unverified_role_id'] == UNVERIFIED_ID
    sync.assert_awaited_once()
    # Drives the verified welcome post; the API's canonical PSN name wins.
    link_bot.dispatch.assert_called_once_with('psn_verified', press.user, 'CoolPSN')


async def test_role_swap_failure_still_syncs_and_clears_the_button(link_bot, user_factory, interaction_factory, monkeypatch):
    """A misconfigured role must not cancel the achievement-role sync."""
    sync = AsyncMock()
    monkeypatch.setattr(
        'commands.link.apply_verification_roles',
        AsyncMock(side_effect=RoleSwapError('the Unverified role was not found in this server')),
    )
    monkeypatch.setattr(LinkCog, '_sync_roles_fire_and_forget', sync)
    cog = LinkCog(link_bot)
    press = button_interaction(user_factory(7))

    verify = await open_verify(cog, interaction_factory(user=user_factory(7)))
    with aioresponses() as mocked:
        mocked.post('https://api.test/verify/', payload={'success': True})
        await verify(press)
        await asyncio.sleep(0)

    assert 'Verification succeeded but the Unverified role was not found' in _sent(press)
    sync.assert_awaited_once()
    (await press.original_response()).edit.assert_awaited_with(view=None)
    # No public welcome for someone whose roles did not actually apply.
    link_bot.dispatch.assert_not_called()


async def test_dispatch_falls_back_to_the_submitted_psn_name(link_bot, user_factory, interaction_factory, monkeypatch):
    """Older API responses omit psn_username; the announcement still needs a name."""
    monkeypatch.setattr('commands.link.apply_verification_roles', AsyncMock())
    monkeypatch.setattr(LinkCog, '_sync_roles_fire_and_forget', AsyncMock())
    cog = LinkCog(link_bot)
    press = button_interaction(user_factory(7))

    verify = await open_verify(cog, interaction_factory(user=user_factory(7)))
    with aioresponses() as mocked:
        mocked.post('https://api.test/verify/', payload={'success': True})
        await verify(press)
        await asyncio.sleep(0)

    link_bot.dispatch.assert_called_once_with('psn_verified', press.user, 'coolpsn')


async def test_failed_verification_does_not_touch_roles(link_bot, user_factory, interaction_factory, monkeypatch):
    swap = AsyncMock()
    monkeypatch.setattr('commands.link.apply_verification_roles', swap)
    cog = LinkCog(link_bot)
    press = button_interaction(user_factory(7))

    verify = await open_verify(cog, interaction_factory(user=user_factory(7)))
    with aioresponses() as mocked:
        mocked.post('https://api.test/verify/', payload={'success': False, 'message': 'Code not found'})
        await verify(press)

    swap.assert_not_awaited()
    link_bot.dispatch.assert_not_called()
    assert 'Code not found' in _sent(press)
