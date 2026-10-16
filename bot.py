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
import hmac
import uuid
from collections.abc import Callable
from aiohttp import ClientError, ClientSession, ClientTimeout
from pydantic import BaseModel
from db.engine import init_db
from utils.command_errors import handle_app_command_error
from utils.role_guard import refusal_reason
from utils.proxy import mute_proxy_auth_deprecations, parse_proxy_url

logging.basicConfig(level=logging.INFO)  # Set to DEBUG for verbose dev
logger = logging.getLogger(__name__)

# Short id unique to this process, logged on connect so overlapping instances
# during a deploy are distinguishable in the Render logs.
INSTANCE_ID = uuid.uuid4().hex[:8]

role_queue = asyncio.Queue()
role_removal_queue = asyncio.Queue()

#: Attempts per role change before it is dropped (a 429 counts as an attempt).
ROLE_MAX_ATTEMPTS = 5
#: First wait before retrying a Discord 5xx or a network error; doubles each time (1s, 2s, 4s, 8s).
ROLE_RETRY_BASE_SECONDS = 1
#: Pause between queue items, so a bulk sweep stays under Discord's rate limits.
ROLE_QUEUE_PAUSE_SECONDS = 0.5
#: Wait when a 429's Retry-After is missing or unreadable, and the margin added to it.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
RETRY_AFTER_MARGIN_SECONDS = 0.5
#: Failures worth a retry besides Discord's 5xx: a dropped connection or a timeout (asyncio's
#: TimeoutError is an OSError). Anything else is a bug, logged with its traceback and dropped.
_TRANSIENT_ERRORS = (OSError, ClientError)


async def apply_role_change(data: dict[str, int], adding: bool) -> None:
    """Add or remove one role, retrying what can succeed later and dropping what cannot.

    A 429 waits for Discord's Retry-After. A 5xx or a network error waits 1s, 2s, 4s... A 4xx
    (the role sits above the bot's, a missing permission) cannot succeed on retry, so it is logged
    once and dropped, as is any other exception (a bug), with its traceback.
    """
    verb = 'assign' if adding else 'remove'
    user_id = data.get('user_id')
    role_id = data.get('role_id')
    guild_id = data.get('guild_id', GUILD_ID)
    for attempt in range(1, ROLE_MAX_ATTEMPTS + 1):
        try:
            guild = bot.get_guild(guild_id)
            if not guild:
                logger.error(f"Guild not found: {guild_id}")
                return
            role = guild.get_role(role_id)
            if not role:
                logger.error(f"Role not found: {role_id} in guild {guild_id}")
                return
            if reason := refusal_reason(role):
                logger.warning(f"Refused to {verb} role {role.name} ({role_id}): {reason}")
                return
            member = guild.get_member(user_id)
            if not member:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    logger.error(f"Member not found: {user_id} in guild {guild_id}")
                    return
            if adding:
                await member.add_roles(role)
                logger.info(f"Assigned role {role.name} to {member.name} in guild {guild_id}")
            elif role not in member.roles:
                logger.info(f"Member {member.name} does not have role {role.name}, skipping removal")
            else:
                await member.remove_roles(role)
                logger.info(f"Removed role {role.name} from {member.name} in guild {guild_id}")
            return
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = _retry_after_seconds(e)
                logger.warning(f"Rate limited (429) on role {verb}; Retry-After {retry_after}s.")
                if attempt < ROLE_MAX_ATTEMPTS:
                    await asyncio.sleep(retry_after + RETRY_AFTER_MARGIN_SECONDS)
            elif e.status >= 500:
                await _wait_before_retry(f"Discord {e.status} on role {verb}", attempt)
            else:
                logger.error(f"Role {verb} failed with Discord {e.status}, not retrying: {e}")
                return
        except _TRANSIENT_ERRORS as e:
            await _wait_before_retry(f"Network error on role {verb} ({e!r})", attempt)
        except Exception:
            logger.exception(f"Role {verb} failed unexpectedly, not retrying: {data}")
            return
    logger.error(f"Gave up on role {verb} after {ROLE_MAX_ATTEMPTS} attempts: {data}")


