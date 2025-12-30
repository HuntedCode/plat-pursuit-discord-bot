# No longer in use - encapsulated by the link command

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
                        if self.bot.verified_role_id == 0:
                            logger.warning(f"No VERIFIED_ROLE_ID set. Skipping role assignment for {discord_id}")
                        else:
                            try:
                                role = interaction.guild.get_role(self.bot.verified_role_id)
                                if role:
                                    await interaction.user.add_roles(role)
                                    logger.info(f"Assigned verified role to {discord_id}")
                                else:
                                    logger.error(f"Role ID {self.bot.verified_role_id} not found in guild {interaction.guild_id}")
                            except discord.Forbidden:
                                logger.error(f"Bot lacks permissions to assign role to {discord_id}")
                                await interaction.followup.send('Verification succeeded but role assignment failed. Contact admin.', ephemeral=True)
                                return
                            except Exception as e:
                                logger.error(f"Role assignment error: {e}")
                                await interaction.followup.send('Verification succeeded but unexpected error assigning role. Contact admin.', ephemeral=True)
                                return
                        await interaction.followup.send(f"Success! <@{discord_id}>, your PSN has been linked and verified. Enjoy your time in PlatPursuit!", ephemeral=True)
                    else:
                        await interaction.followup.send(f"Error: {data.get('message'), 'Verification failed. Check your "About Me" and account permissions.'}", ephemeral=True)
                else:
                    await interaction.followup.send('API error. Please try again later.', ephemeral=True)

async def setup(bot):
    await bot.add_cog(VerifyCog(bot))