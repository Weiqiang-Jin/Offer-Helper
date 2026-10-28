import asyncio
import json
import numpy as np
import pytest
from fastapi.testclient import TestClient
from interview import app as module
from interview.audio import mono_pcm
from interview.answers import messages, punctuate_question


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(module, "state", module.State())
    monkeypatch.setattr(module, "CONFIG", dict(module.CONFIG))
    monkeypatch.setitem(module.CONFIG, "llm_key", "")
    monkeypatch.setitem(module.CONFIG, "asr_key", "")
    monkeypatch.setitem(module.CONFIG, "asr_backend", "dashscope")
    with TestClient(module.app) as c:
        c.headers["Authorization"] = "Bearer " + module.TOKEN
        yield c


def test_auth_and_missing_credentials(client):
    assert client.get('/api/state', headers={'Authorization': ''}).status_code == 401
    assert client.post('/api/answer', json={'question': '为什么？'}).status_code == 400
    assert client.post('/api/audio/start', json={'remote': 1, 'self_device': 2, 'transcribe': True}).status_code == 400
    assert client.post('/api/answer', json={'question': '   '}).status_code == 400
    assert client.post('/api/answer', json={'question': '问题', 'demo': True}).status_code == 400


def test_transcript_finalization_and_stale_session(client):
    s = module.state
    s.event({'type': 'transcript', 'id': 'a', 'role': 'remote', 'text': '草稿', 'final': False})
    s.event({'type': 'transcript', 'id': 'a', 'role': 'remote', 'text': '完整问题', 'final': True})
    s.event({'type': 'transcript', 'id': 'a', 'role': 'remote', 'text': '迟到草稿', 'final': False})
    s.event({'type': 'transcript', 'session': 'old', 'id': 'b', 'text': '旧会话'})
    assert len(s.history) == 1
    assert s.history[0]['text'] == '完整问题'


def test_model_websocket_and_reset(client, monkeypatch):
    async def model_answer(*args):
        yield '模型回答'
    monkeypatch.setattr(module, 'stream_answer', model_answer)
    monkeypatch.setitem(module.CONFIG, 'llm_key', 'fake')
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'token': module.TOKEN})
        assert ws.receive_json()['type'] == 'snapshot'
        assert client.post('/api/transcript', json={'role': 'remote', 'text': '介绍你的项目'}).status_code == 200
        assert ws.receive_json()['type'] == 'transcript'
        assert client.post('/api/answer', json={'question': '介绍你的项目', 'demo': True}).status_code == 200
        chunks=[]
        while True:
            event=ws.receive_json()
            if event['type']=='answer_delta': chunks.append(event['text'])
            if event['type']=='answer_end':
                assert event['status']=='done'
                break
        assert ''.join(chunks) == '模型回答'
        assert len(module.state.history)==1  # Generated advice is not spoken history.
        assert client.post('/api/reset',json={}).status_code==200
        assert client.get('/api/state').json()['history']==[]


def test_generation_cancellation_and_concurrency(client, monkeypatch):
    async def slow(*args):
        yield '开始'
        await asyncio.sleep(30)
        yield '结束'
    monkeypatch.setattr(module, 'stream_answer', slow)
    monkeypatch.setitem(module.CONFIG, 'llm_key', 'fake')
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'token': module.TOKEN}); ws.receive_json()
        assert client.post('/api/answer',json={'question':'测试'}).status_code==200
        while ws.receive_json()['type'] != 'answer_delta': pass
        assert client.post('/api/answer',json={'question':'重复'}).status_code==409
        assert client.post('/api/reset',json={}).status_code==409
        assert client.post('/api/answer/cancel',json={}).status_code==200
        assert ws.receive_json()['status']=='cancelled'
        assert module.state.answer=='开始'


def test_transcribed_question_is_punctuated_before_answer(client, monkeypatch):
    seen = []
    async def punctuate(config, question):
        return '请介绍项目，并说明你的职责。'
    async def answer(config, question, profile, history):
        seen.append(question)
        yield '回答'
    monkeypatch.setattr(module, 'punctuate_question', punctuate)
    monkeypatch.setattr(module, 'stream_answer', answer)
    monkeypatch.setitem(module.CONFIG, 'llm_key', 'fake')
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'token': module.TOKEN}); ws.receive_json()
        assert client.post('/api/answer', json={'question': '请介绍项目并说明你的职责', 'transcribed': True}).status_code == 200
        events = []
        while not events or events[-1]['type'] != 'answer_end':
            events.append(ws.receive_json())
    assert seen == ['请介绍项目，并说明你的职责。']
    assert next(x for x in events if x['type'] == 'question_normalized')['question'] == seen[0]


