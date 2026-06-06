import discord
from discord import app_commands
from discord.ext import commands
import os
import io
import logging

logger = logging.getLogger('psn_api')

# Discord only accepts 60, 1440, 4320, or 10080 for auto-archive. 7 days keeps
# slow-moving mod conversations from archiving mid-thread.
TICKET_AUTO_ARCHIVE_MINUTES = 10080

OPEN_CUSTOM_ID = 'ticket:open'
CLOSE_CUSTOM_ID = 'ticket:close'

# Thread-name prefixes distinguish self-serve tickets from mod-initiated ones so
# the self-serve duplicate guard never matches a thread a user was pulled into.
SELF_PREFIX = 'ticket-'
MOD_PREFIX = 'modticket-'


class TicketPanelView(discord.ui.View):
    """Persistent panel posted in the support channel. Re-registered on startup."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label='Open a Ticket',
        style=discord.ButtonStyle.primary,
        emoji='🎫',
        custom_id=OPEN_CUSTOM_ID,
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog('TicketsCog')
        if not cog:
            await interaction.response.send_message('Ticket system is unavailable right now.', ephemeral=True)
            return
        await cog.handle_open(interaction)


class TicketControlView(discord.ui.View):
    """Persistent controls posted inside each ticket thread."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label='Close Ticket',
        style=discord.ButtonStyle.danger,
        emoji='🔒',
        custom_id=CLOSE_CUSTOM_ID,
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog('TicketsCog')
        if not cog:
            await interaction.response.send_message('Ticket system is unavailable right now.', ephemeral=True)
            return
        await cog.handle_close(interaction)


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.enabled = os.getenv('ENABLE_TICKETS', 'False').lower() == 'true'
        self.channel_id = int(os.getenv('TICKET_CHANNEL_ID', 0))
        self.mod_role_id = int(os.getenv('TICKET_MOD_ROLE_ID', 0))
        self.log_channel_id = int(os.getenv('TICKET_LOG_CHANNEL_ID', 0))

    async def cog_load(self):
        # Register persistent views so the panel and close buttons survive restarts.
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketControlView())

    def _is_mod(self, member: discord.Member) -> bool:
        if member.guild_permissions.manage_messages:
            return True
        if self.mod_role_id and any(r.id == self.mod_role_id for r in member.roles):
            return True
        return False

    def _is_ticket_thread(self, channel) -> bool:
        return (
            isinstance(channel, discord.Thread)
            and channel.parent_id == self.channel_id
            and channel.type == discord.ChannelType.private_thread
        )

    def _mod_mention(self) -> str:
        return f"<@&{self.mod_role_id}>" if self.mod_role_id else 'Moderators'

    @staticmethod
    def _self_ticket_name(user) -> str:
        # Owner id is appended so the duplicate guard can identify the opener by name.
        return f"{SELF_PREFIX}{user.name}-{user.id}"

    def _mod_members(self, guild):
        """Members holding the mod role. A role @mention does not notify non-members of a
        private thread, so mods must be added as thread members to be pinged and see it."""
        if not self.mod_role_id or guild is None:
            return []
        role = guild.get_role(self.mod_role_id)
        return [m for m in role.members if not m.bot] if role else []

    def _resolve_channel(self):
        """Return the configured ticket channel, or None if missing/misconfigured."""
        channel = self.bot.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.error(f"Ticket channel {self.channel_id} not found or not a text channel.")
            return None
        return channel

    def _find_open_ticket(self, channel: discord.TextChannel, user_id: int):
        """Return the user's existing open *self-serve* ticket, or None. No DB.

        Matches on the owner id encoded in the thread name (see _self_ticket_name) rather than
        membership: mods are members of every ticket, so membership can't identify the owner.
        Only self-serve threads count, so a user pulled into a mod ticket can still open their own.
        """
        suffix = f"-{user_id}"
        for thread in channel.threads:
            if thread.archived or thread.type != discord.ChannelType.private_thread:
                continue
            if thread.name.startswith(SELF_PREFIX) and thread.name.endswith(suffix):
                return thread
        return None

    async def _create_ticket(self, channel, name, members, embed, opened_by):
        """Create a private ticket thread, pull in members, and post the starter message.

        Returns (thread, failed_members). Raises discord.Forbidden / HTTPException if the
        thread itself cannot be created; adding individual members is best-effort.
        """
        thread = await channel.create_thread(
            name=name,
            type=discord.ChannelType.private_thread,
            invitable=False,
            auto_archive_duration=TICKET_AUTO_ARCHIVE_MINUTES,
            reason=f"Ticket opened by {opened_by} ({opened_by.id})",
        )

        # Add the requested members (tracking failures to report back), then silently add the
        # mod team so the role @mention below actually reaches them and the thread shows in
        # their sidebar. Both are best-effort; a failed add never aborts the ticket.
        requested_ids = {m.id for m in members}
        added = []
        failed = []
        for member in members:
            try:
                await thread.add_user(member)
                added.append(member)
            except discord.HTTPException as e:
                logger.warning(f"Failed to add {member.id} to ticket {thread.id}: {e}")
                failed.append(member)

        for mod in self._mod_members(channel.guild):
            if mod.id in requested_ids:
                continue
            try:
                await thread.add_user(mod)
            except discord.HTTPException as e:
                logger.warning(f"Failed to add mod {mod.id} to ticket {thread.id}: {e}")

        content = self._mod_mention()
        if added:
            content += f" | {' '.join(m.mention for m in added)}"
        await thread.send(
            content=content,
            embed=embed,
            view=TicketControlView(),
            allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False),
        )
        return thread, failed

    async def handle_open(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not self.enabled:
            await interaction.followup.send('The ticket system is currently disabled.', ephemeral=True)
            return

        if not self.channel_id:
            logger.warning('Ticket open attempted but TICKET_CHANNEL_ID is not set.')
            await interaction.followup.send('The ticket system is not configured. Please contact an admin.', ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.followup.send('Tickets can only be opened from within the server.', ephemeral=True)
            return

        channel = self._resolve_channel()
        if channel is None:
            await interaction.followup.send('The ticket system is misconfigured. Please contact an admin.', ephemeral=True)
            return

        existing = self._find_open_ticket(channel, interaction.user.id)
        if existing:
            await interaction.followup.send(f"You already have an open ticket: {existing.mention}", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Support Ticket {self.bot.plat_pursuit_emoji}",
            color=0x00ff00,
            description=(
                f"Thanks for reaching out, {interaction.user.mention}! A moderator will be with you shortly.\n\n"
                "Please describe your question or issue in as much detail as you can. "
                "Everything here is private between you and the staff team."
            ),
        )
        embed.set_footer(text='A moderator will close this ticket once it is resolved.')

        try:
            thread, _ = await self._create_ticket(
                channel,
                name=self._self_ticket_name(interaction.user),
                members=[interaction.user],
                embed=embed,
                opened_by=interaction.user,
            )
        except discord.Forbidden:
            logger.error('Bot lacks permission to create private threads in the ticket channel.')
            await interaction.followup.send('I lack permission to open a ticket. Please contact an admin.', ephemeral=True)
            return
        except discord.HTTPException as e:
            logger.error(f"Failed to create ticket thread: {e}")
            await interaction.followup.send('Could not open a ticket right now. Please try again later.', ephemeral=True)
            return

        await interaction.followup.send(f"Your ticket has been created: {thread.mention}", ephemeral=True)
        logger.info(f"Ticket opened by {interaction.user.id} -> thread {thread.id}")

    async def handle_close(self, interaction: discord.Interaction):
        if not self._is_ticket_thread(interaction.channel):
            await interaction.response.send_message('This can only be used inside a ticket thread.', ephemeral=True)
            return

        if not self._is_mod(interaction.user):
            await interaction.response.send_message('Only moderators can close tickets.', ephemeral=True)
            return

        await interaction.response.defer()
        thread = interaction.channel

        await self._post_transcript(thread, interaction.user)

        await thread.send(
            embed=discord.Embed(
                title='Ticket Closed',
                color=0xff5555,
                description=f"This ticket was closed by {interaction.user.mention}. The thread is now locked.",
            )
        )

        try:
            await thread.edit(archived=True, locked=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to archive/lock ticket thread {thread.id}: {e}")

        logger.info(f"Ticket {thread.id} closed by {interaction.user.id}")

    async def _post_transcript(self, thread: discord.Thread, closer: discord.Member):
        if not self.log_channel_id:
            return

        log_channel = self.bot.get_channel(self.log_channel_id)
        if not isinstance(log_channel, (discord.TextChannel, discord.Thread)):
            logger.warning(f"Ticket log channel {self.log_channel_id} not found; skipping transcript.")
            return

        lines = []
        try:
            async for message in thread.history(limit=None, oldest_first=True):
                stamp = message.created_at.strftime('%Y-%m-%d %H:%M UTC')
                author = f"{message.author} ({message.author.id})"
                content = message.content or ''
                if message.attachments:
                    attachment_urls = ' '.join(a.url for a in message.attachments)
                    content = f"{content} {attachment_urls}".strip()
                if message.embeds and not content:
                    content = '[embed]'
                lines.append(f"[{stamp}] {author}: {content}")
        except discord.HTTPException as e:
            logger.error(f"Failed to read history for ticket {thread.id}: {e}")
            return

        transcript = '\n'.join(lines) if lines else 'No messages.'
        buffer = io.BytesIO(transcript.encode('utf-8'))
        file = discord.File(buffer, filename=f"{thread.name}-{thread.id}.txt")

        embed = discord.Embed(
            title='Ticket Transcript',
            color=0x5865F2,
            description=f"**Thread:** {thread.name} (`{thread.id}`)\n**Closed by:** {closer.mention}\n**Messages:** {len(lines)}",
        )
        try:
            await log_channel.send(embed=embed, file=file)
        except discord.HTTPException as e:
            logger.error(f"Failed to post transcript for ticket {thread.id}: {e}")

    @app_commands.command(name='ticket', description='Open a private support ticket with the moderators.')
    async def ticket(self, interaction: discord.Interaction):
        await self.handle_open(interaction)

    @app_commands.command(name='ticket_user', description='MODERATOR ONLY: Open a private ticket and pull a user (or up to 3) into it.')
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(
        user='The user to pull into the ticket.',
        reason='Optional reason shown in the ticket.',
        user2='Optional second user to pull in.',
        user3='Optional third user to pull in.',
    )
    async def ticket_user(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
        user2: discord.Member | None = None,
        user3: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not self.enabled:
            await interaction.followup.send('The ticket system is currently disabled.', ephemeral=True)
            return

        if not self.channel_id:
            await interaction.followup.send('TICKET_CHANNEL_ID is not set.', ephemeral=True)
            return

        channel = self._resolve_channel()
        if channel is None:
            await interaction.followup.send('The ticket system is misconfigured. Please contact an admin.', ephemeral=True)
            return

        # Dedupe by id, preserve order, drop bots.
        targets = []
        seen = set()
        for member in (user, user2, user3):
            if member is None or member.bot or member.id in seen:
                continue
            seen.add(member.id)
            targets.append(member)

        if not targets:
            await interaction.followup.send('No valid users to add (bots cannot be pulled into tickets).', ephemeral=True)
            return

        target_mentions = ', '.join(m.mention for m in targets)
        embed = discord.Embed(
            title=f"Moderation Ticket {self.bot.plat_pursuit_emoji}",
            color=0xFFA500,
            description=(
                f"{interaction.user.mention} (moderator) opened this ticket and pulled in {target_mentions}.\n\n"
                "This is a private conversation with the staff team."
            ),
        )
        if reason:
            embed.add_field(name='Reason', value=reason, inline=False)
        embed.set_footer(text='A moderator will close this ticket once it is resolved.')

        try:
            thread, failed = await self._create_ticket(
                channel,
                name=f"{MOD_PREFIX}{user.name}",
                members=targets,
                embed=embed,
                opened_by=interaction.user,
            )
        except discord.Forbidden:
            logger.error('Bot lacks permission to create private threads in the ticket channel.')
            await interaction.followup.send('I lack permission to open a ticket. Check my thread permissions.', ephemeral=True)
            return
        except discord.HTTPException as e:
            logger.error(f"Failed to create mod ticket thread: {e}")
            await interaction.followup.send('Could not open a ticket right now. Please try again later.', ephemeral=True)
            return

        msg = f"Ticket created: {thread.mention}"
        if failed:
            msg += f"\nCould not add: {', '.join(m.mention for m in failed)}"
        await interaction.followup.send(msg, ephemeral=True)
        logger.info(f"Mod ticket opened by {interaction.user.id} for {[m.id for m in targets]} -> thread {thread.id}")

    @app_commands.command(name='ticket_panel', description='MODERATOR ONLY: Post the ticket panel in the support channel.')
    @app_commands.default_permissions(manage_messages=True)
    async def ticket_panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not self.channel_id:
            await interaction.followup.send('TICKET_CHANNEL_ID is not set.', ephemeral=True)
            return

        channel = self._resolve_channel()
        if channel is None:
            await interaction.followup.send('The configured ticket channel could not be found.', ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Need Help? Open a Ticket {self.bot.plat_pursuit_emoji}",
            color=0x00ff00,
            description=(
                "Click the button below to open a **private** ticket with the moderation team.\n\n"
                "Your ticket is visible only to you and the staff. Please keep mod conversations "
                "here in the server instead of DMs so the whole team can help."
            ),
        )
        embed.set_footer(text='You can also use /ticket anywhere in the server.')

        try:
            await channel.send(embed=embed, view=TicketPanelView())
        except discord.Forbidden:
            await interaction.followup.send('I lack permission to post in the ticket channel.', ephemeral=True)
            return

        await interaction.followup.send(f"Ticket panel posted in {channel.mention}.", ephemeral=True)
        logger.info(f"Ticket panel posted in {channel.id} by {interaction.user.id}")


async def setup(bot):
    await bot.add_cog(TicketsCog(bot))
