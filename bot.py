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
import signal
from urllib.parse import urlparse
from aiohttp import BasicAuth, ClientSession

logging.basicConfig(level=logging.INFO)  # Set to DEBUG for verbose dev
logger = logging.getLogger(__name__)

role_queue = asyncio.Queue()

async def role_assignment_worker():
    while True:
        data = await role_queue.get()
        max_retries = 5
        retry_count = 0
        while retry_count < max_retries:
            try:
                user_id = data.get('user_id')
                role_id = data.get('role_id')
                guild_id = data.get('guild_id', GUILD_ID)

                guild = bot.get_guild(guild_id)
                if not guild:
                    logger.error(f"Guild not found: {guild_id}")
                    continue

                member = guild.get_member(user_id)
                if not member:
                    logger.error(f"Member not found: {user_id} in guild {guild_id}")
                    continue

                role = guild.get_role(role_id)
                if not role:
                    logger.error(f"Role not found: {role_id} in guild {guild_id}")
                    continue
                
                await member.add_roles(role)
                logger.info(f"Assigned role {role.name} to {member.name} in guild {guild_id}")
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    retry_after = float(e.response.headers.get('Retry-After', 1))
                    logger.warning(f"Rate limited (429) on role assignment. Retrying after {retry_after} seconds.")
                    await asyncio.sleep(retry_after + 0.5)
                    retry_count += 1
                else:
                    logger.error(f"Role assignment failed: {e}")
            except Exception as e:
                logger.error(f"Error assigning role from queue: {e}")
                await asyncio.sleep(1)
                retry_count += 1
        if retry_count >= max_retries:
            logger.error(f"Max retries exceeded for role assignment: {data}. Dropping request.")
        role_queue.task_done()

        await asyncio.sleep(0.5)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
API_BASE_URL = os.getenv('API_BASE_URL')
API_KEY = os.getenv('API_KEY')

PLAT_PURSUIT_EMOJI_ID = os.getenv('PLAT_PURSUIT_EMOJI_ID')
PLATINUM_EMOJI_ID = os.getenv('PLATINUM_EMOJI_ID')
GOLD_EMOJI_ID = os.getenv('GOLD_EMOJI_ID')
SILVER_EMOJI_ID = os.getenv('SILVER_EMOJI_ID')
BRONZE_EMOJI_ID = os.getenv('BRONZE_EMOJI_ID')

BOT_API_PORT = int(os.getenv('PORT', 5000))
BOT_API_HOST = os.getenv('BOT_API_HOST', '127.0.0.1') # localhost for dev, 0.0.0.0 for prod

PROXY_URL = os.getenv('PROXY_URL')
PROXY = None
PROXY_AUTH = None
if PROXY_URL:
    parsed_proxy = urlparse(PROXY_URL)
    PROXY = f"{parsed_proxy.scheme}://{parsed_proxy.hostname}:{parsed_proxy.port}"
    PROXY_AUTH = BasicAuth(parsed_proxy.username, parsed_proxy.password) if parsed_proxy.username and parsed_proxy.password else None

parser = argparse.ArgumentParser()
parser.add_argument('--sync_commands', action='store_true', help='Global sync bot commands.')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='__', intents=intents, proxy=PROXY, proxy_auth=PROXY_AUTH)

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
    
    if not data.get('user_id') or not data.get('role_id'):
        raise HTTPException(status_code=400, detail='Missing user_id or role_id')
    
    await role_queue.put(data)
    logger.info(f"Queued role assignment: {data}")
    return {'status': 'queued', 'message': 'Role assignment queued for processing'}

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

bot_task = None
worker_task = None
server_task = None
server = None

async def shutdown():
    logger.info("Shutting down tasks...")
    if server_task:
        server_task.cancel()
    if bot_task:
        bot_task.cancel()
    if worker_task:
        worker_task.cancel()

def shutdown_handler(signum, frame):
    logger.info(f"Received signal {signum} - Initiating graceful shutdown")
    loop = asyncio.get_running_loop()
    loop.create_task(shutdown())

async def check_proxy_ip():
    if not PROXY_URL:
        logger.info("No proxy configured, skipping IP check.")
        return
    try:
        async with ClientSession() as session:
            async with session.get('https://api.ipify.org?format=text', proxy=PROXY, proxy_auth=PROXY_AUTH, timeout=10) as resp:
                if resp.status == 200:
                    ip = (await resp.text()).strip()
                    logger.info(f"Outbound IP via proxy: {ip}")
                else:
                    raise Exception(f"IP check failed with status: {resp.status}")
    except Exception as e:
        logger.error(f"Failed to check outbound IP via proxy: {str(e)}")
        raise

async def main():
    global bot_task, worker_task, server_task, server
    await load_extensions()
    
    await check_proxy_ip()

    bot_task = None
    worker_task = asyncio.create_task(role_assignment_worker())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: shutdown_handler(s, None))

    config = uvicorn.Config(app=app, host=BOT_API_HOST, port=BOT_API_PORT, log_level='info')
    server = uvicorn.Server(config)
    
    server_task = asyncio.create_task(server.serve())

    logger.info(f"Starting bot and worker...")

    max_retries = 5
    retry_delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            try:
                bot_task = asyncio.create_task(bot.start(TOKEN))
                await asyncio.gather(bot_task, worker_task, server_task)
                break
            except discord.HTTPException as e:
                if e.status == 429 and attempt < max_retries:
                    retry_after = float(e.response.headers.get('Retry-After', retry_delay))
                    logger.warning(f"Rate limited (429) on bot startup (attempt {attempt}/{max_retries}). Retrying after {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    retry_delay *= 2
                else:
                    logger.error(f"Bot startup failed after {attempt} attempts: {e}")
                    raise
            except Exception as e:
                logger.error(f"Unexpected error during bot startup (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    raise
            
        except asyncio.CancelledError:
            logger.info('Tasks cancelled - Shutting down')
        finally:
            logger.info("Cleaning up resources...")

            while not role_queue.empty():
                try:
                    data = await asyncio.wait_for(role_queue.get(), timeout=1.0)
                    role_queue.task_done()
                except asyncio.TimeoutError:
                    break
            if worker_task:
                worker_task.cancel()
            await asyncio.sleep(0)
            if bot.is_ready():
                await bot.close()
            else:
                if bot_task:
                    bot_task.cancel()
            if server:
                await server.shutdown()

            logger.info('Shutdown complete.')

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Keyboard interrupt - Shutting down')
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}")