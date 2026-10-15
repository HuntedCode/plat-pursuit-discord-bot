"""What users are told when a PlatPursuit API call fails (utils/api_errors.py), and the error
replies for commands that cannot reach PlatPursuit at all and for buttons (utils/command_errors.py).

Before this, every cog fell back to "API error. Please try again later." for any status it did not
expect, throwing away PlatPursuit's own message ("Discord ID already linked to a PSN profile.").
"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest
from discord import app_commands

from commands.community_stats import CommunityStatsCog
from commands.refresh import RefreshCog
from commands.trophy_case import TrophyCaseCog
from commands.unlink import UnlinkCog
from tests.fake_api import mock_api
from conftest import button_interaction, open_verify
from utils.api_errors import (
    BOT_ACCESS_MESSAGE, MAX_MESSAGE_LENGTH, RATE_LIMITED_MESSAGE, REQUEST_FAILED_MESSAGE,
    UNAVAILABLE_MESSAGE, api_error_message, message_from_body,
)
from utils.command_errors import FAILED_MESSAGE, NETWORK_MESSAGE, ReplyOnErrorView, edit_reply, handle_app_command_error


class RawResponse:
    """A response with a raw body, for the bodies FakeResponse cannot produce (HTML, junk)."""

    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def text(self):
        return self._body


def response(status, payload):
    return RawResponse(status, json.dumps(payload))


# ── reading the message out of a body ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('body, expected', [
    ({'message': 'This PSN profile has its privacy settings enabled.'}, 'This PSN profile has its privacy settings enabled.'),
    ({'error': 'No linked profile found.'}, 'No linked profile found.'),
    ({'non_field_errors': ['Discord ID already linked to a PSN profile.']}, 'Discord ID already linked to a PSN profile.'),
    ({'psn_username': ['PSN username must be 3-16 characters.']}, 'PSN username must be 3-16 characters.'),
    ({'detail': 'Not found.'}, 'Not found.'),
    (['A bare list of errors.'], 'A bare list of errors.'),
    ({'success': False}, None),
    ({'message': '   '}, None),
    (None, None),
    ('not a dict', None),
])
def test_every_shape_platpursuit_answers_in(body, expected):
    assert message_from_body(body) == expected


def test_message_and_error_win_over_detail():
    assert message_from_body({'detail': 'x', 'error': 'The real reason.'}) == 'The real reason.'


def test_a_long_message_is_cut():
    text = message_from_body({'error': 'x' * 1000})
    assert len(text) == MAX_MESSAGE_LENGTH and text.endswith('...')


# ── the rules for what reaches the user ────────────────────────────────────────────────────────

@pytest.mark.parametrize('resp, expected', [
    (response(400, {'non_field_errors': ['Discord ID already linked to a PSN profile.']}), 'Discord ID already linked to a PSN profile.'),
    (response(404, {'error': 'No linked profile found.'}), 'No linked profile found.'),
    (response(409, {'success': False, 'message': 'Discord already linked to this profile.'}), 'Discord already linked to this profile.'),
    (response(403, {'success': False, 'message': 'This PSN profile has its privacy settings enabled.'}), 'This PSN profile has its privacy settings enabled.'),
    (response(408, {'success': False, 'message': 'Sync timed out. Try again later.'}), 'Sync timed out. Try again later.'),
    (response(503, {'error': 'PSN is currently unavailable. Try again later.'}), 'PSN is currently unavailable. Try again later.'),
    (response(502, {'success': False, 'message': 'Sync failed. Please try again later.'}), 'Sync failed. Please try again later.'),
])
async def test_platpursuits_own_words_reach_the_user(resp, expected):
    assert await api_error_message(resp, action='test') == expected


async def test_a_500_never_shows_its_text():
    """PlatPursuit's 500s all say "Internal error.", which tells a user nothing."""
    assert await api_error_message(response(500, {'error': 'Internal error.'}), action='test') == UNAVAILABLE_MESSAGE


async def test_a_throttled_request_blames_the_load_not_the_user():
    """The bot's 429s come from PlatPursuit's DRF throttle, which counts every request the bot
    makes for every member, so the person who ran the command is not the one who overdid it."""
    resp = response(429, {'detail': 'Request was throttled. Expected available in 12 seconds.'})
    assert await api_error_message(resp, action='test') == RATE_LIMITED_MESSAGE
    assert 'too often' not in RATE_LIMITED_MESSAGE


