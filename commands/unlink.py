import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import logging

logger = logging.getLogger('psn_api')

class UnlinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='unlink', description='Unlink your Discord from your PSN profile.')
    async def unlink(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)
        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f"Token {self.bot.api_key}"}
            params = {'discord_id': discord_id}
            async with session.get(f"{self.bot.api_base_url}check-linked/", params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data.get('linked'):
                        await interaction.followup.send(data.get('message', 'No linked profile to unlink.'), ephemeral=True)
                        return
                else:
                    await interaction.followup.send('Error checking link. Please try again later.')
                    return
        
        embed = discord.Embed(title='Confirm Unlink', description=f"Are you sure you want to unlink your PSN profile?\n\n{data.get('psn_username')}\n\nThis action cannot be undone.", color=0xff0000)
        view = discord.ui.View(timeout=180)

        async def yes_callback(interaction: discord.Interaction):
            async with aiohttp.ClientSession() as session:
                payload = {'discord_id': discord_id}
                async with session.post(f"{self.bot.api_base_url}unlink/", json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        await interaction.response.edit_message(content='Unlinked successfully!', embed=None, view=None)
                    else:
                        await interaction.response.edit_message(content='Unlink failed. Try again or contact an admin.', embed=None, view=None)

        async def no_callback(interaction: discord.Interaction):
            await interaction.response.edit_message(content='Unlink cancelled.', embed=None, view=None)
        
        yes_button = discord.ui.Button(label='Yes, Unlink', style=discord.ButtonStyle.danger)
        yes_button.callback = yes_callback
        no_button = discord.ui.Button(label='No, Cancel', style=discord.ButtonStyle.secondary)
        no_button.callback = no_callback
        view.add_item(yes_button)
        view.add_item(no_button)

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(UnlinkCog(bot))