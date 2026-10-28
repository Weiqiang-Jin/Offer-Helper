import asyncio
import hashlib
import ssl
import time

import httpx
from fastapi.testclient import TestClient

from interview.app import State
from interview.mobile import Companion


def test_pair_once_expire_revoke_and_readonly():
    state = State()
    state.current_question = '当前提问'
    state.answer = '口述建议'
    state.test_clip = (b'private audio', 48000)
    state.history = [{'role': 'remote', 'text': '旧提问', 'final': True}]
    companion = Companion(state)
    companion.code = 'x' * 32
    companion.code_expires = time.monotonic() + 60
    with TestClient(companion.app) as client:
        assert client.get('/view').status_code == 401
        token = client.post('/pair', json={'code': 'x' * 32}).json()['token']
        assert client.post('/pair', json={'code': 'x' * 32}).status_code == 401
        headers = {'Authorization': 'Bearer ' + token}
        response = client.get('/view', headers=headers)
        assert response.json()['question'] == '当前提问'
        assert set(response.json()) == {'question', 'final', 'answer_question', 'answer',
                                       'answer_status', 'listening', 'desktop_connected'}
        assert response.headers['Cache-Control'] == 'no-store'
        assert client.post('/api/audio/start', json={}, headers=headers).status_code == 404
        assert client.get('/api/model', headers=headers).status_code == 404
        companion.sessions[hashlib.sha256(token.encode()).hexdigest()] = 0
        assert client.get('/view', headers=headers).status_code == 401
        companion.code = 'y' * 32
        companion.code_expires = 0
        assert client.post('/pair', json={'code': 'y' * 32}).status_code == 401


def test_live_tls_companion_and_shutdown(monkeypatch, tmp_path):
    from interview import mobile
    monkeypatch.setattr(mobile, 'ROOT', tmp_path)
    monkeypatch.setattr(mobile, 'lan_addresses', lambda: ['127.0.0.1'])
    async def exercise():
        companion = Companion(State())
        assert companion.status()['enabled'] is False
        try:
            result = await companion.enable()
            assert result['enabled']
            assert result['links'][0].startswith('https://')
            context = ssl.create_default_context(cafile=str(tmp_path / '.mobile-tls/cert.pem'))
            async with httpx.AsyncClient(verify=context, trust_env=False) as client:
                root = result['links'][0].split('/#')[0]
                response = await client.post(root + '/pair', json={'code': companion.code})
                assert response.status_code == 200
                token = response.json()['token']
                response = await client.get(root + '/view', headers={'Authorization': 'Bearer ' + token})
                assert response.status_code == 200
                assert companion.status()['paired']
        finally:
            await companion.disable()
        assert not companion.sessions
        assert companion.status()['enabled'] is False
        assert companion.task is None
    asyncio.run(exercise())
