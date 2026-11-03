"""Offline contract tests for the Ollama- and Anthropic-compatible adapter routes."""
import json

import httpx
import httpx2
import pytest

import proxy
from test_proxy import Session


class SSEStream(httpx2.AsyncByteStream):
    def __init__(self, frames):
        self.frames = frames

    async def __aiter__(self):
        for frame in self.frames:
            yield frame

    async def aclose(self):
        pass


def compat_app(handler):
    requests = []

    async def upstream(request):
        requests.append(request)
        return handler(request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(upstream), follow_redirects=False)
    app = proxy.create_app(Session, lambda **kwargs: client)
    return app, requests


MODELS_UPSTREAM = {'object': 'list', 'data': [
    {'id': 'mistral-large-4', 'object': 'model', 'capabilities': {'tools': True}},
    {'id': 'ministral-3b-2512', 'object': 'model', 'capabilities': {'tools': True}},
]}

CHAT_UPSTREAM = {'id': 'chat-1', 'model': 'mistral-large-4', 'choices': [
    {'index': 0, 'finish_reason': 'stop',
     'message': {'role': 'assistant', 'content': 'OK'}}],
    'usage': {'prompt_tokens': 8, 'completion_tokens': 2}}

TOOL_CHAT_UPSTREAM = {'id': 'chat-2', 'model': 'mistral-large-4', 'choices': [
    {'index': 0, 'finish_reason': 'tool_calls',
     'message': {'role': 'assistant', 'content': '',
                 'tool_calls': [{'id': 'call-1', 'type': 'function',
                                 'function': {'name': 'read_file', 'arguments': '{"path":"x"}'}}]}}],
    'usage': {'prompt_tokens': 8, 'completion_tokens': 5}}

EMBED_UPSTREAM = {'data': [{'embedding': [0.1, 0.2], 'index': 0}]}


def json_upstream(payload, status=200):
    return lambda request: httpx2.Response(status, json=payload)


async def call(app, method, path, *, body=None, headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
        return await client.request(method, path, content=body, headers=headers)


# ---------- Ollama ----------

@pytest.mark.anyio
async def test_ollama_version_needs_no_upstream():
    app, requests = compat_app(json_upstream({}))
    async with app.router.lifespan_context(app):
        response = await call(app, 'GET', '/api/version')
    assert response.status_code == 200
    assert response.json()['version'].startswith('0.0.0-mvibe')
    assert not requests


@pytest.mark.anyio
async def test_ollama_tags_maps_upstream_models():
    app, requests = compat_app(json_upstream(MODELS_UPSTREAM))
    async with app.router.lifespan_context(app):
        response = await call(app, 'GET', '/api/tags')
    assert response.status_code == 200
    names = [m['name'] for m in response.json()['models']]
    assert names == ['mistral-large-4', 'ministral-3b-2512']
    assert str(requests[0].url).endswith('/v1/models')


@pytest.mark.anyio
async def test_ollama_chat_translates_request_and_response():
    app, requests = compat_app(json_upstream(CHAT_UPSTREAM))
    body = json.dumps({'model': 'mistral-large-4',
                       'messages': [{'role': 'user', 'content': 'Reply OK.'}],
                       'options': {'temperature': 0.2, 'num_predict': 32},
                       'stream': False}).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/api/chat', body=body)
    assert response.status_code == 200
    sent = json.loads(requests[0].content)
    assert sent['model'] == 'mistral-large-4' and sent['stream'] is False
    assert sent['messages'] == [{'role': 'user', 'content': 'Reply OK.'}]
    assert sent['temperature'] == 0.2 and sent['max_tokens'] == 32
    out = response.json()
    assert out['done'] is True and out['message'] == {'role': 'assistant', 'content': 'OK'}
    assert out['prompt_eval_count'] == 8 and out['eval_count'] == 2
    assert response.headers['x-request-id']


@pytest.mark.anyio
async def test_ollama_chat_stream_ndjson():
    frames = [b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
              b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
              b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n',
              b'data: [DONE]\n\n']
    app, requests = compat_app(lambda request: httpx2.Response(
        200, stream=SSEStream(frames), headers={'content-type': 'text/event-stream'}))
    body = json.dumps({'model': 'mistral-large-4',
                       'messages': [{'role': 'user', 'content': 'hi'}], 'stream': True}).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/api/chat', body=body)
    assert response.status_code == 200
    assert json.loads(requests[0].content)['stream'] is True
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert [line.get('message', {}).get('content') for line in lines[:2]] == ['Hel', 'lo']
    assert lines[-1]['done'] is True and lines[-1]['eval_count'] == 2
    assert all(line['done'] is False for line in lines[:-1])


@pytest.mark.anyio
async def test_ollama_chat_tool_calls_roundtrip():
    app, requests = compat_app(json_upstream(TOOL_CHAT_UPSTREAM))
    body = json.dumps({'model': 'mistral-large-4',
                       'messages': [{'role': 'user', 'content': 'read x'}],
                       'tools': [{'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object'}}}]}).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/api/chat', body=body)
    sent = json.loads(requests[0].content)
    assert sent['tools'][0]['function']['name'] == 'read_file'
    out = response.json()
    assert out['message']['tool_calls'][0]['function']['name'] == 'read_file'


