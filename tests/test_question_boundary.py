import asyncio
import httpx
import pytest
from interview import answers


@pytest.mark.parametrize('result,expected', [('NEW', 'new'), ('CONTINUE', 'continue'), ('不确定', 'uncertain'), ('UNCERTAIN', 'uncertain')])
def test_boundary_conservative_results(monkeypatch, result, expected):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            assert '已有提问' in kwargs['json']['messages'][1]['content']
            return httpx.Response(200, json={'choices': [{'message': {'content': result}}]}, request=httpx.Request('POST', 'http://test'))
    monkeypatch.setattr(answers.httpx, 'AsyncClient', lambda **kwargs: Client())
    config = {'llm_url': 'http://test', 'llm_key': 'fake', 'llm_model': 'test'}
    assert asyncio.run(answers.question_boundary(config, '你如何设计缓存', '还有一个条件内存只有一百兆')) == expected


def test_boundary_timeout_keeps_previous_question(monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr(answers.httpx, 'AsyncClient', lambda **kwargs: Client())
    config = {'llm_url': 'http://test', 'llm_key': 'fake', 'llm_model': 'test'}
    assert asyncio.run(answers.question_boundary(config, '原问题', '新增条件')) == 'uncertain'
