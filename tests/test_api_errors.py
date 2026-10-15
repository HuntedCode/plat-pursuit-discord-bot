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
from tests.test_link import button_interaction, link_bot, open_verify  # noqa: F401  (link_bot is a fixture)
from utils.api_errors import (
    BOT_ACCESS_MESSAGE, MAX_MESSAGE_LENGTH, RATE_LIMITED_MESSAGE, REQUEST_FAILED_MESSAGE,
    UNAVAILABLE_MESSAGE, api_error_message, message_from_body,
)
from utils.command_errors import FAILED_MESSAGE, ReplyOnErrorView, handle_app_command_error


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


async def test_a_rate_limit_page_is_explained():
    """django-ratelimit answers with the site's HTML 429 page, not JSON."""
    resp = RawResponse(429, '<!doctype html><h1>Too many requests</h1>')
    assert await api_error_message(resp, action='test') == RATE_LIMITED_MESSAGE


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


async def test_verify_explains_a_private_profile_and_keeps_the_button(link_bot, user_factory, interaction_factory):  # noqa: F811
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


async def test_verify_shows_a_serializer_error_instead_of_api_error(link_bot, user_factory, interaction_factory):  # noqa: F811
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
async def test_a_command_that_cannot_reach_platpursuit_says_so(caplog, cause):
    """A timeout (API_TIMEOUT_SECONDS) or a dropped connection is not a bug: no traceback, and
    the user is told PlatPursuit is having trouble rather than "Something went wrong"."""
    interaction = _interaction(done=True)
    error = app_commands.CommandInvokeError(MagicMock(qualified_name='summary'), cause)

    await handle_app_command_error(interaction, error)

    interaction.followup.send.assert_awaited_once_with(UNAVAILABLE_MESSAGE, ephemeral=True)
    assert 'Unhandled error' not in caplog.text
    assert 'Could not reach PlatPursuit' in caplog.text


async def test_a_real_bug_still_gets_the_general_reply_and_a_traceback(caplog):
    interaction = _interaction(done=True)
    error = app_commands.CommandInvokeError(MagicMock(qualified_name='summary'), KeyError('profile'))

    await handle_app_command_error(interaction, error)

    interaction.followup.send.assert_awaited_once_with(FAILED_MESSAGE, ephemeral=True)
    record = next(r for r in caplog.records if 'Unhandled error' in r.getMessage())
    assert record.exc_info is not None


@pytest.mark.parametrize('cause, expected', [
    (asyncio.TimeoutError(), UNAVAILABLE_MESSAGE),
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


def test_the_link_and_unlink_buttons_use_the_replying_view():
    import inspect

    from commands import link, trophy_case, unlink

    for module in (link, unlink, trophy_case):
        source = inspect.getsource(module)
        assert 'ReplyOnErrorView(' in source and 'discord.ui.View(' not in source, module.__name__