@pytest.mark.anyio
async def test_ollama_generate_wraps_prompt():
    app, requests = compat_app(json_upstream(CHAT_UPSTREAM))
    body = json.dumps({'model': 'mistral-large-4', 'prompt': 'Say OK.', 'system': 'Be brief.'}).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/api/generate', body=body)
    sent = json.loads(requests[0].content)
    assert sent['messages'] == [{'role': 'system', 'content': 'Be brief.'},
                                {'role': 'user', 'content': 'Say OK.'}]
    assert response.json()['response'] == 'OK' and response.json()['done'] is True


@pytest.mark.anyio
@pytest.mark.parametrize(('think', 'expected'), [
    (False, 'none'), ('low', 'low'), ('medium', 'medium'), ('high', 'high'),
    (True, None), ('xhigh', None), (7, None),
])
async def test_ollama_chat_think_maps_to_reasoning_effort(think, expected):
    app, requests = compat_app(json_upstream(CHAT_UPSTREAM))
    body = {'model': 'mistral-large-4', 'messages': [{'role': 'user', 'content': 'hi'}], 'stream': False, 'think': think}
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/api/chat', body=json.dumps(body).encode())
    assert response.status_code == 200
    sent = json.loads(requests[0].content)
    if expected is None:
        assert 'reasoning_effort' not in sent
    else:
        assert sent['reasoning_effort'] == expected


@pytest.mark.anyio
async def test_ollama_generate_think_maps_to_reasoning_effort():
    app, requests = compat_app(json_upstream(CHAT_UPSTREAM))
    body = json.dumps({'model': 'mistral-large-4', 'prompt': 'Say OK.', 'think': False}).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/api/generate', body=body)
    assert response.status_code == 200
    assert json.loads(requests[0].content)['reasoning_effort'] == 'none'


@pytest.mark.anyio
async def test_ollama_chat_without_think_adds_no_field():
    app, requests = compat_app(json_upstream(CHAT_UPSTREAM))
    body = json.dumps({'model': 'mistral-large-4', 'messages': [{'role': 'user', 'content': 'hi'}]}).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/api/chat', body=body)
    assert response.status_code == 200
    assert 'reasoning_effort' not in json.loads(requests[0].content)


@pytest.mark.anyio
async def test_ollama_show_and_embed():
    app, requests = compat_app(lambda request: httpx2.Response(
        200, json=MODELS_UPSTREAM if request.url.path.endswith('/models') else EMBED_UPSTREAM))
    async with app.router.lifespan_context(app):
        show = await call(app, 'POST', '/api/show', body=json.dumps({'model': 'mistral-large-4'}).encode())
        missing = await call(app, 'POST', '/api/show', body=json.dumps({'model': 'nope'}).encode())
        embed = await call(app, 'POST', '/api/embed',
                           body=json.dumps({'model': 'mistral-large-4', 'input': ['a', 'b']}).encode())
    assert show.status_code == 200 and show.json()['model_info']['general.architecture'] == 'mistral'
    assert missing.status_code == 404 and 'error' in missing.json()
    assert embed.json() == {'embeddings': [[0.1, 0.2]]}
    assert json.loads(requests[-1].content) == {'model': 'mistral-large-4', 'input': ['a', 'b']}


# ---------- Anthropic ----------

