"""The webhook API PlatPursuit calls (FastAPI in bot.py): health and the API-key gate.

These had no tests before the 2026-10 FastAPI 0.143 / Starlette 1.3 upgrade. The
`webhook_client` fixture (conftest.py) pins its own API_KEY, because importing bot.py loads the
developer's .env.
"""
import pytest

import bot
from conftest import TEST_API_KEY

ROLE_BODY = {'user_id': 1, 'role_id': 2}


def test_health_needs_no_key(webhook_client):
    resp = webhook_client.get('/health')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'healthy'}


@pytest.mark.parametrize('path', ['/assign-role', '/remove-role', '/admin/x-announce/test', '/admin/x-announce/latest'])
def test_protected_routes_refuse_a_missing_key(webhook_client, path):
    assert webhook_client.post(path, json=ROLE_BODY).status_code in (401, 403)


@pytest.mark.parametrize('path', ['/assign-role', '/remove-role'])
def test_role_routes_refuse_a_wrong_key(webhook_client, role_queues, path):
    resp = webhook_client.post(path, json=ROLE_BODY, headers={'Authorization': 'Bearer wrong-key'})
    assert resp.status_code == 401
    assert role_queues[path].empty()


@pytest.mark.parametrize('path', ['/assign-role', '/remove-role'])
def test_role_routes_queue_the_request_with_the_right_key(webhook_client, role_queues, path):
    resp = webhook_client.post(path, json=ROLE_BODY, headers={'Authorization': f'Bearer {TEST_API_KEY}'})
    assert resp.status_code == 200
    assert resp.json()['status'] == 'queued'
    assert role_queues[path].get_nowait() == ROLE_BODY


@pytest.mark.parametrize('unset', [None, ''])
def test_an_unset_api_key_lets_nothing_through(webhook_client, role_queues, monkeypatch, unset):
    """With API_KEY missing from the environment, every request is refused on purpose."""
    monkeypatch.setattr(bot, 'API_KEY', unset)
    resp = webhook_client.post('/assign-role', json=ROLE_BODY, headers={'Authorization': 'Bearer anything'})
    assert resp.status_code == 503
    assert role_queues['/assign-role'].empty()
