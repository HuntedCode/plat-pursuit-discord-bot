import discord
from discord import app_commands
from discord.ext import commands

from utils.api_errors import api_error_message

class SyncRolesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='sync-roles', description='Sync all your earned Discord roles (badges, milestones, premium).')
    async def sync_roles(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        session = self.bot.api_session
        payload = {'discord_id': discord_id}
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
                    'No linked profile found. Use /link to verify your PSN account first!',
                    ephemeral=True
                )
            else:
                await interaction.followup.send(await api_error_message(resp, action='sync-roles'), ephemeral=True)

async def setup(bot):
    await bot.add_cog(SyncRolesCog(bot))