def _retry_after_seconds(error: discord.HTTPException) -> float:
    """A 429's Retry-After, or the default when it is missing or not a number (an HTTP date)."""
    try:
        return float(error.response.headers.get('Retry-After', DEFAULT_RETRY_AFTER_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS


async def _wait_before_retry(what: str, attempt: int) -> None:
    """Back off before the next attempt: 1s, 2s, 4s... and no wait after the last one."""
    if attempt < ROLE_MAX_ATTEMPTS:
        wait = ROLE_RETRY_BASE_SECONDS * 2 ** (attempt - 1)
        logger.warning(f"{what}. Retrying in {wait}s (attempt {attempt} of {ROLE_MAX_ATTEMPTS}).")
        await asyncio.sleep(wait)
    else:
        logger.warning(f"{what} on the last attempt.")


async def _role_worker(get_queue: Callable[[], asyncio.Queue], adding: bool) -> None:
    # The queue is looked up per item rather than captured, so a test can swap in a fresh one.
    while True:
        queue = get_queue()
        data = await queue.get()
        try:
            await apply_role_change(data, adding)
        except Exception:
            # Never let one request end the worker: a dead worker silently stops every role change
            # until the bot restarts, and a backfill waiting on queue.join() would hang.
            logger.exception(f"Role worker failed on {data!r}")
        finally:
            queue.task_done()
        await asyncio.sleep(ROLE_QUEUE_PAUSE_SECONDS)


async def role_assignment_worker() -> None:
    await _role_worker(lambda: role_queue, adding=True)


async def role_removal_worker() -> None:
    await _role_worker(lambda: role_removal_queue, adding=False)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
API_BASE_URL = os.getenv('API_BASE_URL')
API_KEY = os.getenv('API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')

PLAT_PURSUIT_EMOJI_ID = os.getenv('PLAT_PURSUIT_EMOJI_ID')
PLATINUM_EMOJI_ID = os.getenv('PLATINUM_EMOJI_ID')
GOLD_EMOJI_ID = os.getenv('GOLD_EMOJI_ID')
SILVER_EMOJI_ID = os.getenv('SILVER_EMOJI_ID')
BRONZE_EMOJI_ID = os.getenv('BRONZE_EMOJI_ID')

BOT_API_PORT = int(os.getenv('PORT', 5000))
BOT_API_HOST = os.getenv('BOT_API_HOST', '127.0.0.1') # localhost for dev, 0.0.0.0 for prod

# Outbound proxy for all Discord traffic (see utils/proxy.py for why the credentials go separately).
PROXY_URL = os.getenv('PROXY_URL')
PROXY, PROXY_AUTH = parse_proxy_url(PROXY_URL)
mute_proxy_auth_deprecations()

parser = argparse.ArgumentParser()
parser.add_argument('--sync_commands', action='store_true', help='Global sync bot commands.')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, proxy=PROXY, proxy_auth=PROXY_AUTH)
bot.tree.on_error = handle_app_command_error

bot.api_base_url = API_BASE_URL
bot.api_key = API_KEY
bot.api_headers = {'Authorization': f"Token {API_KEY}"}
bot.api_session = None
# Shared with cogs that need bulk role work to go through the rate-limited worker
# rather than calling Discord directly (see commands/backfill_unverified.py).
bot.role_queue = role_queue
bot.db_engine = None
bot.db_sessionmaker = None
# `or 0` (not a getenv default) so a blank env var is treated as unset rather than crashing.
bot.verified_role_id = int(os.getenv('VERIFIED_ROLE_ID') or 0)
bot.unverified_role_id = int(os.getenv('UNVERIFIED_ROLE_ID') or 0)

bot.plat_pursuit_emoji = f"<:PlatPursuit:{PLAT_PURSUIT_EMOJI_ID}>" if PLAT_PURSUIT_EMOJI_ID else "🏆"
bot.platinum_emoji = f"<:Platinum_Trophy:{PLATINUM_EMOJI_ID}>" if PLATINUM_EMOJI_ID else "🏆"
bot.gold_emoji = f"<:Gold_Trophy:{GOLD_EMOJI_ID}>" if GOLD_EMOJI_ID else "🥇"
bot.silver_emoji = f"<:Silver_Trophy:{SILVER_EMOJI_ID}>" if SILVER_EMOJI_ID else "🥈"
bot.bronze_emoji = f"<:Bronze_Trophy:{BRONZE_EMOJI_ID}>" if BRONZE_EMOJI_ID else "🥉"

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', 0))

#: Roles the bot handles itself (the Verified/Unverified swap) or that grant access by ID rather
#: than by permission flags (the ticket mod role sees and closes every ticket). PlatPursuit only
#: sends badge, milestone and premium roles, so the webhook API refuses these outright.
API_PROTECTED_ROLE_IDS = frozenset(
    int(value)
    for value in (os.getenv('VERIFIED_ROLE_ID'), os.getenv('UNVERIFIED_ROLE_ID'), os.getenv('TICKET_MOD_ROLE_ID'))
    if value
)

# The interactive docs and the OpenAPI schema stay off: the API is called only by PlatPursuit,
# and a public map of its routes helps nobody else.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
security = HTTPBearer()

class RoleRequest(BaseModel):
    user_id: int
    role_id: int
    guild_id: int | None = None

def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)) -> None:
    if not API_KEY:
        # Refuse on purpose: comparing against a missing key would only fail by raising.
        raise HTTPException(status_code=503, detail='API key not configured')
    if not hmac.compare_digest(credentials.credentials, API_KEY):
        raise HTTPException(status_code=401, detail='Invalid API key')

