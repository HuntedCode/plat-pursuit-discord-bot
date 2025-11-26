import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import logging

logger = logging.getLogger('psn_api')

class LinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='link', description='Begin linking your PSN. NOTE: Generates a code that must be verified!')
    @app_commands.describe(psn_username='Your PSN username (required)')
    async def link(self, interaction: discord.Interaction, psn_username: str):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f"Token {self.bot.api_key}"}
            payload = {'psn_username': psn_username, 'discord_id': discord_id}
            async with session.post(f"{self.bot.api_base_url}generate-code/", json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    embed = discord.Embed(title="Verification Code Generated", color=0x00ff00)
                    embed.add_field(name="Your Code (Copy This!)", value=f"**{data['code']}**", inline=False)
                    embed.add_field(
                        name="🔴 IMPORTANT Instructions",
                        value=f"**Put the code above in your PSN 'About Me' section and use /verify to finish linking your account!**\n\nThis code expires in 1 hour.",
                        inline=False
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    logger.info(f"Generated code for user {discord_id} - PSN: {psn_username}")
                elif resp.status == 400:
                    data = await resp.json()
                    error_msg = data.get('non_field_errors', [data.get('detail', 'Unknown error')])[0]
                    await interaction.followup.send(f"Error: {error_msg}", ephemeral=True)
                else:
                    await interaction.followup.send('Error generating code. Please try again later or contact an admin.', ephemeral=True)

async def setup(bot):
    await bot.add_cog(LinkCog(bot))