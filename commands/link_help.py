import discord
from discord.ext import commands
import logging

logger = logging.getLogger('psn_api')


class LinkHelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='link')
    @commands.cooldown(1, 60, commands.BucketType.channel)
    async def link_help(self, ctx: commands.Context):
        embed = discord.Embed(
            title=f"Link Your PSN Account {self.bot.plat_pursuit_emoji}",
            description="Ready to start pursuing plats? Here's how to connect your PSN account:",
            color=0x00ff00,
        )
        embed.add_field(
            name="Step 1: Start the Link",
            value="Use the **/link** slash command followed by your PSN username.\nExample: `/link psn_username:YourPSN`",
            inline=False,
        )
        embed.add_field(
            name="Step 2: Copy Your Code",
            value="You'll receive a unique verification code. Copy it!",
            inline=False,
        )
        embed.add_field(
            name="Step 3: Update Your PSN Profile",
            value=(
                "Head to your PSN **'About Me'** section and paste the code there.\n"
                "You can do this from the PlayStation app or console settings."
            ),
            inline=False,
        )
        embed.add_field(
            name="Step 4: Verify",
            value="Click the **Verify Now** button that appeared with your code. That's it!",
            inline=False,
        )
        embed.set_footer(text="No Trophy Can Hide From Us \U0001f3c6")

        try:
            await ctx.send(embed=embed)
        except discord.Forbidden:
            logger.error(f"Missing permissions to send link instructions in channel {ctx.channel.id}")
            return
        logger.info(f"Sent link instructions to channel {ctx.channel.id} (requested by {ctx.author.id})")


async def setup(bot):
    await bot.add_cog(LinkHelpCog(bot))
