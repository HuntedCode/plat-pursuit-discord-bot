"""Error replies for slash commands and for the buttons they post.

Mod commands are hidden by Discord's default_permissions, but a server admin can widen those in
Server Settings > Integrations. So each mod command also checks permissions when it runs
(app_commands.checks.has_permissions). The tree's handler turns a failed check, or any other error
a command does not handle itself, into a clear, private reply instead of Discord's generic "The
application did not respond" (or a deferred reply stuck on "thinking..."). Button callbacks never
reach the tree's handler, so their views subclass `ReplyOnErrorView` for the same reply.
"""
import asyncio
import logging

import aiohttp
import discord
from discord import app_commands

from utils.api_errors import UNAVAILABLE_MESSAGE

logger = logging.getLogger('psn_api')

NO_PERMISSION_MESSAGE = "You don't have permission to use this command."
COOLDOWN_MESSAGE = "You're using this command too fast. Try again in {seconds}s."
FAILED_MESSAGE = 'Something went wrong. Please try again later.'

_PERMISSION_ERRORS = (app_commands.MissingPermissions, app_commands.MissingRole, app_commands.MissingAnyRole)
#: PlatPursuit could not be reached at all: a timeout (API_TIMEOUT_SECONDS) or a dropped connection.
_UNREACHABLE_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)


def _reply_for(error: Exception) -> str | None:
    """The reply for an expected error, or None for one that is a bug."""
    if isinstance(error, app_commands.CommandInvokeError):
        error = error.original
    if isinstance(error, _PERMISSION_ERRORS):
        return NO_PERMISSION_MESSAGE
    if isinstance(error, app_commands.CommandOnCooldown):
        return COOLDOWN_MESSAGE.format(seconds=int(error.retry_after) + 1)
    if isinstance(error, _UNREACHABLE_ERRORS):
        return UNAVAILABLE_MESSAGE
    return None


async def _reply_to_error(interaction: discord.Interaction, error: Exception, where: str) -> None:
    message = _reply_for(error)
    if message is None:
        logger.error(f"Unhandled error in {where}", exc_info=error)
        message = FAILED_MESSAGE
    elif message == UNAVAILABLE_MESSAGE:
        logger.warning(f"Could not reach PlatPursuit in {where}: {error!r}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException as e:
        # The interaction may have expired (it has 15 minutes) or been answered elsewhere.
        logger.warning(f"Could not send the error reply for {where}: {e!r}")


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
