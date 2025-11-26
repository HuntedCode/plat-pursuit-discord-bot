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
                    embed.add_field(name="Your Code (Copy This!)", value=f"{data['code']}", inline=False)
                    embed.add_field(
                        name="🔴 IMPORTANT 🔴 - Instructions:",
                        value=f"Put the code above in your PSN 'About Me' section and press the 'Verify Now' button below to finish linking your account!\n\nThis button will expire in 15 minutes. If button expires, run /link again.",
                        inline=False
                    )

                    view = discord.ui.View(timeout=900)

                    async def verify_callback(interaction: discord.Interaction):
                        modal = discord.ui.Modal(title="Confirm PSN Username for Verification")
                        modal.add_item(discord.ui.TextInput(label='PSN Username', default=psn_username, required=True))

                        async def on_submit(modal_interaction: discord.Interaction):
                            await modal_interaction.response.defer(ephemeral=True)
                            input_psn = modal.children[0].value.lower()
                            async with aiohttp.ClientSession() as session:
                                print("Verify Now button pushed..")
                                verify_payload = {'discord_id': discord_id, 'psn_username': input_psn}
                                async with session.post(f"{self.bot.api_base_url}verify/", json=verify_payload, headers=headers) as verify_resp:
                                    if verify_resp.status == 200:
                                        data = await verify_resp.json()
                                        if data.get('success'):
                                            if self.bot.verified_role_id == 0:
                                                logger.warning(f"No VERIFIED_ROLE_ID set. Skipping role assignment for {discord_id}")
                                            else:
                                                try:
                                                    role = modal_interaction.guild.get_role(self.bot.verified_role_id)
                                                    if role:
                                                        await modal_interaction.user.add_roles(role)
                                                        logger.info(f"Assigned verified role to {discord_id}")
                                                    else:
                                                        logger.error(f"Role ID {self.bot.verified_role_id} not found in guild {modal_interaction.guild_id}")
                                                except discord.Forbidden:
                                                    logger.error(f"Bot lacks permissions to assign role to {discord_id}")
                                                    await modal_interaction.followup.send('Verification succeeded but role assignment failed. Contact admin.', ephemeral=True)
                                                    return
                                                except Exception as e:
                                                    logger.error(f"Role assignment error: {e}")
                                                    await modal_interaction.followup.send('Verification succeeded but unexpected error assigning role. Contact admin.', ephemeral=True)
                                                    return
                                            await modal_interaction.followup.send('Success! Your PSN is verified and linked.', ephemeral=True)
                                        else:
                                            await modal_interaction.followup.send(f"Error: {data.get('message'), 'Verification failed. Check About Me and permissions.'}", ephemeral=True)
                                    else:
                                        await modal_interaction.followup.send('API error. Please try again later.', ephemeral=True)

                        modal.on_submit = on_submit
                        await interaction.response.send_modal(modal)

                    button = discord.ui.Button(label="Verify Now", style=discord.ButtonStyle.primary)
                    button.callback = verify_callback
                    view.add_item(button)

                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                    logger.info(f"Generated code for user {discord_id} - PSN: {psn_username}")
                elif resp.status == 400:
                    data = await resp.json()
                    error_msg = data.get('non_field_errors', [data.get('detail', 'Unknown error')])[0]
                    await interaction.followup.send(f"Error: {error_msg}", ephemeral=True)
                else:
                    await interaction.followup.send('Error generating code. Please try again later or contact an admin.', ephemeral=True)

async def setup(bot):
    await bot.add_cog(LinkCog(bot))