"""Optional local ChatGPT plan connection. Tokens stay in process memory."""
import asyncio
import base64
import hashlib
import json
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import httpx
import jwt

RESOURCE = 'https://api.openai.com/v1'
AUTH = 'https://auth.openai.com'
TOKEN_URL = AUTH + '/api/accounts/oauth/token'


class ChatGPTConnection:
    def __init__(self, path):
        self.path = Path(path)
        self.registration = {}
        if self.path.exists():
            self.registration = json.loads(self.path.read_text(encoding='utf-8'))
        self.pending = None
        self.tokens = {}
        self.email = ''
        self.models = []
        self.model = ''
        self.enabled = False
        self.lock = asyncio.Lock()
        self.login_stage = ''
        self.login_error = ''

    def save_registration(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.registration), encoding='utf-8')
        temporary.replace(self.path)

    def status(self):
        sharing = 'chatgpt.tokens.use.direct' in self.tokens.get('scope', '').split()
        return {'connected': bool(self.tokens), 'sharing': sharing, 'enabled': self.enabled,
                'pending': bool(self.pending and time.monotonic() < self.pending['expires']),
                'login_error': self.login_error,
                'email': self.email, 'models': self.models, 'model': self.model}

    def begin(self, port):
        self.login_error = ''
        self.login_stage = '等待浏览器授权'
        self.registration.setdefault('host', 'urn:uuid:' + str(uuid.uuid4()))
        self.save_registration()
        verifier = secrets.token_urlsafe(48)
        self.pending = {'state': secrets.token_urlsafe(32), 'nonce': secrets.token_urlsafe(32),
                        'verifier': verifier, 'redirect': f'http://127.0.0.1:{port}/auth/callback',
                        'expires': time.monotonic() + 300,
                        'client': self.registration.get('client', 'dynamic_agent_client')}
        p = self.pending
        params = {'client_id': p['client'], 'ext_agent_host_id': self.registration['host'],
                  'response_type': 'code', 'redirect_uri': p['redirect'], 'state': p['state'],
                  'nonce': p['nonce'], 'scope': 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct',
                  'resource': RESOURCE, 'code_challenge_method': 'S256',
                  'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
        if p['client'] == 'dynamic_agent_client':
            params['agent_name_hint'] = 'offer helper'
        return AUTH + '/api/accounts/authorize?' + urlencode(params)

    async def finish(self, query):
        try:
            await self._finish(query)
            self.login_error = ''
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                code = ''
                try:
                    value = exc.response.json().get('error')
                    if isinstance(value, dict): value = value.get('code')
                    if value in ('invalid_grant', 'invalid_client', 'access_denied', 'invalid_scope', 'unauthorized_client'):
                        code = ' / ' + value
                except (ValueError, AttributeError):
                    pass
                reason = f'服务返回 HTTP {exc.response.status_code}{code}。'
                if code.endswith('invalid_grant'):
                    reason += '授权码已过期或已使用，请重新点击登录，不要刷新回调页。'
            elif isinstance(exc, httpx.RequestError):
                reason = '后台无法连接 OpenAI，请检查后台进程的网络或代理设置。'
            elif isinstance(exc, jwt.ExpiredSignatureError):
                reason = '身份凭据已过期，请校准系统时间后重新登录。'
            elif isinstance(exc, jwt.InvalidTokenError):
                reason = f'身份凭据校验失败（{type(exc).__name__}），请重新登录。'
            elif type(exc) is ValueError and str(exc) in (
                '登录请求已失效，请重新登录。', '未完成授权，请返回软件重试。',
                '登录注册信息不匹配。', '身份签名地址无效。', '身份校验失败。', '账号身份不匹配。'):
                reason = str(exc)
            elif isinstance(exc, (KeyError, StopIteration)):
                reason = '授权响应缺少预期的身份字段或签名公钥。'
            else:
                reason = f'处理失败（{type(exc).__name__}）。'
            self.login_error = f'{self.login_stage}：{reason}'
            raise ValueError(self.login_error) from None

    async def _finish(self, query):
        self.login_stage = '校验登录回调'
        p = self.pending
        if not p or time.monotonic() > p['expires'] or not secrets.compare_digest(query.get('state', ''), p['state']):
            raise ValueError('登录请求已失效，请重新登录。')
        self.pending = None
        if query.get('error'):
            raise ValueError('未完成授权，请返回软件重试。')
        client_id = query.get('client_id', p['client'])
        if client_id == 'dynamic_agent_client' or (p['client'] != 'dynamic_agent_client' and client_id != p['client']):
            raise ValueError('登录注册信息不匹配。')
        self.registration['client'] = client_id
        self.save_registration()
        async with httpx.AsyncClient(timeout=30) as client:
            self.login_stage = '交换授权码'
            response = await client.post(TOKEN_URL, data={'grant_type': 'authorization_code',
                'client_id': client_id, 'code': query.get('code', ''), 'code_verifier': p['verifier'],
                'redirect_uri': p['redirect'], 'resource': RESOURCE})
            response.raise_for_status()
            tokens = response.json()
            self.login_stage = '获取身份校验配置'
            discovery = await client.get(AUTH + '/.well-known/openid-configuration')
            discovery.raise_for_status()
            jwks_url = discovery.json()['jwks_uri']
            if not jwks_url.startswith(AUTH + '/'):
                raise ValueError('身份签名地址无效。')
            self.login_stage = '获取身份签名公钥'
            keys = await client.get(jwks_url)
            keys.raise_for_status()
            self.login_stage = '校验身份令牌'
            header = jwt.get_unverified_header(tokens['id_token'])
            key = next((k for k in keys.json()['keys'] if k.get('kid') == header.get('kid')), None)
            if key is None: raise KeyError('signing key')
            identity = jwt.decode(tokens['id_token'], jwt.PyJWK.from_dict(key).key,
                algorithms=['RS256'], issuer=AUTH, audience=client_id,
                options={'require': ['exp', 'iss', 'aud', 'sub', 'nonce']})
            if not secrets.compare_digest(identity['nonce'], p['nonce']):
                raise ValueError('身份校验失败。')
            if self.registration.get('subject') and identity['sub'] != self.registration['subject']:
                raise ValueError('账号身份不匹配。')
            self.registration['subject'] = identity['sub']
            self.save_registration()
            self.tokens = {**tokens, 'expires_at': time.time() + tokens.get('expires_in', 3600)}
            self.email = identity.get('email', '')
            self.enabled = False

    async def access_token(self):
        async with self.lock:
            if not self.status()['sharing']:
                raise ValueError('请登录并授权使用 ChatGPT 计划。')
            if self.tokens['expires_at'] < time.time() + 60:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(TOKEN_URL, data={'grant_type': 'refresh_token',
                        'client_id': self.registration['client'], 'refresh_token': self.tokens.get('refresh_token', ''),
                        'resource': RESOURCE})
                    response.raise_for_status()
                    fresh = response.json()
                    self.tokens = {**self.tokens, **fresh, 'expires_at': time.time() + fresh.get('expires_in', 3600)}
            if not self.status()['sharing']:
                raise ValueError('ChatGPT 计划授权已失效。')
            return self.tokens['access_token']

    async def list_models(self):
        token = await self.access_token()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(RESOURCE + '/models', headers={'Authorization': 'Bearer ' + token})
            response.raise_for_status()
            self.models = [{'slug': m['slug'], 'name': m.get('display_name', m['slug'])}
                           for m in response.json().get('models', []) if m.get('visibility') == 'list']
        if self.model not in [m['slug'] for m in self.models]:
            self.enabled = False
        return self.models

    async def stream(self, messages):
        token = await self.access_token()
        completed = False
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
            async with client.stream('POST', RESOURCE + '/responses',
                    headers={'Authorization': 'Bearer ' + token},
                    json={'model': self.model, 'input': messages, 'store': False, 'stream': True}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith('data:'): continue
                    event = json.loads(line[5:].strip())
                    if event.get('type') == 'response.output_text.delta': yield event['delta']
                    elif event.get('type') == 'response.completed': completed = True
                    elif event.get('type') in ('error', 'response.failed', 'response.incomplete'):
                        raise RuntimeError('ChatGPT 请求失败，请检查计划额度或重新登录。')
        if not completed: raise RuntimeError('ChatGPT 响应中断，请重试。')

    async def complete(self, messages):
        return ''.join([chunk async for chunk in self.stream(messages)])

    def logout(self):
        self.login_error = ''
        self.pending = None
        self.tokens = {}
        self.models = []
        self.email = self.model = ''
        self.enabled = False


from .paths import DATA_DIR

connection = ChatGPTConnection(DATA_DIR / '.chatgpt-registration.json')
