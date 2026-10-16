"""The outbound proxy the bot reaches Discord through.

PROXY_URL (`scheme://user:password@host:port`) keeps the bot off Render's shared IP, where other
services' traffic counts against the same Discord IP rate limits. It is live in production.

The credentials go to discord.py separately, as `proxy_auth`, and the address goes without them.
aiohttp 3.14 deprecates that route (both `BasicAuth` and the `proxy_auth` parameter; removal in 4.0)
in favour of credentials inside the proxy URL, but that route puts the password in log lines: when
the proxy refuses a request, aiohttp's error text includes the proxy URL, userinfo and all, and
discord.py logs it on every gateway reconnect. So the split stays, and only aiohttp's two
deprecation messages are muted. aiohttp 4 will need another way (discord.py offers no proxy headers).
"""
import warnings

from aiohttp import BasicAuth
from yarl import URL

#: The start of aiohttp 3.14's two deprecation messages for this route (helpers.py, client.py).
_DEPRECATIONS = (
    'BasicAuth is deprecated',
    "The 'proxy_auth' parameter is deprecated",
)


def mute_proxy_auth_deprecations():
    """Ignore exactly aiohttp's two proxy-credential deprecation warnings, process-wide.

    The `proxy_auth` one fires inside aiohttp on every request discord.py makes, so it cannot be
    muted at a call site of ours. Every other warning is untouched.
    """
    for message in _DEPRECATIONS:
        warnings.filterwarnings('ignore', message=message, category=DeprecationWarning)


def parse_proxy_url(proxy_url):
    """(address without credentials, credentials) for discord.py's `proxy` and `proxy_auth`, or
    (None, None) when PROXY_URL is unset. Credentials are None without a username. Percent-encoded
    userinfo is decoded; IPv6 hosts and a missing port are kept as given."""
    if not proxy_url:
        return None, None
    url = URL(proxy_url)
    address = str(url.with_user(None))
    if not url.user:
        return address, None
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message=_DEPRECATIONS[0], category=DeprecationWarning)
        return address, BasicAuth(url.user, url.password or '')
