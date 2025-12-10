import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import asyncio
import logging
import argparse
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn

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

BOT_API_PORT = int(os.getenv('BOT_API_PORT', 5000))
BOT_API_HOST = os.getenv('BOT_API_HOST', '127.0.0.1') # localhost for dev, 0.0.0.0 for prod

parser = argparse.ArgumentParser()
parser.add_argument('--sync_commands', action='store_true', help='Global sync bot commands.')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='__', intents=intents)

bot.api_base_url = API_BASE_URL
bot.api_key = API_KEY
bot.verified_role_id = int(os.getenv('VERIFIED_ROLE_ID', 0))

bot.plat_pursuit_emoji = f"<:PlatPursuit:{PLAT_PURSUIT_EMOJI_ID}>" if PLAT_PURSUIT_EMOJI_ID else "🏆"
bot.platinum_emoji = f"<:Platinum_Trophy:{PLATINUM_EMOJI_ID}>" if PLATINUM_EMOJI_ID else "🏆"
bot.gold_emoji = f"<:Gold_Trophy:{GOLD_EMOJI_ID}>" if GOLD_EMOJI_ID else "🥇"
bot.silver_emoji = f"<:Silver_Trophy:{SILVER_EMOJI_ID}>" if SILVER_EMOJI_ID else "🥈"
bot.bronze_emoji = f"<:Bronze_Trophy:{BRONZE_EMOJI_ID}>" if BRONZE_EMOJI_ID else "🥉"

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', 0))

app = FastAPI()
security = HTTPBearer()

@app.post("/assign-role")
async def assign_role(data: dict, credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail='Invalid API key')
    
    user_id = data.get('user_id')
    role_id = data.get('role_id')
    guild_id = data.get('guild_id', GUILD_ID)

    guild = bot.get_guild(guild_id)
    if not guild:
        raise HTTPException(status_code=404, detail='Guild not found')

    member = guild.get_member(user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    
    role = guild.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail='Role not found')
    
    await member.add_roles(role)
    logger.info(f"Assigned role {role.name} to {member.name}")
    return {'status': 'success', 'message': f"Assigned role {role.name} to {member.name}"}

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

async def load_extensions():
    extensions = [
        'commands.link',
        'commands.unlink',
        'commands.refresh',
        'commands.refresh_user',
        'commands.summary',
        'commands.trophy_case',

        'commands.welcome',
        'commands.member_events',
    ]
    for ext in extensions:
        try:
            await bot.load_extension(ext)
            logger.info(f"Loaded extension: {ext}")
        except Exception as e:
            logger.error(f"Error to load extension {ext}: {e}")

async def main():
    await load_extensions()
    
    bot_task = asyncio.create_task(bot.start(TOKEN))

    config = uvicorn.Config(app=app, host=BOT_API_HOST, port=BOT_API_PORT, log_level='info')
    server = uvicorn.Server(config)
    await server.serve()

    await bot_task

if __name__ == '__main__':
    asyncio.run(main())