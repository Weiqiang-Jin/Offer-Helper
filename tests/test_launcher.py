import json
import socket
from unittest.mock import patch
import run


def test_launch_url_bypasses_old_document_cache():
    from urllib.parse import urlsplit
    old = 'http://127.0.0.1:8766/#token=example'
    first, second = run.fresh_url(old), run.fresh_url(old)
    assert first != second
    assert urlsplit(first).fragment == 'token=example'
    assert urlsplit(first).netloc == '127.0.0.1:8766'
    assert 'ui=live-qa-v4' in urlsplit(first).query


def test_occupied_port_is_not_reused():
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        reserved = run.reserve_port(port)
        try:
            assert reserved.getsockname()[1] != port
        finally:
            reserved.close()


def test_stale_runtime_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'ROOT', tmp_path)
    (tmp_path / '.runtime.json').write_text(json.dumps({'url': 'http://127.0.0.1:12345/#token=stale'}))
    with patch('urllib.request.OpenerDirector.open', side_effect=OSError('closed')):
        assert run.existing_url() is None


def test_external_runtime_never_contacted(tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'ROOT', tmp_path)
    (tmp_path / '.runtime.json').write_text(json.dumps({'url': 'http://example.com/#token=stale'}))
    with patch('urllib.request.OpenerDirector.open') as call:
        assert run.existing_url() is None
        call.assert_not_called()
