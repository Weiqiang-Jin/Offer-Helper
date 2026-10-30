"""Opt-in TLS companion server. No desktop controls or provider credentials."""
import asyncio
import hashlib
import ipaddress
import secrets
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import uvicorn


from .paths import DATA_DIR, RESOURCE_DIR

ROOT = DATA_DIR


def lan_addresses():
    addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    return sorted({item[4][0] for item in addresses
                   if ipaddress.ip_address(item[4][0]).is_private
                   and not ipaddress.ip_address(item[4][0]).is_loopback
                   and not ipaddress.ip_address(item[4][0]).is_link_local})


def make_certificate(addresses):
    folder = ROOT / '.mobile-tls'
    folder.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'offer helper local companion')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(a))
                for a in addresses + ['127.0.0.1']]), critical=False)
            .sign(key, hashes.SHA256()))
    (folder / 'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (folder / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return folder, cert.fingerprint(hashes.SHA256()).hex(':')


class PairBody(BaseModel):
    code: str = Field(min_length=20, max_length=200)


class Companion:
    def __init__(self, state):
        self.state = state
        self.server = self.task = None
        self.code = ''
        self.code_expires = 0
        self.sessions = {}
        self.addresses = []
        self.port = None
        self.fingerprint = ''
        self.lock = asyncio.Lock()
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @self.app.middleware('http')
        async def headers(request, call_next):
            response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
            return response

        @self.app.get('/')
        def page():
            return FileResponse(RESOURCE_DIR / 'web/mobile.html')

        @self.app.get('/mobile.js')
        def script():
            return FileResponse(RESOURCE_DIR / 'web/mobile.js')

        @self.app.get('/style.css')
        def style():
            return FileResponse(RESOURCE_DIR / 'web/style.css')

        @self.app.post('/pair')
        async def pair(body: PairBody):
            if not self.code or time.monotonic() >= self.code_expires or not secrets.compare_digest(body.code, self.code):
                raise HTTPException(401, '配对链接已使用或过期，请在电脑重新生成。')
            self.code = ''
            self.sessions.clear()  # One phone at a time; replacing a phone revokes the old one.
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = time.monotonic() + 12 * 3600
            return {'token': token}

        @self.app.get('/view')
        async def view(authorization: str = Header(default='')):
            token = authorization.removeprefix('Bearer ')
            digest = hashlib.sha256(token.encode()).hexdigest()
            if time.monotonic() >= self.sessions.get(digest, 0):
                raise HTTPException(401, '配对已撤销或过期，请重新配对。')
            return self.snapshot()

    def snapshot(self):
        latest = next((x for x in reversed(self.state.history) if x.get('role') == 'remote'), None)
        return {'question': self.state.current_question,
                'final': latest.get('final', True) if latest else True,
                'answer_question': self.state.question, 'answer': self.state.answer,
                'answer_status': self.state.answer_status,
                'listening': self.state.mode == 'transcribe',
                'desktop_connected': bool(self.state.clients)}

    def status(self):
        active = bool(self.server and self.server.started and self.task and not self.task.done())
        return {'enabled': active, 'addresses': self.addresses if active else [],
                'port': self.port if active else None, 'fingerprint': self.fingerprint if active else '',
                'paired': any(exp > time.monotonic() for exp in self.sessions.values())}

    async def enable(self):
        async with self.lock:
            if not self.status()['enabled']:
                self.addresses = await asyncio.to_thread(lan_addresses)
                if not self.addresses:
                    raise HTTPException(400, '没有发现局域网 IPv4 地址，请连接 Wi-Fi 或有线网络。')
                folder, self.fingerprint = await asyncio.to_thread(make_certificate, self.addresses)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    sock.bind(('0.0.0.0', 0))
                    sock.listen(128)
                    self.port = sock.getsockname()[1]
                    config = uvicorn.Config(self.app, access_log=False, log_level='warning',
                        ssl_certfile=str(folder / 'cert.pem'), ssl_keyfile=str(folder / 'key.pem'),
                        timeout_graceful_shutdown=2)
                    self.server = uvicorn.Server(config)
                    self.task = asyncio.create_task(self.server.serve(sockets=[sock]))
                    for _ in range(100):
                        if self.server.started:
                            break
                        if self.task.done():
                            raise RuntimeError('Mobile listener stopped')
                        await asyncio.sleep(.05)
                    if not self.server.started:
                        raise RuntimeError('Mobile listener timeout')
                except Exception:
                    if self.server:
                        self.server.should_exit = True
                    if self.task:
                        await self.task
                    sock.close()
                    raise
            self.code = secrets.token_urlsafe(32)
            self.code_expires = time.monotonic() + 300
            return {**self.status(), 'links': [f'https://{a}:{self.port}/#pair={self.code}' for a in self.addresses],
                    'expires_in': 300}

    async def disable(self):
        async with self.lock:
            self.code = ''
            self.sessions.clear()
            if self.server:
                self.server.should_exit = True
            if self.task:
                await self.task
            self.server = self.task = None
            return self.status()
