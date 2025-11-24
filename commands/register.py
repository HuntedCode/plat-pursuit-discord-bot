from discord.ext import commands

class RegisterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name='register')
    async def register(self, ctx, psn_username: str):
        await ctx.send(f"Registering {psn_username}... (API integration coming soon)")

async def setup(bot):
    await bot.add_cog(RegisterCog(bot))