@pytest.mark.parametrize('status, body', [
    (401, {'detail': 'Invalid token.'}),
    (403, {'detail': 'You do not have permission to perform this action.'}),
])
async def test_an_auth_failure_is_the_bots_problem_not_the_users(caplog, status, body):
    assert await api_error_message(response(status, body), action='summary') == BOT_ACCESS_MESSAGE
    assert any(r.levelno == logging.ERROR and 'refused the bot' in r.getMessage() for r in caplog.records)


async def test_a_proxy_error_page_gets_the_general_message():
    assert await api_error_message(RawResponse(502, '<html>Bad gateway</html>'), action='test') == UNAVAILABLE_MESSAGE


async def test_a_client_error_with_no_message_gets_a_fallback():
    assert await api_error_message(RawResponse(400, ''), action='test') == REQUEST_FAILED_MESSAGE


async def test_every_failure_is_logged_with_its_status_and_body(caplog):
    await api_error_message(response(400, {'error': 'Bad thing.'}), action='refresh')
    assert "PlatPursuit API 400 on refresh" in caplog.text and 'Bad thing.' in caplog.text


# ── the cogs use it ────────────────────────────────────────────────────────────────────────────

def _sent(interaction) -> str:
    args, kwargs = interaction.followup.send.call_args
    return args[0] if args else kwargs.get('content', '')


async def test_refresh_shows_platpursuits_reason(api_bot, user_factory, interaction_factory):
    cog = RefreshCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/refresh/', payload={'error': 'discord_id required.'}, status=400)
        await RefreshCog.refresh.callback(cog, interaction)
    assert _sent(interaction) == 'discord_id required.'


async def test_link_shows_why_a_code_could_not_be_made(api_bot, user_factory, interaction_factory):
    from commands.link import LinkCog

    cog = LinkCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/generate-code/', status=400,
                    payload={'non_field_errors': ['This Discord account is already linked to a PSN account.']})
        await LinkCog.link.callback(cog, interaction, 'coolpsn')
    assert _sent(interaction) == 'This Discord account is already linked to a PSN account.'


async def test_verify_explains_a_private_profile_and_keeps_the_button(link_bot, user_factory, interaction_factory):
    """The case moderators see most: a private PSN profile. The user is told why, and can fix the
    setting and press Verify again without running /link afresh."""
    from commands.link import LinkCog

    cog = LinkCog(link_bot)
    press = button_interaction(user_factory(7))
    verify = await open_verify(cog, interaction_factory(user=user_factory(7)))
    privacy = 'This PSN profile has its privacy settings enabled. Please set your gaming history to public and try again.'
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/verify/', status=403, payload={'success': False, 'message': privacy})
        await verify(press)

    assert _sent(press) == privacy
    press.original_response.assert_not_awaited()


async def test_verify_shows_a_serializer_error_instead_of_api_error(link_bot, user_factory, interaction_factory):
    from commands.link import LinkCog

    cog = LinkCog(link_bot)
    press = button_interaction(user_factory(7))
    verify = await open_verify(cog, interaction_factory(user=user_factory(7)))
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/verify/', status=400,
                    payload={'non_field_errors': ['Discord ID already linked to a PSN profile.']})
        await verify(press)

    assert _sent(press) == 'Discord ID already linked to a PSN profile.'


async def test_trophy_case_explains_an_error_instead_of_crashing(api_bot, user_factory, interaction_factory):
    """fetch_page used to return None on an error, and the command then called None.get()."""
    cog = TrophyCaseCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.get('https://api.test/trophy-case/?discord_id=7&page=1&per_page=4', status=500, payload={'error': 'Internal error.'})
        await TrophyCaseCog.trophy_case.callback(cog, interaction)
    assert _sent(interaction) == UNAVAILABLE_MESSAGE


async def test_unlink_check_errors_stay_private(api_bot, user_factory, interaction_factory):
    """The check-linked error used to be posted publicly in the channel."""
    cog = UnlinkCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.get('https://api.test/check-linked/?discord_id=7', status=500, payload={'error': 'Internal error.'})
        await UnlinkCog.unlink.callback(cog, interaction)
    assert interaction.followup.send.call_args.kwargs.get('ephemeral') is True
    assert _sent(interaction) == UNAVAILABLE_MESSAGE


