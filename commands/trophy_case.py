import discord
from discord import app_commands
from discord.ext import commands
import logging

from utils.api_errors import api_error_message
from utils.command_errors import ReplyOnErrorView

logger = logging.getLogger('psn_api')

class TrophyCaseCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _build_view(self, current_page:int, total_pages: int):
        view = ReplyOnErrorView(timeout=300)

        async def prev_callback(interaction: discord.Interaction):
            nonlocal current_page
            if current_page > 1:
                current_page -= 1
                new_data, error = await self.fetch_page(str(interaction.user.id), current_page)
                if new_data:
                    new_embeds = await self.build_embeds(new_data)
                    new_view = self._build_view(current_page, new_data['total_pages'])
                    await interaction.response.edit_message(embeds=new_embeds, view=new_view)
                else:
                    await interaction.response.edit_message(content=error, embeds=[], view=None)

        async def next_callback(interaction: discord.Interaction):
            nonlocal current_page
            if current_page < total_pages:
                current_page += 1
                new_data, error = await self.fetch_page(str(interaction.user.id), current_page)
                if new_data:
                    new_embeds = await self.build_embeds(new_data)
                    new_view = self._build_view(current_page, new_data['total_pages'])
                    await interaction.response.edit_message(embeds=new_embeds, view=new_view)
                else:
                    await interaction.response.edit_message(content=error, embeds=[], view=None)

        async def publish_callback(interaction: discord.Interaction):
            try:
                page_data, error = await self.fetch_page(str(interaction.user.id), current_page)
                if not page_data:
                    await interaction.response.edit_message(content=error, view=None)
                    return
                embeds = await self.build_embeds(page_data)
                await interaction.response.edit_message(content='Page published to channel!', view=None)
                await interaction.channel.send(embeds=embeds)
                logger.info(f"User {interaction.user.id} published trophy case page {current_page}")
            except discord.Forbidden:
                await interaction.response.edit_message(content='Publish failed (bot lacks permissions.)', view=None)
            except Exception as e:
                logger.error(f"Publish error: {e}")
                await interaction.response.edit_message(content='Publish failed. Please try again later.', view=None)

        prev_button = discord.ui.Button(label='Previous', style=discord.ButtonStyle.secondary, disabled=current_page == 1)
        prev_button.callback = prev_callback
        next_button = discord.ui.Button(label='Next', style=discord.ButtonStyle.secondary, disabled=current_page >= total_pages)
        next_button.callback = next_callback
        publish_button = discord.ui.Button(label='Publish Page', style=discord.ButtonStyle.success)
        publish_button.callback = publish_callback

        view.add_item(prev_button)
        view.add_item(next_button)
        view.add_item(publish_button)

        return view

    async def fetch_page(self, discord_id, current_page, per_page=4) -> tuple[dict | None, str | None]:
        """One page of the trophy case, or None and the message to show instead."""
        session = self.bot.api_session
        params = {'discord_id': discord_id, 'page': current_page, 'per_page': per_page}
        async with session.get(f"{self.bot.api_base_url}trophy-case/", params=params) as resp:
            if resp.status == 200:
                return await resp.json(), None
            return None, await api_error_message(resp, action='trophy-case')

    async def build_embeds(self, page_data):
        platinums = page_data['platinums']
        embeds = []
        if not platinums:
            embed = discord.Embed(title=f"Your Digital Trophy Case! {self.bot.plat_pursuit_emoji} {self.bot.platinum_emoji}", description='No platinums yet. Keep pursuing!', color=0xFFD700)
            embeds.append(embed)
        else:
            for index, p in enumerate(platinums):
                if index == 0:
                    embed = discord.Embed(title=f"Your Digital Trophy Case! {self.bot.plat_pursuit_emoji} {self.bot.platinum_emoji}", color=0xFFD700, url='https://www.platpursuit.com/')
                    embed.set_footer(text=f"Page {page_data['current_page']}/{page_data['total_pages']} | Total Platinums: {page_data['total_plats']}")
                else:
                    embed = discord.Embed(url='https://www.platpursuit.com/')
                if p['icon_url']:
                    embed.set_image(url=p['icon_url'])
                embeds.append(embed)

        return embeds

    @app_commands.command(name='trophy_case', description='Display your earned platinum trophies like a digital trophy case!')
    async def trophy_case(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        page = 1

        data, error = await self.fetch_page(discord_id, page)
        if data is None:
            await interaction.followup.send(error, ephemeral=True)
            return
        if not data.get('linked'):
            await interaction.followup.send(data.get('message', 'No linked profile. Use /link first!'), ephemeral=True)
            return

        embeds = await self.build_embeds(data)
        view = self._build_view(page, data['total_pages'])

        await interaction.followup.send(embeds=embeds, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(TrophyCaseCog(bot))
