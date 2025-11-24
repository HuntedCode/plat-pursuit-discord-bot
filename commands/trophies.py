
import discord
from discord.ext import commands

class TrophiesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name='trophies')
    async def trophies(self, ctx, user: discord.Member = None):
        target = user or ctx.author
        await ctx.send(f"Fetching trophies for {target.display_name}... (API integration coming soon)")

async def setup(bot):
    await bot.add_cog(TrophiesCog(bot))