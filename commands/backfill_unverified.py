"""One-time transition helper for the Verified/Unverified onboarding pair.

Before this system existed, members were only ever given the Verified role on link,
so everyone who never linked holds neither role. This command hands the Unverified
role to those members so the onboarding gate applies to the whole server.

It is idempotent (members who already hold either role are skipped), so it is safe
to re-run once a batch has finished, and safe to delete once the server has been
backfilled.
"""

import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import logging

logger = logging.getLogger('psn_api')

CONFIRM_TIMEOUT = 180

# The role worker sleeps between operations to stay under Discord's role rate
# limits; used only to give the mod a rough duration up front.
SECONDS_PER_ROLE_OP = 0.5


def select_backfill_targets(
    members: list[discord.Member],
    verified_role: discord.Role,
    unverified_role: discord.Role,
) -> list[discord.Member]:
    """Members who should receive the Unverified role.

    Bots are excluded, as are members who are already verified or who already hold
    the Unverified role (which makes re-running the backfill a cheap no-op).
    """
    return [
        m for m in members
        if not m.bot and verified_role not in m.roles and unverified_role not in m.roles
    ]


class BackfillUnverifiedCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Targets are chosen from the member cache, which still shows queued members
        # as roleless until the worker gets to them. Without this guard a second run
        # would enqueue the whole pending batch again.
        self.in_progress = False

    async def _clear_when_drained(self, count: int):
        """Release the in-progress guard once the role worker has caught up."""
        try:
            await self.bot.role_queue.join()
            logger.info(f"Backfill finished: role queue drained after {count} queued job(s)")
        finally:
            self.in_progress = False

    @app_commands.command(
        name='backfill_unverified',
        description='MODERATOR ONLY: One-time backfill of the Unverified role for members without Verified.',
    )
    @app_commands.default_permissions(manage_roles=True)
    async def backfill_unverified(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send('This command can only be used in a server.', ephemeral=True)
            return

        if self.in_progress:
            await interaction.followup.send(
                'A backfill is already running. Wait for it to finish before starting another.',
                ephemeral=True,
            )
            return

        if not self.bot.verified_role_id or not self.bot.unverified_role_id:
            await interaction.followup.send(
                'Both VERIFIED_ROLE_ID and UNVERIFIED_ROLE_ID must be configured to run the backfill.',
                ephemeral=True,
            )
            return

        verified_role = guild.get_role(self.bot.verified_role_id)
        unverified_role = guild.get_role(self.bot.unverified_role_id)
        missing = [
            name for name, role in (('Verified', verified_role), ('Unverified', unverified_role))
            if role is None
        ]
        if missing:
            await interaction.followup.send(
                f"Could not find the {' and '.join(missing)} role(s) in this server. Check the configured role IDs.",
                ephemeral=True,
            )
            return

        # Check up front rather than per member: the worker retries a failed assignment
        # five times, so a hierarchy mistake would mean five wasted API calls per member.
        if not guild.me.guild_permissions.manage_roles:
            await interaction.followup.send(
                'The bot is missing the Manage Roles permission, so it cannot run the backfill.',
                ephemeral=True,
            )
            return
        if unverified_role.position >= guild.me.top_role.position:
            await interaction.followup.send(
                f"The {unverified_role.name} role sits above the bot's highest role, so the bot cannot assign it. "
                "Move the bot's role higher and try again.",
                ephemeral=True,
            )
            return

        # guild.members only holds the full roster once the member list is chunked.
        if not guild.chunked:
            try:
                await guild.chunk()
            except Exception as e:
                logger.error(f"Backfill could not chunk guild {guild.id}: {e}")
                await interaction.followup.send(
                    'Could not load the full member list. Please try again later.',
                    ephemeral=True,
                )
                return

        targets = select_backfill_targets(guild.members, verified_role, unverified_role)
        if not targets:
            await interaction.followup.send(
                'Nothing to do: every member already holds the Verified or Unverified role.',
                ephemeral=True,
            )
            return

        minutes = max(1, round(len(targets) * SECONDS_PER_ROLE_OP / 60))
        embed = discord.Embed(
            title='Backfill Unverified Role',
            description=(
                f"**{len(targets)}** member(s) will be given the {unverified_role.mention} role.\n\n"
                f"Scanned {len(guild.members)} member(s); bots and anyone already holding "
                f"{verified_role.mention} or {unverified_role.mention} are skipped."
            ),
            color=0xffaa00,
        )
        embed.add_field(
            name='Before you confirm',
            value=(
                f"Roles are queued through the shared rate-limited worker, so this takes "
                f"**at least {minutes} minute(s)**, and role assignments coming from PlatPursuit "
                f"queue up behind it until it finishes. Progress is logged, not reported here, and a "
                f"bot restart drops anything still queued. Re-running afterward picks up the rest."
            ),
            inline=False,
        )

        view = discord.ui.View(timeout=CONFIRM_TIMEOUT)

        async def confirm_callback(button_interaction: discord.Interaction):
            if button_interaction.user.id != interaction.user.id:
                await button_interaction.response.send_message(
                    'Only the moderator who ran this command can confirm it.', ephemeral=True
                )
                return
            self.in_progress = True
            for member in targets:
                # Payload shape is the contract read by role_assignment_worker in bot.py.
                await self.bot.role_queue.put({
                    'user_id': member.id,
                    'role_id': unverified_role.id,
                    'guild_id': guild.id,
                })
            asyncio.create_task(self._clear_when_drained(len(targets)))
            logger.info(
                f"Backfill queued Unverified role for {len(targets)} member(s) in guild {guild.id} "
                f"by {interaction.user.id}"
            )
            await button_interaction.response.edit_message(
                content=f"Queued the Unverified role for {len(targets)} member(s). Watch the logs for progress.",
                embed=None,
                view=None,
            )
            view.stop()

        async def cancel_callback(button_interaction: discord.Interaction):
            if button_interaction.user.id != interaction.user.id:
                await button_interaction.response.send_message(
                    'Only the moderator who ran this command can cancel it.', ephemeral=True
                )
                return
            await button_interaction.response.edit_message(content='Backfill cancelled.', embed=None, view=None)
            view.stop()

        confirm = discord.ui.Button(label=f"Yes, backfill {len(targets)}", style=discord.ButtonStyle.danger)
        confirm.callback = confirm_callback
        cancel = discord.ui.Button(label='Cancel', style=discord.ButtonStyle.secondary)
        cancel.callback = cancel_callback
        view.add_item(confirm)
        view.add_item(cancel)

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(BackfillUnverifiedCog(bot))
