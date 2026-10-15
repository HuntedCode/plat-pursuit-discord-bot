"""The slash-command tree's error handler.

Mod commands are hidden by Discord's default_permissions, but a server admin can widen those in
Server Settings > Integrations. So each mod command also checks permissions when it runs
(app_commands.checks.has_permissions). This turns a failed check into a clear, private reply
instead of Discord's generic "The application did not respond".
"""
import logging

import discord
from discord import app_commands

logger = logging.getLogger('psn_api')

NO_PERMISSION_MESSAGE = "You don't have permission to use this command."


async def handle_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        if interaction.response.is_done():
            await interaction.followup.send(NO_PERMISSION_MESSAGE, ephemeral=True)
        else:
            await interaction.response.send_message(NO_PERMISSION_MESSAGE, ephemeral=True)
        return
    command = interaction.command.qualified_name if interaction.command else '?'
    logger.error(f"Unhandled error in /{command}", exc_info=error)