def require_home_guild(data: RoleRequest) -> None:
    """Role requests may only target this bot's own server (PlatPursuit never sends another).

    The workers also refuse any role that carries power (utils/role_guard.py), so a leaked API
    key cannot reach other servers or hand out moderator roles.
    """
    if data.guild_id is not None and data.guild_id != GUILD_ID:
        raise HTTPException(status_code=403, detail='Only the home server is allowed')

def require_api_togglable_role(data: RoleRequest) -> None:
    """Refuse a role the API may never toggle, up front, so PlatPursuit logs the refusal.

    The workers check the role guard again when they run: the role can change while queued, and
    a role the bot has not cached yet can only be checked there.
    """
    if data.role_id in API_PROTECTED_ROLE_IDS:
        raise HTTPException(status_code=403, detail='This role is managed by the bot itself')
    guild = bot.get_guild(GUILD_ID)
    role = guild.get_role(data.role_id) if guild else None
    if role and (reason := refusal_reason(role)):
        raise HTTPException(status_code=403, detail=f'Refused: {reason}')

@app.get("/health")
async def health_check():
    return {'status': 'healthy'}

@app.post("/assign-role")
async def assign_role(data: RoleRequest, _=Depends(verify_api_key)):
    require_home_guild(data)
    require_api_togglable_role(data)
    await role_queue.put(data.model_dump(exclude_none=True))
    logger.info(f"Queued role assignment: user={data.user_id} role={data.role_id}")
    return {'status': 'queued', 'message': 'Role assignment queued for processing'}

@app.post("/remove-role")
async def remove_role(data: RoleRequest, _=Depends(verify_api_key)):
    require_home_guild(data)
    require_api_togglable_role(data)
    await role_removal_queue.put(data.model_dump(exclude_none=True))
    logger.info(f"Queued role removal: user={data.user_id} role={data.role_id}")
    return {'status': 'queued', 'message': 'Role removal queued for processing'}

@app.post("/admin/x-announce/test")
async def admin_x_announce_test(_=Depends(verify_api_key)):
    cog = bot.get_cog('XAnnouncementsCog')
    if not cog:
        raise HTTPException(status_code=503, detail='X announcements cog not loaded')

    test_channel_id = int(os.getenv('X_TEST_ANNOUNCEMENT_CHANNEL_ID', 0))
    test_role_id = int(os.getenv('X_TEST_ANNOUNCEMENT_ROLE_ID', 0))
    if not test_channel_id or not test_role_id:
        raise HTTPException(status_code=400, detail='X_TEST_ANNOUNCEMENT_CHANNEL_ID and X_TEST_ANNOUNCEMENT_ROLE_ID must be set')

    result = await cog.fire_latest(test_channel_id, test_role_id, update_baseline=False)
    if result.get('status') == 'ok':
        return result
    raise HTTPException(status_code=502, detail=result.get('message', 'unknown error'))

@app.post("/admin/x-announce/latest")
async def admin_x_announce_latest(_=Depends(verify_api_key)):
    cog = bot.get_cog('XAnnouncementsCog')
    if not cog:
        raise HTTPException(status_code=503, detail='X announcements cog not loaded')

    channel_id = int(os.getenv('X_ANNOUNCEMENT_CHANNEL_ID', 0))
    role_id = int(os.getenv('X_ANNOUNCEMENT_ROLE_ID', 0))
    if not channel_id or not role_id:
        raise HTTPException(status_code=400, detail='X_ANNOUNCEMENT_CHANNEL_ID and X_ANNOUNCEMENT_ROLE_ID must be set')

    result = await cog.fire_latest(channel_id, role_id, update_baseline=True)
    if result.get('status') == 'ok':
        return result
    raise HTTPException(status_code=502, detail=result.get('message', 'unknown error'))

