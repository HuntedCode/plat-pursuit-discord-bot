import discord
from discord import app_commands
from discord.ext import commands
import logging

from utils.roles import RoleSwapError, apply_verification_roles

logger = logging.getLogger('psn_api')

class UnlinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='unlink', description='Unlink your Discord from your PSN profile.')
    async def unlink(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        discord_id = str(interaction.user.id)
        session = self.bot.api_session
        params = {'discord_id': discord_id}
        async with session.get(f"{self.bot.api_base_url}check-linked/", params=params) as resp:
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
            # The unlink API call plus two role operations can outrun Discord's 3s
            # interaction deadline, so acknowledge first and edit the result in after.
            await interaction.response.defer()
            payload = {'discord_id': discord_id}
            async with session.post(f"{self.bot.api_base_url}unlink/", json=payload) as resp:
                if resp.status != 200:
                    await interaction.edit_original_response(content='Unlink failed. Try again or contact an admin.', embed=None, view=None)
                    return

                try:
                    await apply_verification_roles(
                        interaction.user,
                        verified=False,
                        verified_role_id=self.bot.verified_role_id,
                        unverified_role_id=self.bot.unverified_role_id,
                        reason='PSN unlinked via /unlink',
                    )
                    result = 'Unlinked successfully!'
                except RoleSwapError as e:
                    result = f"Unlink succeeded but {e}. Contact admin."
                except Exception as e:
                    logger.error(f"Unexpected error removing verification roles for {discord_id}: {e}")
                    result = 'Unlink succeeded but unexpected error updating roles. Contact admin.'
                await interaction.edit_original_response(content=result, embed=None, view=None)

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
