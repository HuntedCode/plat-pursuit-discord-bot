import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import asyncio
import logging
import argparse

logging.basicConfig(level=logging.INFO)  # Set to DEBUG for verbose dev
logger = logging.getLogger(__name__)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
API_BASE_URL = os.getenv('API_BASE_URL')
API_KEY = os.getenv('API_KEY')

PLAT_PURSUIT_EMOJI_ID = os.getenv('PLAT_PURSUIT_EMOJI_ID')
PLATINUM_EMOJI_ID = os.getenv('PLATINUM_EMOJI_ID')
GOLD_EMOJI_ID = os.getenv('GOLD_EMOJI_ID')
SILVER_EMOJI_ID = os.getenv('SILVER_EMOJI_ID')
BRONZE_EMOJI_ID = os.getenv('BRONZE_EMOJI_ID')

parser = argparse.ArgumentParser()
parser.add_argument('--sync_commands', action='store_true', help='Global sync bot commands.')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='__', intents=intents)

bot.api_base_url = API_BASE_URL
bot.api_key = API_KEY
bot.verified_role_id = int(os.getenv('VERIFIED_ROLE_ID', 0))

bot.plat_pursuit_emoji = f"<:PlatPursuit:{PLAT_PURSUIT_EMOJI_ID}>" if PLAT_PURSUIT_EMOJI_ID else "🏆"
bot.platinum_emoji = f"<:Platinum_Trophy:{PLATINUM_EMOJI_ID}>" if PLATINUM_EMOJI_ID else "🏆"
bot.gold_emoji = f"<:Gold_Trophy:{GOLD_EMOJI_ID}>" if GOLD_EMOJI_ID else "🥇"
bot.silver_emoji = f"<:Silver_Trophy:{SILVER_EMOJI_ID}>" if SILVER_EMOJI_ID else "🥈"
bot.bronze_emoji = f"<:Bronze_Trophy:{BRONZE_EMOJI_ID}>" if BRONZE_EMOJI_ID else "🥉"


@bot.event
async def on_ready():
    logger.info(f"{bot.user} has connected to Discord! Ready to Pursue Plats!")
    args = parser.parse_args()
    try:
        if args.sync_commands: 
            synced = await bot.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.tree.command(name='ping', description='Test bot responsiveness.')
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message('Pong! Bot is online.', ephemeral=True)

async def main():
    async with bot:
        extensions = [
            'commands.link',
            'commands.unlink',
            'commands.refresh',
            'commands.summary',
            ]
        for ext in extensions:
            try:
                await bot.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                logger.error(f"Error to load extension {ext}: {e}")
        await bot.start(TOKEN)

asyncio.run(main())