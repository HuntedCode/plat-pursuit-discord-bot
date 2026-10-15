"""The slash-command tree's error handler.

Mod commands are hidden by Discord's default_permissions, but a server admin can widen those in
Server Settings > Integrations. So each mod command also checks permissions when it runs
(app_commands.checks.has_permissions). This turns a failed check, or any other error a command
does not handle itself, into a clear, private reply instead of Discord's generic "The application
did not respond" (or a deferred reply stuck on "thinking...").
"""
import logging

import discord
from discord import app_commands

logger = logging.getLogger('psn_api')

NO_PERMISSION_MESSAGE = "You don't have permission to use this command."
COOLDOWN_MESSAGE = "You're using this command too fast. Try again in {seconds}s."
FAILED_MESSAGE = 'Something went wrong. Please try again later.'

_PERMISSION_ERRORS = (app_commands.MissingPermissions, app_commands.MissingRole, app_commands.MissingAnyRole)


def _reply_for(error: app_commands.AppCommandError) -> str | None:
    """The reply for an expected error, or None for one that is a bug."""
    if isinstance(error, _PERMISSION_ERRORS):
        return NO_PERMISSION_MESSAGE
    if isinstance(error, app_commands.CommandOnCooldown):
        return COOLDOWN_MESSAGE.format(seconds=int(error.retry_after) + 1)
    return None


async def handle_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    command = interaction.command
    # discord.py calls this even after a command's own handler (a cog_app_command_error, say) has
    # already answered and logged, so step aside for those. The tree itself asks the same question
    # with this method before falling back to its default handler.
    if command is not None and command._has_any_error_handlers():
        return
    name = command.qualified_name if command else '?'
    message = _reply_for(error)
    if message is None:
        logger.error(f"Unhandled error in /{name}", exc_info=error)
        message = FAILED_MESSAGE
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException as e:
        # The interaction may have expired (it has 15 minutes) or been answered elsewhere.
        logger.warning(f"Could not send the error reply for /{name}: {e!r}")