async def test_community_stats_says_when_platpursuit_is_unreachable(api_bot):
    cog = CommunityStatsCog(api_bot)
    with mock_api(cog.bot):  # nothing registered: the fake raises a connection error
        data, error = await cog._get_json('community-stats/today/')
    assert data is None and error == UNAVAILABLE_MESSAGE


# ── commands and buttons that cannot reach PlatPursuit ─────────────────────────────────────────

def _interaction(done):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(return_value=done)
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.command = None
    return interaction


@pytest.mark.parametrize('cause', [asyncio.TimeoutError(), aiohttp.ClientConnectionError('refused')])
async def test_a_network_error_gets_a_connection_reply_not_a_traceback(caplog, cause):
    """A timeout (API_TIMEOUT_SECONDS) or a dropped connection is not a bug: no traceback, and a
    reply about the connection rather than "Something went wrong". It does not name PlatPursuit:
    the same errors come from Discord's own HTTP calls."""
    interaction = _interaction(done=True)
    error = app_commands.CommandInvokeError(MagicMock(qualified_name='summary'), cause)

    await handle_app_command_error(interaction, error)

    interaction.followup.send.assert_awaited_once_with(NETWORK_MESSAGE, ephemeral=True)
    assert 'Unhandled error' not in caplog.text
    assert 'Network error' in caplog.text


async def test_a_real_bug_still_gets_the_general_reply_and_a_traceback(caplog):
    interaction = _interaction(done=True)
    error = app_commands.CommandInvokeError(MagicMock(qualified_name='summary'), KeyError('profile'))

    await handle_app_command_error(interaction, error)

    interaction.followup.send.assert_awaited_once_with(FAILED_MESSAGE, ephemeral=True)
    record = next(r for r in caplog.records if 'Unhandled error' in r.getMessage())
    assert record.exc_info is not None


@pytest.mark.parametrize('cause, expected', [
    (asyncio.TimeoutError(), NETWORK_MESSAGE),
    (RuntimeError('a bug'), FAILED_MESSAGE),
])
async def test_a_button_that_fails_gets_a_private_reply(cause, expected):
    """Button callbacks never reach the tree's handler; discord.py's default View.on_error only
    logs, which left the person who pressed it on "thinking..." with no answer."""
    view = ReplyOnErrorView()
    button = discord.ui.Button(label='Verify Now')
    interaction = _interaction(done=True)

    await view.on_error(interaction, cause, button)

    interaction.followup.send.assert_awaited_once_with(expected, ephemeral=True)


@pytest.mark.parametrize('status, keeps_button', [
    (403, True), (408, True), (429, True), (502, True), (400, False), (404, False), (409, False),
])
async def test_verify_keeps_its_button_only_when_pressing_again_can_help(link_bot, user_factory, interaction_factory, status, keeps_button):
    from commands.link import LinkCog

    cog = LinkCog(link_bot)
    press = button_interaction(user_factory(7))
    verify = await open_verify(cog, interaction_factory(user=user_factory(7)))
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/verify/', status=status, payload={'success': False, 'message': 'Reason.'})
        await verify(press)

    assert (press.original_response.await_count == 0) is keeps_button


# ── every button replies, and publish errors never answer twice ────────────────────────────────

async def test_the_views_the_commands_send_reply_on_errors(api_bot, link_bot, user_factory, interaction_factory):
    from commands.link import LinkCog

    interaction = interaction_factory(user=user_factory(7))
    await open_verify(LinkCog(link_bot), interaction)
    assert isinstance(interaction.followup.send.call_args.kwargs['view'], ReplyOnErrorView), '/link'

    cog = UnlinkCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.get('https://api.test/check-linked/?discord_id=7', payload={'linked': True, 'psn_username': 'coolpsn'})
        await UnlinkCog.unlink.callback(cog, interaction)
    assert isinstance(interaction.followup.send.call_args.kwargs['view'], ReplyOnErrorView), '/unlink'

    cog = TrophyCaseCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.get('https://api.test/trophy-case/?discord_id=7&page=1&per_page=4', payload=TROPHY_PAGE)
        await TrophyCaseCog.trophy_case.callback(cog, interaction)
    assert isinstance(interaction.followup.send.call_args.kwargs['view'], ReplyOnErrorView), '/trophy_case'

    stats_view = CommunityStatsCog(api_bot)._build_publish_view(7, discord.Embed(title='x'))
    assert isinstance(stats_view, ReplyOnErrorView), '/trophystats'


