import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import logging

from utils.api_errors import api_error_message
from utils.command_errors import ReplyOnErrorView
from utils.roles import RoleSwapError, apply_verification_roles

logger = logging.getLogger('psn_api')

#: Verify failures worth pressing the button again for: a private profile (403, fixable in PSN's
#: settings), a sync timeout (408), PlatPursuit's throttle (429) and server or PSN trouble (5xx).
#: Anything else (400 already linked, 404, 409) cannot succeed on a retry, so the button goes.
RETRYABLE_VERIFY_STATUSES = frozenset({403, 408, 429, 500, 502, 503, 504})

class LinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _sync_roles_fire_and_forget(self, discord_id: str):
        """Call sync-roles API after verification. Failures are logged but not surfaced to user."""
        try:
            session = self.bot.api_session
            payload = {'discord_id': discord_id}
            async with session.post(f"{self.bot.api_base_url}sync-roles/", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"Post-verify sync-roles succeeded for {discord_id}: {data.get('roles_synced', 0)} roles synced")
                else:
                    body = await resp.text()
                    logger.warning(f"Post-verify sync-roles returned {resp.status} for {discord_id}: {body}")
        except Exception as e:
            logger.error(f"Post-verify sync-roles failed for {discord_id}: {e}")

    @app_commands.command(name='link', description='Begin linking your PSN. NOTE: Generates a code that must be verified!')
    @app_commands.describe(psn_username='Your PSN username (required)')
    async def link(self, interaction: discord.Interaction, psn_username: str):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        session = self.bot.api_session
        payload = {'psn_username': psn_username.lower(), 'discord_id': discord_id}
        async with session.post(f"{self.bot.api_base_url}generate-code/", json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                embed = discord.Embed(title="Verification Code Generated", color=0x00ff00)
                embed.add_field(name="Your Code (Copy This!)", value=f"{data['code']}", inline=False)
                embed.add_field(
                    name="🔴 IMPORTANT 🔴 - Instructions:",
                    value=f"Put the code above in your PSN 'About Me' section and press the 'Verify Now' button below to finish linking your account!\n\nThis button will expire in 15 minutes. If button expires, run /link again.",
                    inline=False
                )

                if data.get('privacy_warning'):
                    embed.add_field(
                        name="⚠️ Privacy Warning",
                        value=data['privacy_warning'],
                        inline=False
                    )

                view = ReplyOnErrorView(timeout=900)

                async def verify_callback(button_interaction: discord.Interaction):
                    await button_interaction.response.defer(ephemeral=True)
                    verify_payload = {'discord_id': discord_id, 'psn_username': psn_username.lower()}
                    async with session.post(f"{self.bot.api_base_url}verify/", json=verify_payload) as verify_resp:
                        if verify_resp.status != 200:
                            message = await api_error_message(verify_resp, action='verify')
                            await button_interaction.followup.send(message, ephemeral=True)
                            if verify_resp.status not in RETRYABLE_VERIFY_STATUSES:
                                # Already linked, or nothing to verify: pressing again cannot help.
                                original_message = await button_interaction.original_response()
                                await original_message.edit(view=None)
                            return

                        data = await verify_resp.json()
                        if data.get('success'):
                            # Achievement-role sync is independent of the verification
                            # role swap, so start it first: a misconfigured role must
                            # not stop a verified user's badge roles from syncing.
                            asyncio.create_task(self._sync_roles_fire_and_forget(discord_id))
                            try:
                                await apply_verification_roles(
                                    button_interaction.user,
                                    verified=True,
                                    verified_role_id=self.bot.verified_role_id,
                                    unverified_role_id=self.bot.unverified_role_id,
                                    reason='PSN verified via /link',
                                )
                                result = 'Success! Your PSN is verified and linked.'
                                # Listened for by WelcomeCog (verified welcome post).
                                # Dispatched only after the role swap returns cleanly,
                                # so a failed assignment never gets a public welcome.
                                self.bot.dispatch(
                                    'psn_verified',
                                    button_interaction.user,
                                    data.get('psn_username') or psn_username,
                                )
                            except RoleSwapError as e:
                                result = f"Verification succeeded but {e}. Contact admin."
                            except Exception as e:
                                logger.error(f"Unexpected error assigning verification roles for {discord_id}: {e}")
                                result = 'Verification succeeded but unexpected error assigning roles. Contact admin.'
                            await button_interaction.followup.send(result, ephemeral=True)
                        else:
                            await button_interaction.followup.send(f"Error: {data.get('message', 'Verification failed. Check About Me and permissions.')}", ephemeral=True)

                        original_message = await button_interaction.original_response()
                        await original_message.edit(view=None)

                button = discord.ui.Button(label="Verify Now", style=discord.ButtonStyle.primary)
                button.callback = verify_callback
                view.add_item(button)

                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                logger.info(f"Generated code for user {discord_id} - PSN: {psn_username}")
            else:
                await interaction.followup.send(await api_error_message(resp, action='generate-code'), ephemeral=True)

async def setup(bot):
    await bot.add_cog(LinkCog(bot))
