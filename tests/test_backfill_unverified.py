"""Tests for the one-time /backfill_unverified transition command."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from commands.backfill_unverified import BackfillUnverifiedCog, select_backfill_targets

VERIFIED_ID = 111
UNVERIFIED_ID = 222


def make_role(role_id: int, name: str, position: int = 5):
    role = MagicMock(name=f"Role:{name}")
    role.id = role_id
    role.name = name
    role.position = position
    role.mention = f"<@&{role_id}>"
    return role


@pytest.fixture
def roles():
    return make_role(VERIFIED_ID, 'Verified'), make_role(UNVERIFIED_ID, 'Unverified')


def make_member(member_id: int, *, roles: list, bot: bool = False):
    member = MagicMock(name=f"Member:{member_id}")
    member.id = member_id
    member.bot = bot
    member.roles = roles
    return member


def make_bot(*, verified_role_id=VERIFIED_ID, unverified_role_id=UNVERIFIED_ID):
    bot = MagicMock(name='Bot')
    bot.verified_role_id = verified_role_id
    bot.unverified_role_id = unverified_role_id
    bot.role_queue = asyncio.Queue()
    return bot


def attach_guild(interaction, members, guild_roles, *, chunked=True,
                 manage_roles=True, bot_top_position=100):
    guild = MagicMock(name='Guild')
    guild.id = 1000
    guild.members = members
    guild.chunked = chunked
    guild.chunk = AsyncMock()
    guild.get_role = lambda role_id: guild_roles.get(role_id)
    guild.me.guild_permissions.manage_roles = manage_roles
    guild.me.top_role.position = bot_top_position
    interaction.guild = guild
    return guild


def _sent(interaction) -> str:
    args, kwargs = interaction.followup.send.call_args
    return args[0] if args else kwargs.get('content', '')


# --- target selection -------------------------------------------------------

def test_selects_only_unroled_humans(roles):
    verified, unverified = roles
    linked = make_member(1, roles=[verified])
    already = make_member(2, roles=[unverified])
    target = make_member(3, roles=[])
    a_bot = make_member(4, roles=[], bot=True)

    assert select_backfill_targets([linked, already, target, a_bot], verified, unverified) == [target]


def test_selection_is_empty_when_everyone_is_covered(roles):
    verified, unverified = roles
    members = [make_member(1, roles=[verified]), make_member(2, roles=[unverified])]

    assert select_backfill_targets(members, verified, unverified) == []


def test_member_with_both_roles_is_not_a_target(roles):
    """Odd state, but re-adding Unverified would be a wasted role op."""
    verified, unverified = roles
    both = make_member(1, roles=[verified, unverified])

    assert select_backfill_targets([both], verified, unverified) == []


# --- command behavior -------------------------------------------------------

async def open_preview(cog, interaction):
    """Run the command and return its confirm/cancel callbacks."""
    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)
    view = interaction.followup.send.call_args.kwargs['view']
    return view.children[0].callback, view.children[1].callback


def press(user_id: int):
    button = MagicMock(name='ButtonInteraction')
    button.user = MagicMock(id=user_id)
    button.response.edit_message = AsyncMock()
    button.response.send_message = AsyncMock()
    return button


async def test_preview_does_not_queue_anything(roles, interaction_factory, user_factory):
    verified, unverified = roles
    bot = make_bot()
    cog = BackfillUnverifiedCog(bot)
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[]), make_member(2, roles=[verified])],
                 {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    await open_preview(cog, interaction)

    assert bot.role_queue.empty()
    assert '**1** member(s)' in interaction.followup.send.call_args.kwargs['embed'].description


async def test_confirm_queues_one_job_per_target(roles, interaction_factory, user_factory):
    verified, unverified = roles
    bot = make_bot()
    cog = BackfillUnverifiedCog(bot)
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[]), make_member(2, roles=[])],
                 {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    confirm, _ = await open_preview(cog, interaction)
    button = press(7)
    await confirm(button)

    queued = [bot.role_queue.get_nowait() for _ in range(bot.role_queue.qsize())]
    assert queued == [
        {'user_id': 1, 'role_id': UNVERIFIED_ID, 'guild_id': 1000},
        {'user_id': 2, 'role_id': UNVERIFIED_ID, 'guild_id': 1000},
    ]
    assert '2 member(s)' in button.response.edit_message.call_args.kwargs['content']


async def test_cancel_queues_nothing(roles, interaction_factory, user_factory):
    verified, unverified = roles
    bot = make_bot()
    cog = BackfillUnverifiedCog(bot)
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])], {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    _, cancel = await open_preview(cog, interaction)
    await cancel(press(7))

    assert bot.role_queue.empty()


async def test_another_moderator_cannot_confirm(roles, interaction_factory, user_factory):
    """The preview is ephemeral, but don't rely on that to scope the confirm."""
    verified, unverified = roles
    bot = make_bot()
    cog = BackfillUnverifiedCog(bot)
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])], {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    confirm, _ = await open_preview(cog, interaction)
    button = press(99)
    await confirm(button)

    assert bot.role_queue.empty()
    assert 'Only the moderator' in button.response.send_message.call_args[0][0]


async def test_unchunked_guild_is_chunked_before_scanning(roles, interaction_factory, user_factory):
    verified, unverified = roles
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    guild = attach_guild(interaction, [make_member(1, roles=[])],
                         {VERIFIED_ID: verified, UNVERIFIED_ID: unverified}, chunked=False)

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    guild.chunk.assert_awaited_once()


async def test_reports_nothing_to_do_when_no_targets(roles, interaction_factory, user_factory):
    verified, unverified = roles
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[verified])], {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    assert 'Nothing to do' in _sent(interaction)


async def test_unconfigured_role_id_aborts(roles, interaction_factory, user_factory):
    verified, unverified = roles
    cog = BackfillUnverifiedCog(make_bot(unverified_role_id=0))
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])], {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    assert 'must be configured' in _sent(interaction)


async def test_missing_role_in_guild_aborts(roles, interaction_factory, user_factory):
    verified, _ = roles
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])], {VERIFIED_ID: verified})

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    assert 'Unverified' in _sent(interaction)


