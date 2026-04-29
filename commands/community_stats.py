import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging
import time

from utils.formatting import format_number

logger = logging.getLogger('psn_api')

EMBED_COLOR_DEFAULT = 0x003791  # PlatPursuit brand blue
EMBED_COLOR_RECORD = 0xFFD700   # Gold (matches the daily post on record-setting days)

ET_ZONE = ZoneInfo("America/New_York")

COMMAND_COOLDOWN_RATE = 3
COMMAND_COOLDOWN_PER_SECONDS = 30.0
PUBLISH_COOLDOWN_SECONDS = 60
PUBLISH_VIEW_TIMEOUT_SECONDS = 900


class CommunityStatsCog(commands.Cog):
    trophystats = app_commands.Group(
        name='trophystats',
        description='Community trophy stats from PlatPursuit.',
    )

    def __init__(self, bot):
        self.bot = bot
        self._last_publish_at: dict[int, float] = {}

    async def _get_json(self, path: str) -> tuple[dict | None, str | None]:
        url = f"{self.bot.api_base_url}{path}"
        try:
            async with self.bot.api_session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json(), None
                if resp.status == 404:
                    return None, "No community stats data found for that day yet. Check back soon!"
                text = await resp.text()
                logger.error(f"Community-stats API error {resp.status} on {url}: {text}")
                return None, "Couldn't reach the PlatPursuit API right now. Try again in a moment."
        except Exception:
            logger.exception(f"Community-stats API exception on {url}")
            return None, "Couldn't reach the PlatPursuit API right now. Try again in a moment."

    def _build_day_embed(self, data: dict, title: str, footer: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"{title} ({data['date']})",
            color=EMBED_COLOR_DEFAULT,
        )
        embed.add_field(name="🏆 Trophies", value=format_number(data.get('total_trophies')), inline=True)
        embed.add_field(name=f"{self.bot.platinum_emoji} Platinums", value=format_number(data.get('total_platinums')), inline=True)
        embed.add_field(name="🌟 Ultra Rares", value=format_number(data.get('total_ultra_rares')), inline=True)
        embed.add_field(name="📊 PP Score", value=format_number(data.get('pp_score')), inline=False)
        embed.set_footer(text=footer)
        return embed

    def _build_publish_view(self, invoker_id: int, embed: discord.Embed) -> discord.ui.View:
        view = discord.ui.View(timeout=PUBLISH_VIEW_TIMEOUT_SECONDS)

        async def publish_callback(btn_interaction: discord.Interaction):
            if btn_interaction.user.id != invoker_id:
                await btn_interaction.response.send_message(
                    "Only the person who ran the command can publish it.",
                    ephemeral=True,
                )
                return

            now = time.monotonic()
            last = self._last_publish_at.get(invoker_id, 0.0)
            elapsed = now - last
            if elapsed < PUBLISH_COOLDOWN_SECONDS:
                wait = int(PUBLISH_COOLDOWN_SECONDS - elapsed) + 1
                await btn_interaction.response.send_message(
                    f"Slow down! You can publish another community stat in {wait}s.",
                    ephemeral=True,
                )
                return

            try:
                public_embed = embed.copy()
                existing_footer = embed.footer.text or ''
                shared_by = f"Shared by {btn_interaction.user.display_name}"
                public_embed.set_footer(
                    text=f"{shared_by} | {existing_footer}" if existing_footer else shared_by
                )
                await btn_interaction.response.edit_message(content='Published to channel!', view=None)
                await btn_interaction.channel.send(embed=public_embed)
                self._last_publish_at[invoker_id] = now
                logger.info(
                    f"User {invoker_id} published community stats to channel {btn_interaction.channel_id}"
                )
            except discord.Forbidden:
                await btn_interaction.response.edit_message(
                    content='Publish failed (bot lacks permissions). Contact admin.',
                    view=None,
                )
            except Exception as e:
                logger.error(f"Community-stats publish error: {e}")
                await btn_interaction.response.edit_message(
                    content='Publish failed. Try again later or contact an admin.',
                    view=None,
                )

        button = discord.ui.Button(label='Publish to Channel', style=discord.ButtonStyle.success)
        button.callback = publish_callback
        view.add_item(button)
        return view

    @trophystats.command(name='today', description="Today's community trophy totals so far.")
    @app_commands.checks.cooldown(COMMAND_COOLDOWN_RATE, COMMAND_COOLDOWN_PER_SECONDS, key=lambda i: i.user.id)
    async def today(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data, err = await self._get_json("community-stats/today/")
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        footer = data.get('data_freshness_note') or 'Live community stats.'
        embed = self._build_day_embed(data, "Today's Community Trophy Totals", footer)
        view = self._build_publish_view(interaction.user.id, embed)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @trophystats.command(name='yesterday', description="Yesterday's final community trophy totals.")
    @app_commands.checks.cooldown(COMMAND_COOLDOWN_RATE, COMMAND_COOLDOWN_PER_SECONDS, key=lambda i: i.user.id)
    async def yesterday(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        target_date = (datetime.now(ET_ZONE) - timedelta(days=1)).date().isoformat()
        data, err = await self._get_json(f"community-stats/{target_date}/")
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        footer = "PP Score = trophies + 5×plats + 3×URs"
        embed = self._build_day_embed(data, "Yesterday's Community Trophy Totals", footer)
        view = self._build_publish_view(interaction.user.id, embed)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @trophystats.command(name='records', description='All-time community trophy records.')
    @app_commands.checks.cooldown(COMMAND_COOLDOWN_RATE, COMMAND_COOLDOWN_PER_SECONDS, key=lambda i: i.user.id)
    async def records(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data, err = await self._get_json("community-stats/records/")
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return

        embed = discord.Embed(
            title='All-Time Community Trophy Records',
            color=EMBED_COLOR_RECORD,
        )

        def record_line(record):
            if not record or record.get('value') is None:
                return '_no data yet_'
            return f"**{format_number(record['value'])}** on {record['date']}"

        embed.add_field(name='🏆 Most Trophies', value=record_line(data.get('max_trophies')), inline=False)
        embed.add_field(name=f"{self.bot.platinum_emoji} Most Platinums", value=record_line(data.get('max_platinums')), inline=False)
        embed.add_field(name='🌟 Most Ultra Rares', value=record_line(data.get('max_ultra_rares')), inline=False)
        embed.add_field(name='📊 Highest PP Score', value=record_line(data.get('max_pp_score')), inline=False)
        embed.set_footer(text='Records never reset. Every day is a chance to break one.')
        view = self._build_publish_view(interaction.user.id, embed)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            wait = int(error.retry_after) + 1
            msg = f"You're using community stats commands too fast. Try again in {wait}s."
        else:
            logger.error(f"Community-stats command error: {error}")
            msg = "Something went wrong. Please try again later."

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(CommunityStatsCog(bot))
