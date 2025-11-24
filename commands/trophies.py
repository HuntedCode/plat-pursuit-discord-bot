import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

class TrophiesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='trophies', description='View your or another user\'s trophy progress.')
    @app_commands.describe(user='Optional: Mention a user to view their trophies. Leave blank to view your own.')
    async def trophies(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        target = user or interaction.user
        discord_id = str(target.id)

        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f"Bearer {self.bot.application_id}_{self.bot.api_key}"}
            params = {'discord_id': discord_id}
            async with session.get(f"{self.bot.api_base_url}trophies/", params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('linked'):
                        embed = discord.Embed(title=f"{target.display_name}\'s Trophy Progress", color=0x00ff00)
                        embed.add_field(name='Total Trophies', value=data['trophies']['total'], inline=True)
                        embed.add_field(name='Platinum', value=data['trophies']['platinum'], inline=True)
                        await interaction.followup.send(embed=embed)
                    else:
                        await interaction.followup.send('No PSN linked. User /register first!')
                else:
                    await interaction.followup.send('API error. Please try again later.')

async def setup(bot):
    await bot.add_cog(TrophiesCog(bot))