import discord
from discord.ext import commands
import os
import logging
import asyncio

logger = logging.getLogger('psn_api')

class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.welcome_channel_id = int(os.getenv('WELCOME_CHANNEL_ID', 0))
        self.enable_pings = os.getenv('ENABLE_WELCOME_PINGS', 'True').lower() == 'true'
        self.welcome_delay_seconds = int(os.getenv('WELCOME_DELAY_SECONDS', 8))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or self.welcome_channel_id == 0:
            return
        
        channel = self.bot.get_channel(self.welcome_channel_id)
        if not channel:
            logger.warning(f"Welcome channel ID {self.welcome_channel_id} not found.")
            return
        
        try:
            await asyncio.sleep(self.welcome_delay_seconds)
            mention = f"<@{member.id}>" if self.enable_pings else member.display_name
            embed = discord.Embed(title=f"Welcome to Plat Pursuit! {self.bot.plat_pursuit_emoji}", color=0x00ff00, description=f"Hey {mention}! It's good to see you! Follow the instructions below to get the most out of our services.")
            embed.add_field(
                name='Getting Started',
                value=f"1. Use **/link <psn_username>** to connect and sync your PSN profile to our service.\n2. Add the generated code to your PSN **'About Me'** and click **Verify Now**.\n3. Once verified you'll have full access to our server and state of the art Discord bot! Feel free to use the various slash commands.\nQuestions? Ping a moderator! {self.bot.plat_pursuit_emoji} {self.bot.platinum_emoji}",
                inline=False,
            )
            embed.set_footer(text=f"Happy pursuing! 🏆 | Powered by Plat Pursuit")

            await channel.send(content=f"Welcome {mention}!", embed=embed)
            logger.info(f"Sent welcome to {member.id} in channel {channel.id}")
        except discord.Forbidden:
            logger.error(f"Bot lacks permissions to send in welcome channel {channel.id}")
        except Exception as e:
            logger.error(f"Welcome error for {member.id}: {e}")

async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))