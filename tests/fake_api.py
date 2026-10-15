"""A stand-in for the bot's aiohttp session to the PlatPursuit API, for tests.

Replaces aioresponses, which builds aiohttp's internal ClientResponse itself and broke on
aiohttp 3.14 (a new required `stream_writer` argument; no aioresponses release supports it yet).
This fake depends only on the small surface the cogs actually use:

    async with session.get(url, params=...) / session.post(url, json=...) as resp:
        resp.status, await resp.json(), await resp.text()

Usage mirrors aioresponses, so tests read the same:

    with mock_api(cog.bot) as mocked:
        mocked.post('https://api.test/refresh/', payload={'linked': True})
        await RefreshCog.refresh.callback(cog, interaction)

Each registered response answers one matching request, in order. A request nothing matches
raises aiohttp.ClientConnectionError, as aioresponses did, so code paths that handle a failed
call (the fire-and-forget role sync after /link, for one) behave as they do in production.
"""
import json
from collections import defaultdict, deque
from contextlib import contextmanager

import aiohttp
from yarl import URL


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return '' if self._payload is None else json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeApiSession:
    """Answers get/post from registered responses; records every request it receives."""

    def __init__(self):
        self._responses = defaultdict(deque)
        self.requests = []

    def _register(self, method, url, payload, status):
        self._responses[(method, URL(url))].append(FakeResponse(status, payload))

    def _answer(self, method, url, params=None, **kwargs):
        target = URL(url).update_query(params) if params else URL(url)
        self.requests.append((method, target, kwargs.get('json')))
        queue = self._responses.get((method, target))
        if not queue:
            raise aiohttp.ClientConnectionError(f'Connection refused: {method} {target}')
        return queue.popleft()

    def get(self, url, **kwargs):
        return self._answer('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self._answer('POST', url, **kwargs)

    async def close(self):
        pass


class _Registrar:
    def __init__(self, session):
        self._session = session

    def get(self, url, *, payload=None, status=200):
        self._session._register('GET', url, payload, status)

    def post(self, url, *, payload=None, status=200):
        self._session._register('POST', url, payload, status)


@contextmanager
def mock_api(bot):
    """Register responses for `bot.api_session` inside the block; they are dropped after it."""
    session = bot.api_session
    try:
        yield _Registrar(session)
    finally:
        session._responses.clear()
