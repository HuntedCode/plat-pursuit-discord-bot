import discord
from discord.ext import commands
import os
import logging

logger = logging.getLogger('psn_api')

MAX_FIELD_LENGTH = 1024


class AuditLogCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.audit_log_channel_id = int(os.getenv('AUDIT_LOG_CHANNEL_ID', 0))

    def _get_audit_channel(self) -> discord.TextChannel | None:
        if self.audit_log_channel_id == 0:
            return None
        channel = self.bot.get_channel(self.audit_log_channel_id)
        if not channel:
            logger.warning(f"Audit log channel ID {self.audit_log_channel_id} not found.")
        return channel

    def _format_duration(self, delta) -> str:
        total_seconds = int(delta.total_seconds())
        if total_seconds <= 0:
            return "Just now"

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600

        if days >= 365:
            years = days // 365
            remaining_days = days % 365
            months = remaining_days // 30
            if months > 0:
                return f"{years}y {months}mo"
            return f"{years}y {remaining_days}d"
        elif days >= 30:
            months = days // 30
            remaining_days = days % 30
            return f"{months}mo {remaining_days}d"
        elif days > 0:
            return f"{days}d {hours}h"
        else:
            return f"{hours}h" if hours > 0 else "<1h"

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        channel = self._get_audit_channel()
        if not channel:
            return

        try:
            now = discord.utils.utcnow()
            account_age = self._format_duration(now - member.created_at)

            embed = discord.Embed(
                title="Member Joined",
                color=0x00ff00,
                timestamp=now,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="User", value=f"{member.mention} ({member.name})", inline=True)
            embed.add_field(name="User ID", value=str(member.id), inline=True)
            embed.add_field(
                name="Account Created",
                value=f"{discord.utils.format_dt(member.created_at, 'R')} ({account_age})",
                inline=False,
            )
            embed.add_field(name="Member Count", value=str(member.guild.member_count), inline=True)
            embed.set_footer(text="No Trophy Can Hide From Us \U0001f3c6")

            await channel.send(embed=embed)
            logger.info(f"Logged join for {member.id} ({member.name})")
        except discord.Forbidden:
            logger.error(f"Missing permissions to send in audit log channel {channel.id}")
        except Exception as e:
            logger.error(f"Audit log join error for {member.id}: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return

        channel = self._get_audit_channel()
        if not channel:
            return

        try:
            now = discord.utils.utcnow()

            membership_duration = "Unknown"
            joined_value = "Unknown"
            if member.joined_at:
                membership_duration = self._format_duration(now - member.joined_at)
                joined_value = f"{discord.utils.format_dt(member.joined_at, 'R')} ({membership_duration})"

            roles = [role.mention for role in member.roles if role != member.guild.default_role]
            if not roles:
                roles_str = "None"
            else:
                truncated = []
                current_length = 0
                for i, role in enumerate(roles):
                    separator = ", " if truncated else ""
                    remaining = len(roles) - i
                    suffix = f", ... +{remaining} more"
                    needed = len(separator) + len(role)
                    if current_length + needed + len(suffix) > MAX_FIELD_LENGTH:
                        truncated.append(f"... +{remaining} more")
                        break
                    truncated.append(role)
                    current_length += needed
                roles_str = ", ".join(truncated)

            embed = discord.Embed(
                title="Member Left",
                color=0xff0000,
                timestamp=now,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="User", value=f"{member.mention} ({member.name})", inline=True)
            embed.add_field(name="User ID", value=str(member.id), inline=True)
            embed.add_field(name="Joined Server", value=joined_value, inline=False)
            embed.add_field(name="Roles", value=roles_str, inline=False)
            embed.add_field(name="Member Count", value=str(member.guild.member_count), inline=True)
            embed.set_footer(text="No Trophy Can Hide From Us \U0001f3c6")

            await channel.send(embed=embed)
            logger.info(f"Logged leave for {member.id} ({member.name})")
        except discord.Forbidden:
            logger.error(f"Missing permissions to send in audit log channel {channel.id}")
        except Exception as e:
            logger.error(f"Audit log leave error for {member.id}: {e}")


async def setup(bot):
    await bot.add_cog(AuditLogCog(bot))
