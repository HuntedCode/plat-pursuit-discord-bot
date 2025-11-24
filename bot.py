import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import os
import aiohttp
import asyncio

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
API_BASE_URL = os.getenv('API_BASE_URL')
API_KEY = os.getenv('API_KEY')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='__', intents=intents)

bot.api_base_url = API_BASE_URL
bot.api_key = API_KEY

@bot.event
async def on_ready():
    print(f"{bot.user} has connected to Discord! Ready to Pursuit Plats!")
    # Sync slash commands. Run only once per major change - keep commented out for dev
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.tree.command(name='ping', description='Test bot responsiveness.')
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message('Pong! Bot is online.')

async def main():
    async with bot:
        try:
            await bot.load_extension('commands.register')
            await bot.load_extension('commands.trophies')
            await bot.start(TOKEN)
        except Exception as e:
            print(f"Error loading extensions: {e}")

asyncio.run(main())