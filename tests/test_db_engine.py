from db.engine import _normalize_url


def test_postgres_scheme_converted_to_asyncpg():
    url, args = _normalize_url('postgres://u:p@h:5432/db')
    assert url == 'postgresql+asyncpg://u:p@h:5432/db'
    assert args == {}


def test_postgresql_scheme_converted_to_asyncpg():
    url, args = _normalize_url('postgresql://u:p@h/db')
    assert url == 'postgresql+asyncpg://u:p@h/db'
    assert args == {}


def test_already_asyncpg_url_preserved():
    url, args = _normalize_url('postgresql+asyncpg://u:p@h/db')
    assert url == 'postgresql+asyncpg://u:p@h/db'
    assert args == {}


def test_sslmode_require_becomes_connect_arg():
    url, args = _normalize_url('postgresql://u:p@h/db?sslmode=require')
    assert 'sslmode' not in url
    assert args == {'ssl': True}


def test_sslmode_disable_does_not_force_ssl():
    url, args = _normalize_url('postgresql://u:p@h/db?sslmode=disable')
    assert args == {}


def test_channel_binding_param_stripped():
    url, args = _normalize_url('postgresql://u:p@h/db?sslmode=require&channel_binding=require')
    assert 'channel_binding' not in url
    assert 'sslmode' not in url
    assert args == {'ssl': True}


def test_sqlite_url_passes_through_untouched():
    url, args = _normalize_url('sqlite+aiosqlite:///notes.db')
    assert url == 'sqlite+aiosqlite:///notes.db'
    assert args == {}
