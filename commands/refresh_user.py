import discord
from discord import app_commands
from discord.ext import commands
import logging

logger = logging.getLogger('psn_api')

class RefreshUserCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='refresh_user', description='MODERATOR ONLY: Refresh an user\'s PSN profile.')
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(user='The user to refresh.')
    async def refresh_user(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        target_discord_id = str(user.id)
        session = self.bot.api_session
        payload = {'discord_id': target_discord_id, 'admin_override': True}
        async with session.post(f"{self.bot.api_base_url}refresh/", json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('success'):
                    await interaction.followup.send(f"Successfully refreshed profile for {user.display_name}.", ephemeral=True)
                else:
                    await interaction.followup.send(f"Error: {data.get('message', 'Refresh failed. Check if linked.')}", ephemeral=True)
            else:
                await interaction.followup.send('API error. Please try again later.', ephemeral=True)

async def setup(bot):
    await bot.add_cog(RefreshUserCog(bot))
