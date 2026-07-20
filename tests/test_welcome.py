"""Tests for the join welcome and the verified welcome announcement."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from commands.welcome import FOOTER, WelcomeCog

WELCOME_CHANNEL = 900
VERIFIED_CHANNEL = 901


@pytest.fixture
def welcome_env(monkeypatch):
    """Both channels configured, pings on, no join delay."""
    monkeypatch.setenv('WELCOME_CHANNEL_ID', str(WELCOME_CHANNEL))
    monkeypatch.setenv('VERIFIED_WELCOME_CHANNEL_ID', str(VERIFIED_CHANNEL))
    monkeypatch.setenv('ENABLE_WELCOME_PINGS', 'True')
    monkeypatch.setenv('WELCOME_DELAY_SECONDS', '0')


def make_bot(channels: dict):
    bot = MagicMock(name='Bot')
    bot.plat_pursuit_emoji = ':pp:'
    bot.platinum_emoji = ':plat:'
    bot.gold_emoji = ':gold:'
    bot.silver_emoji = ':silver:'
    bot.get_channel = lambda channel_id: channels.get(channel_id)
    return bot


def make_channel(channel_id: int):
    channel = MagicMock(name=f"Channel:{channel_id}")
    channel.id = channel_id
    channel.send = AsyncMock()
    return channel


def make_member(member_id: int = 42, *, bot: bool = False, display_name: str = 'NewMember'):
    member = MagicMock(name='Member')
    member.id = member_id
    member.bot = bot
    member.display_name = display_name
    return member


def sent(channel):
    args, kwargs = channel.send.call_args
    return kwargs['content'], kwargs['embed']


# --- verified welcome -------------------------------------------------------

async def test_verified_welcome_announces_mention_and_psn(welcome_env):
    channel = make_channel(VERIFIED_CHANNEL)
    cog = WelcomeCog(make_bot({VERIFIED_CHANNEL: channel}))

    await cog.on_psn_verified(make_member(), 'CoolPSN')

    content, embed = sent(channel)
    assert content == 'Welcome <@42>!'
    assert '<@42>' in embed.description
    assert 'CoolPSN' in embed.description
    assert '/refresh' in embed.fields[0].value


async def test_verified_welcome_posts_to_its_own_channel(welcome_env):
    """It must not land in the join-welcome channel."""
    join_channel = make_channel(WELCOME_CHANNEL)
    verified_channel = make_channel(VERIFIED_CHANNEL)
    cog = WelcomeCog(make_bot({WELCOME_CHANNEL: join_channel, VERIFIED_CHANNEL: verified_channel}))

    await cog.on_psn_verified(make_member(), 'CoolPSN')

    verified_channel.send.assert_awaited_once()
    join_channel.send.assert_not_awaited()


async def test_verified_welcome_carries_the_shared_footer_and_title(welcome_env):
    channel = make_channel(VERIFIED_CHANNEL)
    cog = WelcomeCog(make_bot({VERIFIED_CHANNEL: channel}))

    await cog.on_psn_verified(make_member(), 'CoolPSN')

    _, embed = sent(channel)
    assert embed.title.startswith('A New Pursuer Has Arrived!')
    assert embed.footer.text == FOOTER


async def test_verified_welcome_announces_each_member_once(welcome_env):
    """A double-clicked Verify button must not ping the channel twice."""
    channel = make_channel(VERIFIED_CHANNEL)
    cog = WelcomeCog(make_bot({VERIFIED_CHANNEL: channel}))
    member = make_member()

    await cog.on_psn_verified(member, 'CoolPSN')
    await cog.on_psn_verified(member, 'CoolPSN')

    channel.send.assert_awaited_once()


async def test_verified_welcome_escapes_markdown_in_the_psn_name(welcome_env):
    channel = make_channel(VERIFIED_CHANNEL)
    cog = WelcomeCog(make_bot({VERIFIED_CHANNEL: channel}))

    await cog.on_psn_verified(make_member(), '**not_a_mod**')

    _, embed = sent(channel)
    assert r'\*\*not\_a\_mod\*\*' in embed.description
    assert '****' not in embed.description


async def test_verified_welcome_disabled_when_channel_unset(welcome_env, monkeypatch):
    monkeypatch.delenv('VERIFIED_WELCOME_CHANNEL_ID', raising=False)
    channel = make_channel(VERIFIED_CHANNEL)
    cog = WelcomeCog(make_bot({VERIFIED_CHANNEL: channel}))

    await cog.on_psn_verified(make_member(), 'CoolPSN')

    channel.send.assert_not_awaited()


async def test_verified_welcome_skipped_when_channel_missing(welcome_env):
    cog = WelcomeCog(make_bot({}))

    await cog.on_psn_verified(make_member(), 'CoolPSN')  # must not raise


async def test_verified_welcome_respects_ping_toggle(welcome_env, monkeypatch):
    monkeypatch.setenv('ENABLE_WELCOME_PINGS', 'False')
    channel = make_channel(VERIFIED_CHANNEL)
    cog = WelcomeCog(make_bot({VERIFIED_CHANNEL: channel}))

    await cog.on_psn_verified(make_member(display_name='Quiet'), 'CoolPSN')

    content, embed = sent(channel)
    assert content == 'Welcome Quiet!'
    assert '<@42>' not in embed.description


async def test_verified_welcome_swallows_forbidden(welcome_env):
    channel = make_channel(VERIFIED_CHANNEL)
    channel.send.side_effect = discord.Forbidden(MagicMock(status=403), 'nope')
    cog = WelcomeCog(make_bot({VERIFIED_CHANNEL: channel}))

    await cog.on_psn_verified(make_member(), 'CoolPSN')  # logged, not raised


# --- join welcome (regression cover for the shared helpers) -----------------

async def test_join_welcome_still_posts_to_the_welcome_channel(welcome_env):
    channel = make_channel(WELCOME_CHANNEL)
    cog = WelcomeCog(make_bot({WELCOME_CHANNEL: channel}))

    await cog.on_member_join(make_member())

    content, embed = sent(channel)
    assert content == 'Welcome <@42>!'
    assert 'Getting Started' == embed.fields[0].name


async def test_join_welcome_ignores_bots(welcome_env):
    channel = make_channel(WELCOME_CHANNEL)
    cog = WelcomeCog(make_bot({WELCOME_CHANNEL: channel}))

    await cog.on_member_join(make_member(bot=True))

    channel.send.assert_not_awaited()


async def test_join_welcome_disabled_when_channel_unset(welcome_env, monkeypatch):
    monkeypatch.delenv('WELCOME_CHANNEL_ID', raising=False)
    channel = make_channel(WELCOME_CHANNEL)
    cog = WelcomeCog(make_bot({WELCOME_CHANNEL: channel}))

    await cog.on_member_join(make_member())

    channel.send.assert_not_awaited()


async def test_blank_channel_env_vars_do_not_crash_startup(monkeypatch):
    """A commented-out value is the natural way to disable a channel."""
    monkeypatch.setenv('WELCOME_CHANNEL_ID', '')
    monkeypatch.setenv('VERIFIED_WELCOME_CHANNEL_ID', '')

    cog = WelcomeCog(make_bot({}))

    assert cog.welcome_channel_id == 0
    assert cog.verified_welcome_channel_id == 0


async def test_join_welcome_waits_the_configured_delay(welcome_env, monkeypatch):
    monkeypatch.setenv('WELCOME_DELAY_SECONDS', '8')
    slept = []
    monkeypatch.setattr('commands.welcome.asyncio.sleep', AsyncMock(side_effect=slept.append))
    channel = make_channel(WELCOME_CHANNEL)
    cog = WelcomeCog(make_bot({WELCOME_CHANNEL: channel}))

    await cog.on_member_join(make_member())

    assert slept == [8]
    channel.send.assert_awaited_once()
