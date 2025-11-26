import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import logging

logger = logging.getLogger('psn_api')

class RefreshCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='refresh', description='Refresh your PSN account! (Only once per hour.)')
    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f"Token {self.bot.api_key}"}
            payload = {'discord_id': discord_id}
            async with session.post(f"{self.bot.api_base_url}refresh/", json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data.get('linked'):
                        await interaction.followup.send(data.get('message', 'No linked profile to refresh. Use /link to connect now!'), ephemeral=True)
                        return
                    if data.get('success'):
                        await interaction.followup.send(f"Your PSN profile {data['psn_username']} is refreshing now!", ephemeral=True)
                    else:
                        await interaction.followup.send(f"Error: {data.get('message', 'Refresh failed. Please try again later.')}", ephemeral=True)
                else:
                    await interaction.followup.send('API error. Please try again later.', ephemeral=True)
    
async def setup(bot):
    await bot.add_cog(RefreshCog(bot))