"""Verification role management shared by /link and /unlink.

The server's onboarding flow applies an "Unverified" role to new members. Once a
member links (and verifies) their PSN they should hold "Verified" instead, and
unlinking swaps them back. Both commands need that same swap, so it lives here.

Either role ID may be 0/unset, in which case that half of the swap is skipped.
Anything else that goes wrong raises RoleSwapError with a short, user-facing
reason so the calling command can surface it.
"""

import logging
from typing import Awaitable, Callable

import discord

logger = logging.getLogger('psn_api')

VERIFIED_LABEL = 'Verified'
UNVERIFIED_LABEL = 'Unverified'


class RoleSwapError(Exception):
    """A verification role change could not be applied.

    The message is a short lowercase clause meant to be embedded in a
    user-facing sentence (e.g. "Unlink succeeded but <message>. Contact admin.").
    """


def _resolve_roles(guild: discord.Guild, wanted: list[tuple[int, str]]) -> list[discord.Role]:
    """Turn (role_id, label) pairs into roles, skipping unset IDs."""
    roles = []
    for role_id, label in wanted:
        if not role_id:
            # An unset Unverified role is a supported configuration; an unset Verified
            # role means nobody gets verified, which should be loud.
            log = logger.warning if label == VERIFIED_LABEL else logger.debug
            log(f"No {label} role ID configured; skipping that half of the swap")
            continue
        role = guild.get_role(role_id)
        if role is None:
            logger.error(f"{label} role ID {role_id} not found in guild {guild.id}")
            raise RoleSwapError(f"the {label} role was not found in this server")
        roles.append(role)
    return roles


async def _call(
    action: str,
    method: Callable[..., Awaitable[None]],
    roles: list[discord.Role],
    reason: str,
    partial: bool = False,
) -> None:
    """Run one add_roles/remove_roles call, translating Discord errors to RoleSwapError."""
    suffix = ' (some roles were already changed)' if partial else ''
    try:
        await method(*roles, reason=reason)
    except discord.Forbidden as e:
        logger.error(f"Bot lacks permission to {action} roles {[r.name for r in roles]}: {e}")
        raise RoleSwapError(f"the bot lacks permission to manage those roles{suffix}") from e
    except discord.HTTPException as e:
        logger.error(f"Discord error on role {action} for {[r.name for r in roles]}: {e}")
        raise RoleSwapError(f"Discord rejected the role update{suffix}") from e


async def apply_verification_roles(
    member: discord.Member,
    *,
    verified: bool,
    verified_role_id: int,
    unverified_role_id: int,
    reason: str,
) -> None:
    """Swap a member between the Verified and Unverified roles.

    verified=True adds Verified and removes Unverified; verified=False does the
    reverse. Raises RoleSwapError if a configured role is missing or Discord
    rejects the change.
    """
    if verified:
        to_add = [(verified_role_id, VERIFIED_LABEL)]
        to_remove = [(unverified_role_id, UNVERIFIED_LABEL)]
    else:
        to_add = [(unverified_role_id, UNVERIFIED_LABEL)]
        to_remove = [(verified_role_id, VERIFIED_LABEL)]

    # Resolve both halves before touching Discord so a misconfigured role ID fails
    # cleanly instead of leaving the member half-swapped.
    add_roles = [r for r in _resolve_roles(member.guild, to_add) if r not in member.roles]
    remove_roles = [r for r in _resolve_roles(member.guild, to_remove) if r in member.roles]

    if add_roles:
        await _call('add', member.add_roles, add_roles, reason)
    if remove_roles:
        # The add above has already committed, so a failure here is a partial swap.
        await _call('remove', member.remove_roles, remove_roles, reason, partial=bool(add_roles))

    logger.info(
        f"Verification roles updated for {member.id}: "
        f"added {[r.name for r in add_roles] or 'none'}, removed {[r.name for r in remove_roles] or 'none'}"
    )
