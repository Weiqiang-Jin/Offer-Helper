import argparse
import json
import os
import secrets
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent


def fresh_url(url):
    """Force a new document load, preserving the authentication fragment."""
    from urllib.parse import urlsplit, urlunsplit
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/',
                       'ui=live-qa-v4&launch=' + secrets.token_hex(8), parsed.fragment))


def existing_url():
    try:
        from urllib.parse import urlsplit, parse_qs
        data = json.loads((ROOT / '.runtime.json').read_text(encoding='utf-8'))
        parsed = urlsplit(data['url'])
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
            return None
        token = parse_qs(parsed.fragment)['token'][0]
        request = urllib.request.Request(f'http://127.0.0.1:{parsed.port}/api/state',
                                         headers={'Authorization': 'Bearer ' + token})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=1) as response:
            if json.load(response).get('type') == 'snapshot':
                return data['url']
    except (OSError, ValueError, KeyError, IndexError):
        pass
    return None


def reserve_port(preferred):
    for port in range(preferred, min(preferred + 30, 65536)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', port))
            sock.listen(128)
            return sock
        except OSError:
            sock.close()
    raise RuntimeError('No available local port. Close an unused application and retry.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535')
    url = existing_url()
    if url:
        print(f'offer helper | Project: {ROOT}\nOpening current UI: live-qa-v4', flush=True)
        if not args.no_browser:
            webbrowser.open(fresh_url(url))
        return
    # Reserve the port before issuing credentials or opening the browser.
    sock = reserve_port(args.port)
    port = sock.getsockname()[1]
    token_path = ROOT / '.local-token'
    try:
        with token_path.open('x', encoding='ascii') as file:
            file.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    token = token_path.read_text(encoding='ascii').strip()
    if len(token) < 32:
        raise RuntimeError('Invalid .local-token file. Remove that file and restart.')
    os.environ["INTERVIEW_TOKEN"] = token
    url = f'http://127.0.0.1:{port}/#token={token}'
    server = uvicorn.Server(uvicorn.Config('interview.app:app', host='127.0.0.1', port=port, access_log=False))
    def ready():
        while not server.started and not server.should_exit:
            time.sleep(0.1)
        if server.started:
            (ROOT / '.runtime.json').write_text(json.dumps({'url': url, 'pid': os.getpid()}), encoding='utf-8')
            print(f'Project: {ROOT}\nReady: http://127.0.0.1:{port}\nKeep this window open.', flush=True)
            if not args.no_browser:
                webbrowser.open(fresh_url(url))
    threading.Thread(target=ready, daemon=True).start()
    try:
        server.run(sockets=[sock])
    finally:
        sock.close()


if __name__ == '__main__':
    main()

