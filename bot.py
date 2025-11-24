import discord
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

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} has connected to Discord! Ready to Pursuit Plats!")

@bot.command(name='ping')
async def ping(ctx):
    await ctx.send('Pong! Bot is online.')

async def main():
    async with bot:
        await bot.load_extension('commands.register')
        await bot.load_extension('commands.trophies')
        await bot.start(TOKEN)

asyncio.run(main())