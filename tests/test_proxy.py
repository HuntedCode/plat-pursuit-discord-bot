"""The proxy PlatBot reaches Discord through (utils/proxy.py).

PROXY_URL is live in production: it keeps the bot off Render's shared IP and the Discord rate limits
other services there would share. These pin down the three things that matter about it: the proxy
gets the right credentials on the paths production uses (CONNECT, for HTTPS and the gateway
websocket), the password never reaches an error or a log line, and aiohttp's deprecation warnings for
this route are muted without muting anything else.
"""
import ast
import asyncio
import os
import pathlib
import socket
import subprocess
import sys
import warnings

import aiohttp
import pytest

import bot
from utils.proxy import _DEPRECATIONS, mute_proxy_auth_deprecations, parse_proxy_url

ROOT = pathlib.Path(__file__).resolve().parents[1]
PASSWORD = 'S3cretPw'


# --- parsing --------------------------------------------------------------------------------------

@pytest.mark.parametrize('proxy_url, address, credentials', [
    (None, None, None),
    ('', None, None),
    ('http://bot-user:S3cretPw@proxy.example.net:8080', 'http://proxy.example.net:8080', ('bot-user', 'S3cretPw')),
    ('http://bot%40user:p%3Ass%2Fword@proxy.example.net:8080', 'http://proxy.example.net:8080', ('bot@user', 'p:ss/word')),
    ('http://u:pw@[2001:db8::1]:8080', 'http://[2001:db8::1]:8080', ('u', 'pw')),
    ('http://u:pw@proxy.example.net', 'http://proxy.example.net', ('u', 'pw')),
    ('http://proxy.example.net:8080', 'http://proxy.example.net:8080', None),
], ids=['unset', 'empty', 'plain', 'percent-encoded', 'ipv6', 'no-port', 'no-credentials'])
def test_the_address_and_credentials_are_split(proxy_url, address, credentials):
    parsed_address, auth = parse_proxy_url(proxy_url)

    assert parsed_address == address
    assert ((auth.login, auth.password) if auth else None) == credentials


def test_a_malformed_url_fails_without_naming_the_password():
    with pytest.raises(ValueError) as error:
        parse_proxy_url(f'http://bot-user:{PASSWORD}@proxy.example.net:notaport')

    assert PASSWORD not in str(error.value)


# --- against a stand-in proxy ---------------------------------------------------------------------

class StandInProxy:
    """A local proxy that records each request's first line and Proxy-Authorization, then refuses
    it with a 407, as Bright Data does with bad credentials or a source IP it does not allow."""

    def __init__(self):
        self.requests = []

    async def __aenter__(self):
        async def handle(reader, writer):
            head = (await reader.readuntil(b'\r\n\r\n')).decode('latin1').split('\r\n')
            auth = next((h.split(':', 1)[1].strip() for h in head if h.lower().startswith('proxy-authorization:')), None)
            self.requests.append((head[0], auth))
            writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            await writer.drain()
            writer.close()

        self.server = await asyncio.start_server(handle, '127.0.0.1', 0)
        self.url = f'http://bot-user:{PASSWORD}@127.0.0.1:{self.server.sockets[0].getsockname()[1]}'
        return self

    async def __aexit__(self, *exc):
        self.server.close()


