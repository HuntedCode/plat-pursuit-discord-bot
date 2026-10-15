"""Error replies for slash commands and for the buttons they post.

Mod commands are hidden by Discord's default_permissions, but a server admin can widen those in
Server Settings > Integrations. So each mod command also checks permissions when it runs
(app_commands.checks.has_permissions). The tree's handler turns a failed check, or any other error
a command does not handle itself, into a clear, private reply instead of Discord's generic "The
application did not respond" (or a deferred reply stuck on "thinking..."). Button callbacks never
reach the tree's handler, so their views subclass `ReplyOnErrorView` for the same reply (without
it, the press just shows "This interaction failed").
"""
import asyncio
import logging

import aiohttp
import discord
from discord import app_commands

logger = logging.getLogger('psn_api')

NO_PERMISSION_MESSAGE = "You don't have permission to use this command."
COOLDOWN_MESSAGE = "You're using this command too fast. Try again in {seconds}s."
FAILED_MESSAGE = 'Something went wrong. Please try again later.'
#: A dropped connection or a timeout. Not "PlatPursuit is having trouble": these errors come from
#: Discord's HTTP calls too, and the handler cannot tell which service it was.
NETWORK_MESSAGE = 'A connection problem got in the way. Please try again in a moment.'

_PERMISSION_ERRORS = (app_commands.MissingPermissions, app_commands.MissingRole, app_commands.MissingAnyRole)
#: A service could not be reached: a timeout (API_TIMEOUT_SECONDS for PlatPursuit) or a dropped
#: connection. asyncio's TimeoutError is the builtin one on 3.11+.
_NETWORK_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)


def _unwrap(error: Exception) -> Exception:
    return error.original if isinstance(error, app_commands.CommandInvokeError) else error


def _reply_for(error: Exception) -> str | None:
    """The reply for an expected error, or None for one that is a bug."""
    error = _unwrap(error)
    if isinstance(error, _PERMISSION_ERRORS):
        return NO_PERMISSION_MESSAGE
    if isinstance(error, app_commands.CommandOnCooldown):
        return COOLDOWN_MESSAGE.format(seconds=int(error.retry_after) + 1)
    if isinstance(error, _NETWORK_ERRORS):
        return NETWORK_MESSAGE
    return None


async def _reply_to_error(interaction: discord.Interaction, error: Exception, where: str) -> None:
    message = _reply_for(error)
    if message is None:
        logger.error(f"Unhandled error in {where}", exc_info=error)
        message = FAILED_MESSAGE
    elif isinstance(_unwrap(error), _NETWORK_ERRORS):
        logger.warning(f"Network error in {where}: {_unwrap(error)!r}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException as e:
        # The interaction may have expired (it has 15 minutes) or been answered elsewhere.
        logger.warning(f"Could not send the error reply for {where}: {e!r}")


async def edit_reply(interaction: discord.Interaction, **fields) -> None:
    """Edit the message a button sits on, whether or not the press has been answered yet.

    For a callback's error path: if it already answered (`response.edit_message`) and then
    failed, answering again would raise InteractionResponded, so edit the original instead.
    """
    if interaction.response.is_done():
        await interaction.edit_original_response(**fields)
    else:
        await interaction.response.edit_message(**fields)


async def handle_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    command = interaction.command
    # discord.py calls this even after a command's own handler (a cog_app_command_error, say) has
    # already answered and logged, so step aside for those. The tree itself asks the same question
    # with this method before falling back to its default handler.
    if command is not None and command._has_any_error_handlers():
        return
    await _reply_to_error(interaction, error, f"/{command.qualified_name if command else '?'}")


class ReplyOnErrorView(discord.ui.View):
    """A view whose button callbacks get the same private error reply as slash commands.

    discord.py routes a callback's exception to `View.on_error`, whose default only logs it, so the
    person who pressed the button is left on "thinking..." with no answer.
    """

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        label = getattr(item, 'label', None) or type(item).__name__
        await _reply_to_error(interaction, error, f"the '{label}' button")
