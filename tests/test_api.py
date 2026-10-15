"""The webhook API PlatPursuit calls (FastAPI in bot.py): health and the API-key gate.

These had no tests before the 2026-10 FastAPI 0.143 / Starlette 1.3 upgrade. Importing bot.py
loads the developer's .env, so every test pins its own API_KEY rather than using a real one.
"""
import pytest
from fastapi.testclient import TestClient

import bot

TEST_KEY = 'test-api-key-not-for-production'
ROLE_BODY = {'user_id': 1, 'role_id': 2}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bot, 'API_KEY', TEST_KEY)
    # raise_server_exceptions=False: a server error must come back as a 500, as it would live.
    return TestClient(bot.app, raise_server_exceptions=False)


def _drain(queue):
    while not queue.empty():
        queue.get_nowait()


def test_health_needs_no_key(client):
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'healthy'}


@pytest.mark.parametrize('path', ['/assign-role', '/remove-role', '/admin/x-announce/test', '/admin/x-announce/latest'])
def test_protected_routes_refuse_a_missing_key(client, path):
    assert client.post(path, json=ROLE_BODY).status_code in (401, 403)


@pytest.mark.parametrize('path', ['/assign-role', '/remove-role'])
def test_role_routes_refuse_a_wrong_key(client, path):
    resp = client.post(path, json=ROLE_BODY, headers={'Authorization': 'Bearer wrong-key'})
    assert resp.status_code == 401


@pytest.mark.parametrize('path, queue', [('/assign-role', bot.role_queue), ('/remove-role', bot.role_removal_queue)])
def test_role_routes_queue_the_request_with_the_right_key(client, path, queue):
    _drain(queue)
    resp = client.post(path, json=ROLE_BODY, headers={'Authorization': f'Bearer {TEST_KEY}'})
    assert resp.status_code == 200
    assert resp.json()['status'] == 'queued'
    assert queue.get_nowait() == ROLE_BODY


def test_an_unset_api_key_lets_nothing_through(monkeypatch):
    """With API_KEY missing from the environment, a request must fail, never pass."""
    monkeypatch.setattr(bot, 'API_KEY', None)
    client = TestClient(bot.app, raise_server_exceptions=False)
    resp = client.post('/assign-role', json=ROLE_BODY, headers={'Authorization': 'Bearer anything'})
    assert resp.status_code >= 400
