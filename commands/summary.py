import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import logging

logger = logging.getLogger('psn_api')

class SummaryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def generate_progress_bar(self, percentage: int, length: int = 10) -> str:
        if percentage < 0 or percentage > 100:
            return "Invalid progress"
        filled = int(length * (percentage / 100))
        half = 1 if (length * (percentage / 100) - filled) >= 0.5 else 0
        empty = length - filled - half
        return '[' + '▓' * filled + '▒' * half + '░' * empty + f'] {percentage}%'
    
    def format_number(self, number: int) -> str:
        return f"{number:,}" if number is not None else "0"
    
    @app_commands.command(name='summary', description='View your or another user\'s trophy progress.')
    @app_commands.describe(user='Optional: Mention a user to view their trophies. Leave blank to view your own.')
    async def trophies(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        discord_id = str(target.id)

        async with aiohttp.ClientSession() as session:
            headers = {'Authorization': f"Token {self.bot.api_key}"}
            params = {'discord_id': discord_id}
            async with session.get(f"{self.bot.api_base_url}summary/", params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('linked'):
                        profile = data['profile']
                        embed = discord.Embed(title=f"{target.display_name}\'s PSN Profile Summary", color=0xFFD700)
                        if profile.get('avatar_url'):
                            embed.set_thumbnail(url=profile['avatar_url'])
                        
                        # Profile Info
                        embed.add_field(name='PSN Username', value=profile['display_psn_username'], inline=True)
                        embed.add_field(name='Account ID', value=profile['account_id'], inline=True)
                        embed.add_field(name='PS+', value='Yes' if profile['is_plus'] else 'No', inline=True)

                        progress_bar = self.generate_progress_bar(profile['progress'])
                        embed.add_field(name='Level & Progress', value=f"Level {profile['trophy_level']}\n{progress_bar} to {int(profile['trophy_level']) + 1}", inline=True)
                        embed.add_field(name='Country', value=f"{profile['country']} {profile['flag']}", inline=True)

                        # Trophy Breakdown
                        if profile['psn_history_public']:
                            summary = profile['earned_trophy_summary']
                            embed.add_field(name='Trophy Breakdown', value=f"🏆 Platinum: {self.format_number(summary.get('platinum', 0))}\n🥇 Gold: {self.format_number(summary.get('gold', 0))}\n🥈 Silver: {self.format_number(summary.get('silver', 0))}\n🥉 Bronze: {self.format_number(summary.get('bronze', 0))}", inline=False)
                            embed.add_field(name='Total Trophies', value=f"**{self.format_number(profile['total_trophies'])}**", inline=True)
                            embed.add_field(name='Games Played', value=self.format_number(profile['total_games']), inline=True)
                        else:
                            embed.add_field(name="⚠️ Warning", value="Incorrect PSN permissions. Set 'Gaming History' to 'Anyone' for full stats.", inline=False)
                        
                        # Rarest Trophies
                        rarest = profile['rarest_trophies']
                        rarest_str = '\n'.join([f"🌟 {t['earn_rate']}% - {t['name']} ({t['game']})" for t in rarest]) or "None yet. Keep pursuing!"
                        embed.add_field(name='Rarest Trophies', value=rarest_str, inline=False)

                        # Recent Platinums
                        platinums = profile['recent_platinums']
                        platinums_str = "\n".join([f"🏆 {p['name']} ({p['game']}) - {p['earned_date']}" for p in platinums]) or "None yet. Your first plat awaits!"
                        embed.add_field(name='Recent Platinums', value=platinums_str, inline=False)
                        
                        embed.add_field(name="Status", value="Verified" if profile['is_verified'] else "Unverified. Use /link to verify.", inline=False)

                        embed.set_footer(text=f"Last synced: {profile['last_synced']}") 

                        view = discord.ui.View(timeout=900)

                        async def publish_callback(interaction: discord.Interaction):
                            try:
                                public_embed = embed.copy()
                                public_embed.set_footer(text=f"Shared by {interaction.user.display_name} | Last synced: {profile['last_synced']}")
                                await interaction.channel.send(embed=public_embed)
                                await interaction.response.edit_message(content='Published to channel!', view=None)
                                logger.info(f"User {discord_id} published profile summary to channel {interaction.channel_id}")
                            except discord.Forbidden:
                                await interaction.response.edit_message(content='Publish failed (bot lacks permissions). Contact admin.', view=None)
                            except Exception as e:
                                logger.error(f"Publish error: {e}")
                                await interaction.response.edit_message(content='Publish failed. Try again later or contact an admin.', view=None)
                        
                        button = discord.ui.Button(label='Publish to Channel', style=discord.ButtonStyle.success)
                        button.callback = publish_callback
                        view.add_item(button)
                        
                        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                    else:
                        await interaction.followup.send('No PSN linked. User /register first!', ephemeral=True)
                else:
                    error_text = await resp.text()
                    print(error_text)
                    logger.error(f"API error: {resp.status} - {error_text}")
                    await interaction.followup.send('API error. Please try again later.', ephemeral=True)

async def setup(bot):
    await bot.add_cog(SummaryCog(bot))