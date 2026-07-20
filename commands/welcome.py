import discord
from discord.ext import commands
import os
import logging
import asyncio

logger = logging.getLogger('psn_api')

FOOTER = 'Happy pursuing! 🏆 | No Trophy Can Hide From Us'

# PSN names are short; the cap only guards against a junk value reaching a public embed.
MAX_PSN_DISPLAY_LENGTH = 32

class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.welcome_channel_id = int(os.getenv('WELCOME_CHANNEL_ID') or 0)
        self.verified_welcome_channel_id = int(os.getenv('VERIFIED_WELCOME_CHANNEL_ID') or 0)
        self.enable_pings = os.getenv('ENABLE_WELCOME_PINGS', 'True').lower() == 'true'
        self.welcome_delay_seconds = int(os.getenv('WELCOME_DELAY_SECONDS', 8))
        # Verifying again (double-clicked button, or a re-link) must not re-announce
        # someone to the whole channel. Process-lifetime only, which is enough: a
        # restart between two verifications by the same member is not worth persisting.
        self.announced_member_ids: set[int] = set()

    def _mention(self, member: discord.Member) -> str:
        """How to address a member, honouring ENABLE_WELCOME_PINGS."""
        return f"<@{member.id}>" if self.enable_pings else member.display_name

    def _channel(self, channel_id: int, label: str):
        """Resolve a configured channel, logging (and returning None) if unusable."""
        if channel_id == 0:
            return None
        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"{label} channel ID {channel_id} not found.")
            return None
        return channel

    async def _post(self, channel, member: discord.Member, embed: discord.Embed, label: str):
        """Send a welcome embed. Never raises: a failed welcome must not break the caller."""
        embed.set_footer(text=FOOTER)
        try:
            await channel.send(content=f"Welcome {self._mention(member)}!", embed=embed)
            logger.info(f"Sent {label} for {member.id} in channel {channel.id}")
        except discord.Forbidden:
            logger.error(f"Bot lacks permissions to send in {label} channel {channel.id}")
        except Exception as e:
            logger.error(f"{label} error for {member.id}: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        channel = self._channel(self.welcome_channel_id, 'Welcome')
        if not channel:
            return

        await asyncio.sleep(self.welcome_delay_seconds)
        mention = self._mention(member)
        embed = discord.Embed(title=f"Welcome to Plat Pursuit! {self.bot.plat_pursuit_emoji}", color=0x00ff00, description=f"Hey {mention}! It's good to see you! Follow the instructions below to get the most out of our services.")
        embed.add_field(
            name='Getting Started',
            value=f"1. Use **/link <psn_username>** to connect and sync your PSN profile to our service.\n2. Add the generated code to your PSN **'About Me'** and click **Verify Now**.\n3. Once verified you'll have full access to our server and state of the art Discord bot! Feel free to use the various slash commands.\n4. Visit our website! https://www.platpursuit.com/\nQuestions? Ping a moderator! {self.bot.plat_pursuit_emoji} {self.bot.platinum_emoji}",
            inline=False,
        )
        await self._post(channel, member, embed, 'welcome')

    @commands.Cog.listener()
    async def on_psn_verified(self, member: discord.Member, psn_username: str):
        """Announce a newly verified member. Dispatched by LinkCog after verification."""
        channel = self._channel(self.verified_welcome_channel_id, 'Verified welcome')
        if not channel or member.id in self.announced_member_ids:
            return
        self.announced_member_ids.add(member.id)

        # The PSN name is user-supplied, so escape it: markdown in a public embed would
        # otherwise let it mangle or impersonate the rest of the announcement.
        display_psn = discord.utils.escape_markdown(psn_username[:MAX_PSN_DISPLAY_LENGTH])
        embed = discord.Embed(
            title=f"A New Pursuer Has Arrived! {self.bot.plat_pursuit_emoji}",
            color=0x00ff00,
            description=f"{self._mention(member)} just verified as **{display_psn}** and is officially part of the hunt!",
        )
        embed.add_field(
            name="Now that you're verified",
            value=(
                f"{self.bot.platinum_emoji} **/summary** - your trophy progress at a glance\n"
                f"{self.bot.gold_emoji} **/refresh** - pull in your latest trophies (once per hour)\n"
                f"{self.bot.silver_emoji} **/trophystats** - see what the community earned today\n\n"
                'Say hi and tell us what you are chasing right now!'
            ),
            inline=False,
        )
        await self._post(channel, member, embed, 'verified welcome')

async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))
