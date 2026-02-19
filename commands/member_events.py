import discord
from discord.ext import commands
import os
import logging

logger = logging.getLogger('psn_api')

class MemberEventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.enable_unlink_on_leave = os.getenv('ENABLE_UNLINK_ON_LEAVE', 'True').lower() == 'true'

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot or not self.enable_unlink_on_leave:
            return

        discord_id = str(member.id)
        try:
            session = self.bot.api_session
            payload = {'discord_id': discord_id}
            async with session.post(f"{self.bot.api_base_url}unlink/", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('success'):
                        logger.info(f"Unlinked profile for leaving user {discord_id} ({member.display_name})")
                    else:
                        logger.warning(f"Unlink failed for {discord_id}: {data.get('message', 'Unknown error')}")
                else:
                    logger.error(f"API error unlinking {discord_id}: Status {resp.status}")
        except Exception as e:
            logger.error(f"Unlink error for {discord_id}: {e}")

async def setup(bot):
    await bot.add_cog(MemberEventsCog(bot))
