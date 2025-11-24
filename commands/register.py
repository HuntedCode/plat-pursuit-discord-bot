import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

class RegisterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='register', description='Link your Discord to a PSN account using the PSN username.')
    @app_commands.describe(psn_username='Your PSN username (required)')
    async def register(self, interaction: discord.Interaction, psn_username: str):
        await interaction.response.defer()
        discord_id = str(interaction.id)

        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f"Bearer {self.bot.application_id}_{self.bot.api_key}"}
            payload = {'discord_id': discord_id, 'psn_username': psn_username}
            async with session.post(f"{self.bot.api_base_url}register/", json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        await interaction.followup.send(f"Success! Linked {psn_username} to your Discord.")
                    else:
                        await interaction.followup.send(f"Error {data.get('message'), 'Failed to link. Check supplied username and try again later.'}")
                else:
                    await interaction.followup.send('API error. Please try again later or contact an admin.')

async def setup(bot):
    await bot.add_cog(RegisterCog(bot))