@pytest.mark.anyio
async def test_anthropic_messages_translates_blocks_and_response():
    app, requests = compat_app(json_upstream(TOOL_CHAT_UPSTREAM))
    body = json.dumps({
        'model': 'mistral-large-4', 'max_tokens': 64, 'stream': False,
        'system': 'Be terse.',
        'messages': [
            {'role': 'user', 'content': [{'type': 'text', 'text': 'read x'}]},
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call-1', 'name': 'read_file', 'input': {'path': 'x'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_call_id': 'call-1', 'content': 'file-bytes'}]},
        ],
        'tools': [{'name': 'read_file', 'description': 'read', 'input_schema': {'type': 'object'}}],
    }).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/v1/messages', body=body,
                              headers={'anthropic-version': '2023-06-01'})
    assert response.status_code == 200
    sent = json.loads(requests[0].content)
    assert sent['max_tokens'] == 64 and sent['stream'] is False
    assert sent['messages'][0] == {'role': 'system', 'content': 'Be terse.'}
    assert sent['messages'][1] == {'role': 'user', 'content': 'read x'}
    assert sent['messages'][2]['tool_calls'][0]['function']['name'] == 'read_file'
    assert sent['messages'][3] == {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'file-bytes'}
    assert sent['tools'] == [{'type': 'function', 'function': {
        'name': 'read_file', 'description': 'read', 'parameters': {'type': 'object'}}}]
    out = response.json()
    assert out['type'] == 'message' and out['role'] == 'assistant'
    assert out['stop_reason'] == 'tool_use'
    assert {'type': 'tool_use', 'id': 'call-1', 'name': 'read_file', 'input': {'path': 'x'}} in out['content']
    assert out['usage'] == {'input_tokens': 8, 'output_tokens': 5}


@pytest.mark.anyio
async def test_anthropic_messages_stream_events():
    frames = [b'data: {"id":"chat-1","choices":[{"delta":{"role":"assistant","content":""}}],"usage":{"prompt_tokens":4,"completion_tokens":0}}\n\n',
              b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n',
              b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n',
              b'data: [DONE]\n\n']
    app, requests = compat_app(lambda request: httpx2.Response(
        200, stream=SSEStream(frames), headers={'content-type': 'text/event-stream'}))
    body = json.dumps({'model': 'mistral-large-4', 'max_tokens': 16,
                       'messages': [{'role': 'user', 'content': 'hi'}], 'stream': True}).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/v1/messages', body=body,
                              headers={'anthropic-version': '2023-06-01'})
    assert response.status_code == 200
    assert json.loads(requests[0].content)['stream'] is True
    events = []
    for block in response.text.strip().split('\n\n'):
        event, _, data = block.partition('\n')
        events.append((event.removeprefix('event: '), json.loads(data.removeprefix('data: '))))
    names = [name for name, _ in events]
    assert names[0] == 'message_start' and names[-1] == 'message_stop'
    assert names.count('message_stop') == 1 and names.count('message_delta') == 1
    assert 'content_block_start' in names and 'content_block_delta' in names and 'message_delta' in names
    delta = [data for name, data in events if name == 'content_block_delta'][0]
    assert delta['delta'] == {'type': 'text_delta', 'text': 'OK'}
    md = [data for name, data in events if name == 'message_delta'][0]
    assert md['delta']['stop_reason'] == 'end_turn' and md['usage']['output_tokens'] == 2


@pytest.mark.anyio
@pytest.mark.parametrize(('thinking', 'expected'), [
    ({'type': 'disabled'}, 'none'),
    ({'type': 'enabled', 'budget_tokens': 32000}, 'high'),
    ({'type': 'enabled', 'budget_tokens': 20000}, 'medium'),
    ({'type': 'enabled', 'budget_tokens': 4096}, 'low'),
    ({'type': 'enabled', 'budget_tokens': 1024}, 'low'),
    ({'type': 'enabled'}, 'high'),
    ({'type': 'enabled', 'effort': 'xhigh'}, 'xhigh'),
    ({'type': 'adaptive'}, None),
    ({'type': 'enabled', 'budget_tokens': 'bad'}, None),
    ({'type': 'enabled', 'budget_tokens': -1}, None),
    ({'type': 'enabled', 'budget_tokens': True}, None),
    ({'type': 'enabled', 'budget_tokens': None}, None),
    ({'type': 'enabled', 'effort': []}, None),
    ({'type': 'enabled', 'effort': ''}, None),
    ({'type': 'enabled', 'effort': '   '}, None),
    ('not-a-dict', None),
])
async def test_anthropic_messages_thinking_maps_to_reasoning_effort(thinking, expected):
    app, requests = compat_app(json_upstream(CHAT_UPSTREAM))
    body = json.dumps({'model': 'mistral-large-4', 'max_tokens': 16,
                       'messages': [{'role': 'user', 'content': 'hi'}],
                       'thinking': thinking}).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/v1/messages', body=body,
                              headers={'anthropic-version': '2023-06-01'})
    assert response.status_code == 200
    sent = json.loads(requests[0].content)
    if expected is None:
        assert 'reasoning_effort' not in sent
    else:
        assert sent['reasoning_effort'] == expected


