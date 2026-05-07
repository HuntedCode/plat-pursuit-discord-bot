import discord
from discord.ext import commands, tasks
import os
import re
import logging
import asyncio
import aiohttp
import feedparser

logger = logging.getLogger('psn_api')

MAX_ANNOUNCEMENTS_PER_POLL = 5
OWNED_LINK_PATTERN = re.compile(r'^https?://(?:x|twitter)\.com/([^/]+)/status/', re.IGNORECASE)


class XAnnouncementsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.enabled = os.getenv('ENABLE_X_ANNOUNCEMENTS', 'False').lower() == 'true'
        self.feed_url = os.getenv('X_RSS_FEED_URL', '').strip()
        self.channel_id = int(os.getenv('X_ANNOUNCEMENT_CHANNEL_ID', 0))
        self.role_id = int(os.getenv('X_ANNOUNCEMENT_ROLE_ID', 0))
        self.poll_interval_minutes = max(1, int(os.getenv('X_POLL_INTERVAL_MINUTES', 15)))
        self.own_handle = os.getenv('X_OWN_HANDLE', '').strip().lstrip('@').lower() or None
        self.last_seen_id: str | None = None
        self.baseline_set = False
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        if not self._is_configured():
            logger.info("X announcements disabled or not configured; poll loop will not start.")
            return
        self.session = aiohttp.ClientSession()
        self.poll_feed.change_interval(minutes=self.poll_interval_minutes)
        self.poll_feed.start()
        logger.info(f"X announcements enabled, polling every {self.poll_interval_minutes} min.")

    async def cog_unload(self):
        if self.poll_feed.is_running():
            self.poll_feed.cancel()
        if self.session:
            await self.session.close()

    def _is_configured(self) -> bool:
        if not self.enabled:
            return False
        if not self.feed_url or not self.channel_id or not self.role_id:
            logger.warning("X announcements enabled but missing feed URL, channel ID, or role ID.")
            return False
        return True

    @tasks.loop(minutes=15)
    async def poll_feed(self):
        try:
            entries = self._filter_owned(await self._fetch_entries())
            if not entries:
                return

            latest_id = self._entry_id(entries[0])
            if not latest_id:
                return

            if not self.baseline_set:
                self.last_seen_id = latest_id
                self.baseline_set = True
                logger.info(f"X announcement baseline set to {latest_id}; future posts will be announced.")
                return

            if latest_id == self.last_seen_id:
                return

            new_entries = []
            found_last_seen = False
            for entry in entries:
                eid = self._entry_id(entry)
                if eid == self.last_seen_id:
                    found_last_seen = True
                    break
                new_entries.append(entry)

            if not found_last_seen:
                logger.warning(f"Last-seen X post {self.last_seen_id} not in current feed; resetting baseline without announcing to avoid spam.")
                self.last_seen_id = latest_id
                return

            for entry in reversed(new_entries[:MAX_ANNOUNCEMENTS_PER_POLL]):
                await self._announce(entry)

            self.last_seen_id = latest_id
        except Exception as e:
            logger.error(f"X announcement poll failed: {e}")

    @poll_feed.before_loop
    async def _before_poll(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def _entry_id(entry) -> str | None:
        return entry.get('id') or entry.get('link')

    def _is_own_tweet(self, entry) -> bool:
        link = entry.get('link', '')
        match = OWNED_LINK_PATTERN.match(link)
        if not match:
            logger.warning(f"X RSS entry link did not match expected pattern, allowing through: {link}")
            return True
        return match.group(1).lower() == self.own_handle

    def _filter_owned(self, entries: list) -> list:
        if not self.own_handle:
            return entries
        return [e for e in entries if self._is_own_tweet(e)]

    async def _fetch_entries(self) -> list:
        try:
            async with self.session.get(self.feed_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning(f"X RSS fetch returned status {resp.status}")
                    return []
                body = await resp.read()
        except Exception as e:
            logger.error(f"X RSS fetch error: {e}")
            return []

        parsed = await asyncio.to_thread(feedparser.parse, body)
        return parsed.entries or []

    async def _announce(self, entry):
        await self._post_entry(entry, self.channel_id, self.role_id)

    async def _post_entry(self, entry, channel_id: int, role_id: int) -> tuple[bool, str]:
        link = entry.get('link')
        if not link:
            return False, 'feed entry has no link'

        channel = self.bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"X announcement channel {channel_id} not found.")
            return False, f'channel {channel_id} not found'

        content = f"<@&{role_id}> New post: {link}"
        try:
            await channel.send(
                content=content,
                allowed_mentions=discord.AllowedMentions(roles=True, everyone=False, users=False),
            )
            logger.info(f"Announced X post to channel {channel_id}: {link}")
            return True, link
        except discord.Forbidden:
            logger.error(f"Bot lacks permission to post in channel {channel_id}")
            return False, f'forbidden sending to channel {channel_id}'
        except discord.HTTPException as e:
            logger.error(f"Failed to announce X post: {e}")
            return False, f'discord error: {e}'

    async def fire_latest(self, channel_id: int, role_id: int, update_baseline: bool) -> dict:
        if not self.feed_url:
            return {'status': 'error', 'message': 'X_RSS_FEED_URL not configured'}
        if not channel_id or not role_id:
            return {'status': 'error', 'message': 'channel_id and role_id are required'}

        if self.session is None:
            self.session = aiohttp.ClientSession()

        entries = self._filter_owned(await self._fetch_entries())
        if not entries:
            return {'status': 'error', 'message': 'no owned tweets in feed (all filtered as retweets, or feed empty)'}

        entry = entries[0]
        ok, link_or_err = await self._post_entry(entry, channel_id, role_id)
        if not ok:
            return {'status': 'error', 'message': link_or_err}

        if update_baseline:
            entry_id = self._entry_id(entry)
            if entry_id:
                self.last_seen_id = entry_id
                self.baseline_set = True
                logger.info(f"Baseline advanced to {entry_id} after manual fire.")

        return {'status': 'ok', 'link': link_or_err}


async def setup(bot):
    await bot.add_cog(XAnnouncementsCog(bot))
