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

parser = argparse.ArgumentParser()
parser.add_argument('--sync_commands', action='store_true', help='Global sync bot commands.')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='__', intents=intents)

bot.api_base_url = API_BASE_URL
bot.api_key = API_KEY
bot.verified_role_id = int(os.getenv('VERIFIED_ROLE_ID', 0))


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