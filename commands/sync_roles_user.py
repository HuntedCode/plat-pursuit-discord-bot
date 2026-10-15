import discord
from discord import app_commands
from discord.ext import commands

from utils.api_errors import api_error_message

class SyncRolesUserCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='sync_roles_user', description='MODERATOR ONLY: Sync all earned Discord roles for a user.')
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(user='The user to sync roles for.')
    async def sync_roles_user(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        target_discord_id = str(user.id)
        session = self.bot.api_session
        payload = {'discord_id': target_discord_id}
        async with session.post(f"{self.bot.api_base_url}sync-roles/", json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                breakdown = data.get('breakdown', {})
                badge_roles = breakdown.get('badge_roles', 0)
                milestone_roles = breakdown.get('milestone_roles', 0)
                premium_roles = breakdown.get('premium_roles', 0)
                roles_synced = data.get('roles_synced', 0)
                psn_username = data.get('psn_username', 'Unknown')
                await interaction.followup.send(
                    f"Synced {roles_synced} role(s) for {psn_username} "
                    f"({badge_roles} badge, {milestone_roles} milestone, {premium_roles} premium)",
                    ephemeral=True
                )
            elif resp.status == 404:
                await interaction.followup.send(
                    f'No linked profile found for {user.display_name}.',
                    ephemeral=True
                )
            else:
                await interaction.followup.send(await api_error_message(resp, action='sync-roles'), ephemeral=True)

async def setup(bot):
    await bot.add_cog(SyncRolesUserCog(bot))
