"""Tests for the Verified/Unverified role swap shared by /link and /unlink."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from utils.roles import RoleSwapError, apply_verification_roles

VERIFIED_ID = 111
UNVERIFIED_ID = 222


def make_role(role_id: int, name: str):
    role = MagicMock(name=f"Role:{name}")
    role.id = role_id
    role.name = name
    return role


def make_member(*, roles: list, guild_roles: dict):
    """A member in a guild whose get_role resolves the given {id: role} map."""
    guild = MagicMock(name='Guild')
    guild.id = 1000
    guild.get_role = lambda role_id: guild_roles.get(role_id)

    member = MagicMock(name='Member')
    member.id = 42
    member.guild = guild
    member.roles = roles
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


@pytest.fixture
def roles():
    return {
        VERIFIED_ID: make_role(VERIFIED_ID, 'Verified'),
        UNVERIFIED_ID: make_role(UNVERIFIED_ID, 'Unverified'),
    }


async def test_verifying_adds_verified_and_removes_unverified(roles):
    member = make_member(roles=[roles[UNVERIFIED_ID]], guild_roles=roles)

    await apply_verification_roles(
        member, verified=True, verified_role_id=VERIFIED_ID,
        unverified_role_id=UNVERIFIED_ID, reason='test',
    )

    member.add_roles.assert_awaited_once_with(roles[VERIFIED_ID], reason='test')
    member.remove_roles.assert_awaited_once_with(roles[UNVERIFIED_ID], reason='test')


async def test_unverifying_swaps_the_other_way(roles):
    member = make_member(roles=[roles[VERIFIED_ID]], guild_roles=roles)

    await apply_verification_roles(
        member, verified=False, verified_role_id=VERIFIED_ID,
        unverified_role_id=UNVERIFIED_ID, reason='test',
    )

    member.add_roles.assert_awaited_once_with(roles[UNVERIFIED_ID], reason='test')
    member.remove_roles.assert_awaited_once_with(roles[VERIFIED_ID], reason='test')


async def test_removal_skipped_when_member_lacks_the_role(roles):
    """A member who was never given Unverified shouldn't trigger a removal call."""
    member = make_member(roles=[], guild_roles=roles)

    await apply_verification_roles(
        member, verified=True, verified_role_id=VERIFIED_ID,
        unverified_role_id=UNVERIFIED_ID, reason='test',
    )

    member.add_roles.assert_awaited_once()
    member.remove_roles.assert_not_awaited()


async def test_unset_unverified_id_only_touches_verified(roles):
    member = make_member(roles=[roles[UNVERIFIED_ID]], guild_roles=roles)

    await apply_verification_roles(
        member, verified=True, verified_role_id=VERIFIED_ID,
        unverified_role_id=0, reason='test',
    )

    member.add_roles.assert_awaited_once_with(roles[VERIFIED_ID], reason='test')
    member.remove_roles.assert_not_awaited()


async def test_unset_verified_id_only_touches_unverified(roles):
    member = make_member(roles=[roles[VERIFIED_ID]], guild_roles=roles)

    await apply_verification_roles(
        member, verified=False, verified_role_id=0,
        unverified_role_id=UNVERIFIED_ID, reason='test',
    )

    member.add_roles.assert_awaited_once_with(roles[UNVERIFIED_ID], reason='test')
    member.remove_roles.assert_not_awaited()


async def test_both_ids_unset_makes_no_api_calls(roles):
    member = make_member(roles=[roles[UNVERIFIED_ID]], guild_roles=roles)

    await apply_verification_roles(
        member, verified=True, verified_role_id=0,
        unverified_role_id=0, reason='test',
    )

    member.add_roles.assert_not_awaited()
    member.remove_roles.assert_not_awaited()


async def test_add_skipped_when_member_already_has_the_role(roles):
    """Re-running /link shouldn't burn a role-op request re-adding Verified."""
    member = make_member(roles=[roles[VERIFIED_ID]], guild_roles=roles)

    await apply_verification_roles(
        member, verified=True, verified_role_id=VERIFIED_ID,
        unverified_role_id=UNVERIFIED_ID, reason='test',
    )

    member.add_roles.assert_not_awaited()
    member.remove_roles.assert_not_awaited()


async def test_unverifying_member_without_verified_role_skips_removal(roles):
    member = make_member(roles=[], guild_roles=roles)

    await apply_verification_roles(
        member, verified=False, verified_role_id=VERIFIED_ID,
        unverified_role_id=UNVERIFIED_ID, reason='test',
    )

    member.add_roles.assert_awaited_once_with(roles[UNVERIFIED_ID], reason='test')
    member.remove_roles.assert_not_awaited()


async def test_missing_role_in_guild_raises_before_any_api_call(roles):
    """A configured-but-deleted role is a config error: fail loudly, change nothing."""
    member = make_member(roles=[], guild_roles={VERIFIED_ID: roles[VERIFIED_ID]})

    with pytest.raises(RoleSwapError, match='Unverified'):
        await apply_verification_roles(
            member, verified=True, verified_role_id=VERIFIED_ID,
            unverified_role_id=UNVERIFIED_ID, reason='test',
        )

    member.add_roles.assert_not_awaited()
    member.remove_roles.assert_not_awaited()


async def test_forbidden_is_translated_to_role_swap_error(roles):
    member = make_member(roles=[roles[UNVERIFIED_ID]], guild_roles=roles)
    member.add_roles.side_effect = discord.Forbidden(MagicMock(status=403), 'nope')

    with pytest.raises(RoleSwapError, match='permission'):
        await apply_verification_roles(
            member, verified=True, verified_role_id=VERIFIED_ID,
            unverified_role_id=UNVERIFIED_ID, reason='test',
        )


async def test_missing_verified_role_raises_on_the_unlink_path(roles):
    member = make_member(roles=[], guild_roles={UNVERIFIED_ID: roles[UNVERIFIED_ID]})

    with pytest.raises(RoleSwapError, match='Verified'):
        await apply_verification_roles(
            member, verified=False, verified_role_id=VERIFIED_ID,
            unverified_role_id=UNVERIFIED_ID, reason='test',
        )

    member.add_roles.assert_not_awaited()


async def test_http_error_is_translated_to_role_swap_error(roles):
    member = make_member(roles=[roles[UNVERIFIED_ID]], guild_roles=roles)
    member.remove_roles.side_effect = discord.HTTPException(MagicMock(status=500), 'boom')

    with pytest.raises(RoleSwapError, match='Discord'):
        await apply_verification_roles(
            member, verified=True, verified_role_id=VERIFIED_ID,
            unverified_role_id=UNVERIFIED_ID, reason='test',
        )


async def test_failure_after_a_successful_add_reports_the_partial_swap(roles):
    """The add has already committed, so the user must be told the state is mixed."""
    member = make_member(roles=[roles[UNVERIFIED_ID]], guild_roles=roles)
    member.remove_roles.side_effect = discord.Forbidden(MagicMock(status=403), 'nope')

    with pytest.raises(RoleSwapError, match='already changed'):
        await apply_verification_roles(
            member, verified=True, verified_role_id=VERIFIED_ID,
            unverified_role_id=UNVERIFIED_ID, reason='test',
        )

    member.add_roles.assert_awaited_once()
