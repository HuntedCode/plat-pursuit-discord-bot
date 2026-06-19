import logging
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .models import Base

logger = logging.getLogger('psn_api')


def _normalize_url(raw_url: str):
    """Return (sqlalchemy_url, connect_args) for an async engine.

    Accepts the `postgres://` / `postgresql://` form Django and Render hand out and points it
    at the asyncpg driver. asyncpg does not understand libpq-only query params, so `sslmode`
    is translated into a connect arg and `channel_binding` is dropped. SQLite URLs pass through.
    """
    url = raw_url
    for prefix in ('postgresql://', 'postgres://'):
        if url.startswith(prefix):
            url = 'postgresql+asyncpg://' + url[len(prefix):]
            break

    connect_args = {}
    if url.startswith('postgresql+asyncpg://'):
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        sslmode = query.pop('sslmode', None)
        query.pop('channel_binding', None)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        if sslmode and sslmode != 'disable':
            connect_args['ssl'] = True

    return url, connect_args


async def init_db(database_url: str):
    """Create the async engine, ensure PlatBot's tables exist, and return (engine, sessionmaker).

    `create_all` only touches tables in this package's metadata (checkfirst=True), so it is safe
    against a database shared with PlatPursuit: it never sees or alters Django's tables.
    """
    url, connect_args = _normalize_url(database_url)
    engine = create_async_engine(url, connect_args=connect_args, pool_pre_ping=True)
    async with engine.begin() as conn:
        # checkfirst=True (explicit): only create tables in this metadata that are missing,
        # never alter existing ones. This is what keeps a shared database safe.
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, checkfirst=True))
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    logger.info("Database initialized.")
    return engine, sessionmaker
