import discord
from discord import app_commands
from discord.ext import commands
from aiohttp import ClientTimeout

from utils.api_errors import api_error_message

#: A full recheck evaluates every badge against all of a user's games in one request, so it gets
#: longer than the session's default API timeout before the mod is told it failed.
RECHECK_TIMEOUT = ClientTimeout(total=120)

class RecheckBadgesUserCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='recheck_badges_user', description='ADMIN ONLY: Run a full badge recheck for a user.')
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(user='The user whose badges to recheck.')
    async def recheck_badges_user(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        target_discord_id = str(user.id)
        session = self.bot.api_session
        payload = {'discord_id': target_discord_id}
        async with session.post(f"{self.bot.api_base_url}recheck-badges/", json=payload, timeout=RECHECK_TIMEOUT) as resp:
            if resp.status == 200:
                data = await resp.json()
                psn_username = data.get('psn_username', 'Unknown')
                badges_checked = data.get('badges_checked', 0)
                awarded = data.get('awarded', [])
                revoked = data.get('revoked', [])

                lines = [f"Badge recheck complete for **{psn_username}** — {badges_checked} badge(s) checked."]

                if awarded:
                    lines.append(f"**Awarded:** {', '.join(awarded)}")
                if revoked:
                    lines.append(f"**Revoked:** {', '.join(revoked)}")
                if not awarded and not revoked:
                    lines.append("All badges are correct, no changes needed.")

                await interaction.followup.send('\n'.join(lines), ephemeral=True)
            elif resp.status == 404:
                await interaction.followup.send(
                    f'No linked profile found for {user.display_name}.',
                    ephemeral=True
                )
            else:
                await interaction.followup.send(await api_error_message(resp, action='recheck-badges'), ephemeral=True)

async def setup(bot):
    await bot.add_cog(RecheckBadgesUserCog(bot))