def test_punctuation_only_accepts_unchanged_words(monkeypatch):
    import httpx
    from interview import answers
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return httpx.Response(200, json={'choices': [{'message': {'content': self.result}}]}, request=httpx.Request('POST', 'http://test'))
    client = Client()
    monkeypatch.setattr(answers.httpx, 'AsyncClient', lambda **kwargs: client)
    config = {'llm_url': 'http://test', 'llm_key': 'fake', 'llm_model': 'test'}
    client.result = '请介绍项目，并说明你负责的部分。'
    assert asyncio.run(punctuate_question(config, '请介绍项目并说明你负责的部分')) == client.result
    client.result = '请介绍产品，并说明你负责的部分。'
    assert asyncio.run(punctuate_question(config, '请介绍项目并说明你负责的部分')) == '请介绍项目并说明你负责的部分'


def test_audio_downmix_and_prompt():
    data=np.array([[32767,32767],[-32768,-32768],[1000,-1000]],dtype=np.int16).tobytes()
    pcm,level=mono_pcm(data,2)
    assert np.frombuffer(pcm,dtype=np.int16).tolist()==[32767,-32768,0]
    assert 0 < level <= 1
    result=messages('追问','经历',[{'role':'remote','text':'介绍项目','final':True},
                                    {'role':'self','text':'我做过A','final':True},
                                    {'role':'remote','text':'未定稿','final':False}])
    payload=json.loads(result[1]['content'])
    assert payload['面试官提问历史']==['介绍项目']
    assert payload['当前问题']=='追问'


def test_remote_audio_needs_no_microphone(client, monkeypatch):
    class FakeAudio:
        def __init__(self, selected, *args):
            assert selected == {'remote': 8}
            self.id = 'test'
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr(module, 'AudioSession', FakeAudio)
    monkeypatch.setattr(module, 'devices', lambda: {'remote': [{'id': 8}], 'self': []})
    assert client.post('/api/audio/start', json={'remote': 8}).status_code == 200
    assert client.post('/api/audio/stop', json={}).status_code == 200
    assert client.post('/api/transcript', json={'role': 'self', 'text': '过去回答'}).status_code == 422


def test_local_asr_needs_model_not_cloud_key(client, monkeypatch):
    monkeypatch.setitem(module.CONFIG, 'asr_backend', 'sherpa')
    monkeypatch.setattr(module, 'local_asr_ready', lambda config: False)
    response = client.post('/api/audio/start', json={'remote': 8, 'transcribe': True})
    assert response.status_code == 400
    assert 'download-asr.bat' in response.json()['detail']
    monkeypatch.setattr(module, 'local_asr_ready', lambda config: True)
    monkeypatch.setattr(module, 'devices', lambda: {'remote': [{'id': 8}], 'self': []})
    class LocalAudio:
        id = 'local'
        def __init__(self, selected, transcribe, config, emit):
            assert selected == {'remote': 8}
            assert transcribe and config['asr_key'] == ''
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr(module, 'AudioSession', LocalAudio)
    assert client.post('/api/audio/start', json={'remote': 8, 'transcribe': True}).status_code == 200
    assert client.get('/api/state').json()['configured']['asr_backend'] == 'sherpa'


def test_model_settings_persist_redact_and_destination_change(client, monkeypatch, tmp_path):
    from interview import settings
    monkeypatch.setattr(settings, 'SETTINGS_PATH', tmp_path / 'model.json')
    body = {'base_url': 'https://models.example/v1', 'model': 'custom-a',
            'api_key': 'test-secret-not-real', 'remember': True}
    response = client.post('/api/model', json=body)
    assert response.status_code == 200
    assert response.json()['has_key'] is True
    assert 'test-secret' not in response.text
    assert 'test-secret' not in client.get('/api/model').text
    restored = {}
    settings.load_settings(restored)
    assert restored['llm_model'] == 'custom-a'
    assert restored['llm_key'] == 'test-secret-not-real'
    body.update(model='custom-b', api_key='', remember=False)
    assert client.post('/api/model', json=body).json()['has_key'] is True
    body['base_url'] = 'https://other.example/v1'
    assert client.post('/api/model', json=body).json()['has_key'] is False
    assert client.post('/api/model', json={**body, 'base_url': 'http://remote.example/v1'}).status_code == 422
    assert client.get('/api/model', headers={'Authorization': ''}).status_code == 401


def test_selected_model_is_used_for_answer(client, monkeypatch):
    received = []
    async def provider(config, *args):
        received.append(config)
        yield '下一步建议'
    monkeypatch.setattr(module, 'stream_answer', provider)
    assert client.post('/api/model', json={'base_url': 'https://models.example/v1',
        'model': 'chosen-model', 'api_key': 'test-key'}).status_code == 200
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'token': module.TOKEN}); ws.receive_json()
        client.post('/api/answer', json={'question': '如何设计？'})
        while ws.receive_json()['type'] != 'answer_end': pass
    assert received[0]['llm_model'] == 'chosen-model'
    assert received[0]['llm_url'] == 'https://models.example/v1'