@pytest.mark.anyio
async def test_anthropic_messages_without_thinking_adds_no_field():
    app, requests = compat_app(json_upstream(CHAT_UPSTREAM))
    body = json.dumps({'model': 'mistral-large-4', 'max_tokens': 16,
                       'messages': [{'role': 'user', 'content': 'hi'}]}).encode()
    async with app.router.lifespan_context(app):
        response = await call(app, 'POST', '/v1/messages', body=body,
                              headers={'anthropic-version': '2023-06-01'})
    assert response.status_code == 200
    assert 'reasoning_effort' not in json.loads(requests[0].content)


@pytest.mark.anyio
async def test_models_dialect_switch_on_anthropic_version_header():
    app, requests = compat_app(json_upstream(MODELS_UPSTREAM))
    async with app.router.lifespan_context(app):
        openai = await call(app, 'GET', '/v1/models')
        anthropic = await call(app, 'GET', '/v1/models', headers={'anthropic-version': '2023-06-01'})
    assert 'data' in openai.json() and isinstance(openai.json()['data'][0].get('id'), str)
    body = anthropic.json()
    assert body['has_more'] is False and body['first_id'] == 'mistral-large-4'
    assert body['data'][0]['type'] == 'model'


# ---------- shared safety ----------

@pytest.mark.anyio
@pytest.mark.parametrize('method,path,status', [('GET', '/api/nope', 404), ('DELETE', '/api/chat', 405),
                                                 ('POST', '/api/chat', 400)])
async def test_adapter_safety(method, path, status):
    app, requests = compat_app(json_upstream(CHAT_UPSTREAM))
    body = b'not json' if (method, path, status) == ('POST', '/api/chat', 400) else None
    async with app.router.lifespan_context(app):
        response = await call(app, method, path, body=body)
    assert response.status_code == status
    if status == 404:
        assert 'error' in response.json()
    if status == 400:
        assert 'error' in response.json() and not requests


@pytest.mark.anyio
async def test_upstream_error_maps_to_adapter_shape_without_leaking_body(capsys):
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    try:
        app, requests = compat_app(json_upstream({'error': {'message': 'secret-upstream-detail', 'type': 'invalid'}}, status=422))
        async with app.router.lifespan_context(app):
            response = await call(app, 'POST', '/api/chat',
                                  body=json.dumps({'model': 'mistral-large-4', 'messages': []}).encode())
        assert response.status_code == 422
        body = response.json()
        assert 'error' in body and 'secret-upstream-detail' not in response.text
        assert 'reasoning' in body['error']
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows[-1]['outcome'] == 'upstream_error' and rows[-1]['status'] == 422
        assert 'secret' not in json.dumps(rows)
    finally:
        diagnostics.disable_stdout()


@pytest.mark.anyio
async def test_adapter_routes_emit_correlated_diagnostics(capsys):
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    try:
        app, _ = compat_app(json_upstream(CHAT_UPSTREAM))
        async with app.router.lifespan_context(app):
            response = await call(app, 'POST', '/api/chat',
                                  body=json.dumps({'model': 'mistral-large-4',
                                                   'messages': [{'role': 'user', 'content': 'secret-prompt'}]}).encode())
        row = json.loads(capsys.readouterr().out)
        assert row['request_id'] == response.headers['x-request-id']
        assert row['model'] == 'mistral-large-4'
        assert 'secret' not in json.dumps(row)
    finally:
        diagnostics.disable_stdout()


@pytest.mark.anyio
async def test_openapi_documents_adapter_operations():
    app = proxy.create_app()
    paths = app.openapi()['paths']
    for path in ['/api/version', '/api/tags', '/api/chat', '/api/generate', '/api/show', '/api/embed', '/v1/messages']:
        assert path in paths, path