def test_the_ticket_and_backfill_views_reply_on_errors():
    import inspect

    from commands import backfill_unverified, summary, tickets

    for cls in (tickets.TicketPanelView, tickets.TicketControlView, tickets.ConfirmCloseView):
        assert issubclass(cls, ReplyOnErrorView), cls.__name__
    for module in (backfill_unverified, summary):  # their views are built inside a command body
        assert 'ReplyOnErrorView(' in inspect.getsource(module), module.__name__


@pytest.mark.parametrize('answered', [False, True])
async def test_edit_reply_never_answers_twice(answered):
    """A publish that edited the message and then failed to post used to call
    response.edit_message again, which Discord rejects (InteractionResponded)."""
    interaction = MagicMock()
    interaction.response.is_done = MagicMock(return_value=answered)
    interaction.response.edit_message = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    await edit_reply(interaction, content='Publish failed.', view=None)

    if answered:
        interaction.edit_original_response.assert_awaited_once_with(content='Publish failed.', view=None)
        interaction.response.edit_message.assert_not_awaited()
    else:
        interaction.response.edit_message.assert_awaited_once_with(content='Publish failed.', view=None)


# ── the cogs not covered above ─────────────────────────────────────────────────────────────────

TROPHY_PAGE = {'linked': True, 'platinums': [], 'current_page': 1, 'total_pages': 2, 'total_plats': 0}


async def test_sync_roles_shows_platpursuits_reason(api_bot, user_factory, interaction_factory):
    from commands.sync_roles import SyncRolesCog

    cog = SyncRolesCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/sync-roles/', status=400, payload={'error': 'Profile is not Discord verified.'})
        await SyncRolesCog.sync_roles.callback(cog, interaction)
    assert _sent(interaction) == 'Profile is not Discord verified.'


async def test_a_failed_badge_recheck_tells_the_mod_where_to_look(api_bot, user_factory, interaction_factory):
    from commands.recheck_badges_user import RecheckBadgesUserCog

    cog = RecheckBadgesUserCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    target = MagicMock(id=42, display_name='Hunter')
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/recheck-badges/', status=500, payload={'error': 'Badge recheck failed.'})
        await RecheckBadgesUserCog.recheck_badges_user.callback(cog, interaction, target)
    assert _sent(interaction) == "Badge recheck failed for Hunter. Check PlatPursuit's logs for the cause."


async def test_a_failed_unlink_says_why(api_bot, user_factory, interaction_factory):
    cog = UnlinkCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.get('https://api.test/check-linked/?discord_id=7', payload={'linked': True, 'psn_username': 'coolpsn'})
        await UnlinkCog.unlink.callback(cog, interaction)
    yes = interaction.followup.send.call_args.kwargs['view'].children[0].callback
    press = MagicMock()
    press.response.defer = AsyncMock()
    press.edit_original_response = AsyncMock()
    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/unlink/', status=400, payload={'error': 'discord_id required.'})
        await yes(press)
    assert press.edit_original_response.call_args.kwargs['content'] == 'Unlink failed: discord_id required.'


async def test_a_trophy_case_page_error_is_explained(api_bot, user_factory, interaction_factory):
    cog = TrophyCaseCog(api_bot)
    interaction = interaction_factory(user=user_factory(7))
    with mock_api(cog.bot) as mocked:
        mocked.get('https://api.test/trophy-case/?discord_id=7&page=1&per_page=4', payload=TROPHY_PAGE)
        await TrophyCaseCog.trophy_case.callback(cog, interaction)
    next_page = interaction.followup.send.call_args.kwargs['view'].children[1].callback
    press = MagicMock()
    press.user = user_factory(7)
    press.response.edit_message = AsyncMock()
    with mock_api(cog.bot) as mocked:
        mocked.get('https://api.test/trophy-case/?discord_id=7&page=2&per_page=4', status=500, payload={'error': 'Internal error.'})
        await next_page(press)
    assert press.response.edit_message.call_args.kwargs['content'] == UNAVAILABLE_MESSAGE