@bot.event
async def on_ready():
    logger.info(f"{bot.user} has connected to Discord (instance {INSTANCE_ID})! Ready to Pursue Plats!")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

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
        'commands.sync_roles',
        'commands.sync_roles_user',
        'commands.recheck_badges_user',
        # One-time Verified/Unverified transition helper; remove once backfilled.
        'commands.backfill_unverified',

        'commands.community_stats',

        'commands.link_help',

        'commands.welcome',
        'commands.member_events',
        'commands.audit_log',

        'commands.x_announcements',

        'commands.tickets',

        'commands.notes',
    ]
    for ext in extensions:
        try:
            await bot.load_extension(ext)
            logger.info(f"Loaded extension: {ext}")
        except Exception as e:
            logger.error(f"Error to load extension {ext}: {e}")

bot_task = None
worker_task = None
removal_worker_task = None
server_task = None
server = None

_shutdown_started = False

async def shutdown():
    # Idempotent: SIGTERM and SIGINT can both arrive, and Render may resend
    # SIGTERM. Run the teardown exactly once.
    global _shutdown_started
    if _shutdown_started:
        return
    _shutdown_started = True

    logger.info("Shutting down: closing Discord gateway first.")
    # Close the gateway cleanly and promptly. Simply cancelling bot.start() does
    # not guarantee a WebSocket close frame is sent, which leaves a zombie session
    # alive on Discord's side for ~40s (until heartbeats time out). During a Render
    # zero-downtime deploy the new container is already connected, so that zombie
    # window is exactly when duplicate command/event handling happens. bot.close()
    # sends the close frame immediately so Discord drops the old session right away.
    try:
        await asyncio.wait_for(bot.close(), timeout=10)
    except asyncio.TimeoutError:
        logger.warning("bot.close() timed out after 10s; cancelling bot task.")
        if bot_task:
            bot_task.cancel()
    except Exception as e:
        logger.error(f"Error closing bot during shutdown: {e}")

    # Unblock the gather() in main() by stopping the remaining long-lived tasks.
    # The uvicorn server is torn down via server.shutdown() in main()'s finally.
    if server_task:
        server_task.cancel()
    if worker_task:
        worker_task.cancel()
    if removal_worker_task:
        removal_worker_task.cancel()

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

#: Upper bound on one PlatPursuit API call, matching the RSS and admin-script calls. Without it
#: aiohttp waits up to five minutes, leaving a slash command hanging well past Discord's patience.
API_TIMEOUT_SECONDS = 30

def make_api_session(headers: dict[str, str]) -> ClientSession:
    return ClientSession(headers=headers, timeout=ClientTimeout(total=API_TIMEOUT_SECONDS))

async def main():
    global bot_task, worker_task, removal_worker_task, server_task, server
    await load_extensions()

    await check_proxy_ip()

    bot.api_session = make_api_session(bot.api_headers)

    if DATABASE_URL:
        try:
            bot.db_engine, bot.db_sessionmaker = await init_db(DATABASE_URL)
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            bot.db_engine = bot.db_sessionmaker = None
    else:
        logger.warning("DATABASE_URL not set; database-backed features (mod notes) are disabled.")

    bot_task = None
    worker_task = asyncio.create_task(role_assignment_worker())
    removal_worker_task = asyncio.create_task(role_removal_worker())

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: shutdown_handler(s, None))
    except NotImplementedError:
        logger.warning("Signal handlers not supported on this platform (Windows). Use Ctrl+C to stop.")

    config = uvicorn.Config(app=app, host=BOT_API_HOST, port=BOT_API_PORT, log_level='info')
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())

    logger.info("Starting bot and worker...")

    max_retries = 5
    retry_delay = 1.0
    try:
        for attempt in range(1, max_retries + 1):
            try:
                bot_task = asyncio.create_task(bot.start(TOKEN))
                await asyncio.gather(bot_task, worker_task, removal_worker_task, server_task)
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
        while not role_removal_queue.empty():
            try:
                data = await asyncio.wait_for(role_removal_queue.get(), timeout=1.0)
                role_removal_queue.task_done()
            except asyncio.TimeoutError:
                break
        if worker_task:
            worker_task.cancel()
        if removal_worker_task:
            removal_worker_task.cancel()
        await asyncio.sleep(0)
        # Close only if shutdown() didn't already (e.g. a non-signal exit path).
        # Guarding on is_closed() avoids depending on the ready flag's side effects.
        if not bot.is_closed():
            await bot.close()
        elif bot_task:
            bot_task.cancel()
        if bot.api_session:
            await bot.api_session.close()
        if bot.db_engine:
            await bot.db_engine.dispose()
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