async def test_both_roles_missing_are_named_together(interaction_factory, user_factory):
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [], {})

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    assert 'Verified and Unverified' in _sent(interaction)


async def test_preview_is_ephemeral(roles, interaction_factory, user_factory):
    """The confirm scoping assumes only the invoking mod can see the buttons."""
    verified, unverified = roles
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])], {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    assert interaction.followup.send.call_args.kwargs['ephemeral'] is True


async def test_outside_a_guild_aborts(interaction_factory, user_factory):
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    interaction.guild = None

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    assert 'only be used in a server' in _sent(interaction)


async def test_another_moderator_cannot_cancel(roles, interaction_factory, user_factory):
    verified, unverified = roles
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])], {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    _, cancel = await open_preview(cog, interaction)
    button = press(99)
    await cancel(button)

    button.response.edit_message.assert_not_awaited()
    assert 'Only the moderator' in button.response.send_message.call_args[0][0]


async def test_missing_manage_roles_aborts(roles, interaction_factory, user_factory):
    verified, unverified = roles
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])],
                 {VERIFIED_ID: verified, UNVERIFIED_ID: unverified}, manage_roles=False)

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    assert 'Manage Roles' in _sent(interaction)


async def test_role_above_the_bot_aborts(roles, interaction_factory, user_factory):
    """Otherwise every queued job burns five retries on a 403."""
    verified, unverified = roles
    unverified.position = 200
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])],
                 {VERIFIED_ID: verified, UNVERIFIED_ID: unverified}, bot_top_position=100)

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    assert "above the bot's highest role" in _sent(interaction)


async def test_chunk_failure_is_reported(roles, interaction_factory, user_factory):
    verified, unverified = roles
    cog = BackfillUnverifiedCog(make_bot())
    interaction = interaction_factory(user=user_factory(7))
    guild = attach_guild(interaction, [make_member(1, roles=[])],
                         {VERIFIED_ID: verified, UNVERIFIED_ID: unverified}, chunked=False)
    guild.chunk.side_effect = RuntimeError('gateway hiccup')

    await BackfillUnverifiedCog.backfill_unverified.callback(cog, interaction)

    assert 'Could not load the full member list' in _sent(interaction)


async def test_second_run_is_refused_while_a_batch_is_pending(roles, interaction_factory, user_factory):
    """Queued members still look roleless in cache, so a re-run would double-enqueue."""
    verified, unverified = roles
    bot = make_bot()
    cog = BackfillUnverifiedCog(bot)
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])], {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    confirm, _ = await open_preview(cog, interaction)
    await confirm(press(7))
    assert bot.role_queue.qsize() == 1

    second = interaction_factory(user=user_factory(7))
    attach_guild(second, [make_member(1, roles=[])], {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})
    await BackfillUnverifiedCog.backfill_unverified.callback(cog, second)

    assert 'already running' in _sent(second)
    assert bot.role_queue.qsize() == 1


async def test_guard_clears_once_the_queue_drains(roles, interaction_factory, user_factory):
    verified, unverified = roles
    bot = make_bot()
    cog = BackfillUnverifiedCog(bot)
    interaction = interaction_factory(user=user_factory(7))
    attach_guild(interaction, [make_member(1, roles=[])], {VERIFIED_ID: verified, UNVERIFIED_ID: unverified})

    confirm, _ = await open_preview(cog, interaction)
    await confirm(press(7))
    assert cog.in_progress

    bot.role_queue.get_nowait()
    bot.role_queue.task_done()
    await asyncio.sleep(0)  # let the watcher task observe the drained queue

    assert not cog.in_progress
