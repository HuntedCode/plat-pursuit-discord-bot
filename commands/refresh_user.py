import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import logging

logger = logging.getLogger('psn_api')

class RefreshUserCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='refresh_user', description='MODERATOR ONLY: Refresh an user\'s PSN profile.')
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(user='The user to refresh.')
    async def refresh_user(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer()

        target_discord_id = str(user.id)
        async with aiohttp.ClientSession as session:
            headers = {'Authorization': f"Token {self.bot.api_key}"}
            payload = {'discord_id': target_discord_id, 'admin_override': True}
            async with session.post(f"{self.bot.api_base_url}refresh_profile/", json=payload, headers=headers) as resp:
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