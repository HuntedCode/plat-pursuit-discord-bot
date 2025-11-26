import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import logging

logger = logging.getLogger('psn_api')

class VerifyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='verify', description='Verify your PSN account after adding code to your About Me section.')
    @app_commands.describe(psn_username='Your PSN username (required)')
    async def verify(self, interaction: discord.Interaction, psn_username: str):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f"Token {self.bot.api_key}"}
            payload = {'discord_id': discord_id, 'psn_username': psn_username}
            async with session.post(f"{self.bot.api_base_url}verify/", json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        await interaction.followup.send('Success! Your PSN is verified and linked.', ephemeral=True)
                    else:
                        await interaction.followup.send(f"Error: {data.get('message'), 'Verification failed. Check About Me and permissions.'}", ephemeral=True)
                else:
                    await interaction.followup.send('API error. Please try again later.', ephemeral=True)

async def setup(bot):
    await bot.add_cog(VerifyCog(bot))