def test_microphone_is_local_test_only(client, monkeypatch):
    captures = []
    class FakeAudio:
        def __init__(self, selected, transcribe, *args):
            captures.append((selected, transcribe))
            self.id = 'test'
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr(module, 'AudioSession', FakeAudio)
    monkeypatch.setattr(module, 'devices', lambda: {'remote': [{'id': 8}], 'self': [{'id': 3}]})
    assert client.post('/api/audio/start', json={'microphone': 3}).status_code == 200
    assert captures[-1] == ({'self': 3}, False)
    client.post('/api/audio/stop', json={})
    monkeypatch.setitem(module.CONFIG, 'asr_key', 'fake')
    assert client.post('/api/audio/start', json={'remote': 8, 'microphone': 3, 'transcribe': True}).status_code == 200
    assert captures[-1] == ({'remote': 8}, True)


def test_device_test_lifecycle_and_pcm_privacy(client, monkeypatch):
    instances = []
    class FakeTest:
        def __init__(self, kind, index, emit, clip):
            self.id = 'device-' + str(len(instances))
            self.clip = clip
            instances.append(self)
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr(module, 'DeviceTest', FakeTest)
    monkeypatch.setattr(module, 'devices', lambda: {'remote': [{'id': 8}], 'self': [{'id': 3}]})
    assert client.post('/api/audio/test', json={'kind': 'tone', 'device': 8}, headers={'Authorization': ''}).status_code == 401
    assert client.post('/api/audio/test', json={'kind': 'replay', 'device': 8}).status_code == 400
    assert client.post('/api/audio/test', json={'kind': 'record', 'device': 8}).status_code == 400
    assert client.post('/api/audio/test', json={'kind': 'record', 'device': 3}).status_code == 200
    assert client.post('/api/audio/start', json={'remote': 8}).status_code == 409
    s = module.state
    s.event({'type': 'device_test', 'session': instances[-1].id, 'finished': True,
             'phase': 'ready', 'message': 'ready', 'clip': (b'private-pcm', 48000)})
    snapshot = client.get('/api/state')
    assert snapshot.json()['device_test']['has_clip'] is True
    assert 'private-pcm' not in snapshot.text
    assert s.audio is None
    assert client.post('/api/audio/test', json={'kind': 'replay', 'device': 8}).status_code == 200
    assert instances[-1].clip == (b'private-pcm', 48000)
    client.post('/api/audio/stop', json={})
    assert client.post('/api/audio/test', json={'kind': 'record', 'device': 3}).status_code == 200
    assert s.test_clip is None
    old_id = instances[-1].id
    client.post('/api/audio/stop', json={})
    s.event({'type': 'device_test', 'session': old_id, 'finished': True, 'clip': (b'late', 48000)})
    assert s.test_clip is None


def test_five_second_recording_and_selected_playback(monkeypatch):
    from interview import device_test as dt
    opened, written, events = [], [], []
    class Stream:
        def stop_stream(self): pass
        def close(self): pass
        def write(self, data): written.append(data)
    class Driver:
        def get_device_info_by_index(self, index):
            if index == 3:
                return {'maxInputChannels': 1, 'defaultSampleRate': 1000}
            return {'isLoopbackDevice': True, 'name': 'Chosen [Loopback]', 'hostApi': 2}
        def get_device_info_generator(self):
            return iter([{'name': 'Other', 'hostApi': 2, 'maxOutputChannels': 2, 'index': 4},
                         {'name': 'Chosen', 'hostApi': 2, 'maxOutputChannels': 2,
                          'index': 6, 'defaultSampleRate': 2000}])
        def open(self, **kwargs):
            opened.append(kwargs)
            if kwargs.get('input'):
                for _ in range(50):
                    kwargs['stream_callback'](np.full(100, 1000, dtype=np.int16).tobytes(), 100, None, 0)
            return Stream()
        def terminate(self): pass
    monkeypatch.setattr(dt.pa, 'PyAudio', Driver)
    test = dt.DeviceTest('record', 3, events.append)
    test.run()
    clip = events[-1]['clip']
    assert len(clip[0]) == 5 * 1000 * 2
    assert events[-1]['phase'] == 'ready'
    assert events[-2]['remaining'] == 0
    dt.DeviceTest('replay', 8, events.append, clip).run()
    assert opened[-1]['output_device_index'] == 6
    assert sum(map(len, written)) == 5 * 2000 * 2 * 2
    assert events[-1]['phase'] == 'done'
    cancelled = dt.DeviceTest('record', 3, events.append)
    cancelled.halt.set()
    cancelled.run()
    assert events[-1]['phase'] == 'cancelled'
    assert 'clip' not in events[-1]
