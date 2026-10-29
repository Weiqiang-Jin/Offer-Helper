import asyncio
import json
import time
from urllib.parse import urlsplit, parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from interview.chatgpt import ChatGPTConnection, AUTH


def test_authorization_pkce_and_stable_host(tmp_path):
    path=tmp_path/'registration.json'
    c=ChatGPTConnection(path)
    first=parse_qs(urlsplit(c.begin(8765)).query)
    second=parse_qs(urlsplit(c.begin(8766)).query)
    assert first['state'] != second['state']
    assert first['code_challenge'] != second['code_challenge']
    assert first['ext_agent_host_id'] == second['ext_agent_host_id']
    assert first['client_id'] == ['dynamic_agent_client']
    with pytest.raises(ValueError): asyncio.run(c.finish({'state':'wrong'}))
    assert not c.tokens


@pytest.mark.parametrize('valid_nonce',[True,False])
def test_signed_identity_required_before_tokens_saved(tmp_path,monkeypatch,valid_nonce):
    c=ChatGPTConnection(tmp_path/'registration.json');c.begin(8765)
    state=c.pending['state'];nonce=c.pending['nonce'] if valid_nonce else 'wrong'
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk['kid']='test'
    token=jwt.encode({'iss':AUTH,'aud':'oaiapp_test','sub':'user','nonce':nonce,'exp':time.time()+60},key,algorithm='RS256',headers={'kid':'test'})
    def handler(request):
        if request.url.path.endswith('/oauth/token'):
            return httpx.Response(200,json={'id_token':token,'access_token':'secret','scope':'chatgpt.tokens.use.direct','expires_in':3600})
        if request.url.path.endswith('openid-configuration'):
            return httpx.Response(200,json={'jwks_uri':AUTH+'/jwks'})
        return httpx.Response(200,json={'keys':[jwk]})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    call=c.finish({'state':state,'client_id':'oaiapp_test','code':'test'})
    if valid_nonce:
        asyncio.run(call);assert c.status()['sharing'];assert not c.enabled
        assert 'secret' not in json.dumps(c.status())
        assert 'secret' not in c.path.read_text()
    else:
        with pytest.raises(ValueError):asyncio.run(call)
        assert not c.tokens


@pytest.mark.parametrize('completed',[True,False])
def test_response_requires_completion(tmp_path,monkeypatch,completed):
    c=ChatGPTConnection(tmp_path/'r.json');c.model='available-model'
    c.tokens={'access_token':'test','scope':'chatgpt.tokens.use.direct','expires_at':time.time()+3600}
    def handler(request):
        payload=json.loads(request.content)
        assert payload['store'] is False and payload['stream'] is True
        assert request.url.path=='/v1/responses'
        data='data: '+json.dumps({'type':'response.output_text.delta','delta':'回答'})+'\n\n'
        if completed:data+='data: '+json.dumps({'type':'response.completed'})+'\n\n'
        return httpx.Response(200,text=data)
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    if completed:assert asyncio.run(c.complete([]))=='回答'
    else:
        with pytest.raises(RuntimeError):asyncio.run(c.complete([]))


def test_login_failure_reports_stage_without_leaking_response(tmp_path,monkeypatch):
    c=ChatGPTConnection(tmp_path/'r.json');c.begin(8765)
    state=c.pending['state']
    def handler(request):
        return httpx.Response(400,json={'error':'invalid_grant','error_description':'secret-access-token-and-code'})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(ValueError):
        asyncio.run(c.finish({'state':state,'client_id':'oaiapp_test','code':'secret-code'}))
    assert '交换授权码' in c.status()['login_error']
    assert 'invalid_grant' in c.status()['login_error']
    assert 'secret' not in json.dumps(c.status())
    assert not c.tokens
    c.begin(8765)
    assert not c.status()['login_error']