@pytest.fixture
def deprecations():
    """Records DeprecationWarnings, with the bot's mute installed as it is in production."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', DeprecationWarning)
        mute_proxy_auth_deprecations()
        yield caught


async def _through(proxy_url, request):
    address, auth = parse_proxy_url(proxy_url)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.ClientHttpProxyError) as error:
            await request(session, address, auth)
    return error.value


async def test_https_requests_authenticate_on_the_connect(deprecations):
    async with StandInProxy() as proxy:
        await _through(proxy.url, lambda s, address, auth: s.get('https://discord.test/api', proxy=address, proxy_auth=auth))

    (line, auth), = proxy.requests
    assert line.startswith('CONNECT discord.test:443')
    assert auth == aiohttp.encode_basic_auth('bot-user', PASSWORD)
    assert [str(w.message) for w in deprecations] == [], 'a proxy-auth deprecation warning is no longer muted'


async def test_the_gateway_websocket_authenticates_on_the_connect(deprecations):
    async with StandInProxy() as proxy:
        await _through(proxy.url, lambda s, address, auth: s.ws_connect('wss://gateway.test/', proxy=address, proxy_auth=auth))

    (line, auth), = proxy.requests
    assert line.startswith('CONNECT gateway.test:443')
    assert auth == aiohttp.encode_basic_auth('bot-user', PASSWORD)
    assert [str(w.message) for w in deprecations] == []


async def test_a_refused_proxy_never_puts_the_password_in_the_error(deprecations):
    """With the credentials left inside the proxy URL, aiohttp's error text carries the URL, password
    included, and discord.py logs that text on every gateway reconnect."""
    async with StandInProxy() as proxy:
        error = await _through(proxy.url, lambda s, address, auth: s.get('https://discord.test/', proxy=address, proxy_auth=auth))

    assert PASSWORD not in str(error)
    assert aiohttp.encode_basic_auth('bot-user', PASSWORD).split()[1] not in str(error), 'the encoded credentials leaked'
    # repr() is NOT clean: it shows request_info.headers, whose Proxy-Authorization decodes to the
    # password. Nothing logs it today (logging and tracebacks use str()); never log one with %r.
    assert PASSWORD not in repr(error)


async def test_the_startup_ip_check_logs_no_password(monkeypatch, caplog, deprecations):
    async with StandInProxy() as proxy:
        address, auth = parse_proxy_url(proxy.url)
        monkeypatch.setattr(bot, 'PROXY_URL', proxy.url)
        monkeypatch.setattr(bot, 'PROXY', address)
        monkeypatch.setattr(bot, 'PROXY_AUTH', auth)
        with pytest.raises(aiohttp.ClientHttpProxyError):
            await bot.check_proxy_ip()

    assert 'Failed to check outbound IP via proxy' in caplog.text
    assert PASSWORD not in caplog.text


def test_the_mute_is_only_for_the_proxy_auth_route(deprecations):
    warnings.warn('something else is deprecated', DeprecationWarning)

    assert [str(w.message) for w in deprecations] == ['something else is deprecated']


_RUNNING_BOT = r"""
import asyncio, os
async def main():
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(handle, "127.0.0.1", int(os.environ["STANDIN_PORT"]))
    import bot
    try:
        await bot.check_proxy_ip()
    except Exception:
        pass
    server.close()
asyncio.run(main())
"""


def test_a_running_bot_prints_no_proxy_warning_and_no_password():
    """pytest resets warning filters for every test, so only a real process shows whether bot.py's
    mute holds. Run with every DeprecationWarning forced on: without the mute, aiohttp's
    proxy_auth warning prints on each request."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, PROXY_URL=f'http://bot-user:{PASSWORD}@127.0.0.1:{port}', STANDIN_PORT=str(port))

    run = subprocess.run(
        [sys.executable, '-W', 'always::DeprecationWarning', '-c', _RUNNING_BOT],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=60,
    )
    output = run.stdout + run.stderr

    assert 'Failed to check outbound IP via proxy' in output, f'the IP check did not run:\n{output[-2000:]}'
    # aiohttp's two messages only: other packages' deprecations (audioop on Python 3.12) are not ours.
    assert not [m for m in _DEPRECATIONS if m in output], output[-2000:]
    assert PASSWORD not in output


# --- bot.py wiring --------------------------------------------------------------------------------

def _calls(tree, name):
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and (getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)) == name
    ]


def test_the_bot_and_the_ip_check_use_the_split_proxy_settings():
    """bot.py builds the client at import, so its wiring is checked in its source."""
    tree = ast.parse((ROOT / 'bot.py').read_text(encoding='utf-8'))
    (client,) = _calls(tree, 'Bot')
    ip_checks = [c for c in _calls(tree, 'get') if any(isinstance(a, ast.Constant) and 'ipify' in str(a.value) for a in c.args)]
    assert ip_checks, 'the proxy IP check is gone; repoint this test'

    for call in [client, *ip_checks]:
        kwargs = {k.arg: getattr(k.value, 'id', None) for k in call.keywords}
        assert (kwargs.get('proxy'), kwargs.get('proxy_auth')) == ('PROXY', 'PROXY_AUTH'), (
            f'bot.py:{call.lineno} must pass the address and credentials separately; the URL with '
            'its credentials puts the password in proxy error messages'
        )
    assert _calls(tree, 'mute_proxy_auth_deprecations'), 'bot.py no longer mutes the proxy-auth deprecations'
