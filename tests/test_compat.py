"""Offline contract tests for the Ollama- and Anthropic-compatible adapter routes."""
import asyncio
import json

import httpx
import httpx2
import pytest

import proxy
from fakes import Clock
from mvibe_auth.api import AuthError, AuthFailure
from mvibe_proxy import compat, diagnostics
from test_proxy import (SHORT_WAIT, Session, answers, body_of, clocked_gate, harness,
                        raw_asgi)


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


# ---------- Rate-limit gate on the adapter routes (upstream-rate-limit-queue 2.5) ----------
# Requirements 2.3, 2.4, 2.6, 6.4: adapter routes acquire through the stream
# owner's disconnect watcher, and an exhausted upstream 429 keeps its dialect
# error body while carrying the upstream rate-limit guidance headers.

RATE_LIMITED_BODY = b'{"object":"error","message":"Rate limit exceeded","type":"rate_limited"}'
RATE_LIMITED_HEADERS = {'content-type': 'application/json', 'retry-after': '30',
                        'x-ratelimit-limit-req-minute': '5', 'x-ratelimit-remaining-req-minute': '0'}
OLLAMA_CHAT = json.dumps({'model': 'mistral-large-4', 'stream': False,
                          'messages': [{'role': 'user', 'content': 'hi'}]}).encode()
ANTHROPIC_MESSAGES = json.dumps({'model': 'mistral-large-4', 'max_tokens': 32, 'stream': False,
                                 'messages': [{'role': 'user', 'content': 'hi'}]}).encode()


def holding_sleep(clock):
    """A gate sleep that blocks until `release` is set; `holding` is set as each wait starts."""
    holding, release = asyncio.Event(), asyncio.Event()

    async def sleep(seconds):
        holding.set()
        await release.wait()
        clock.advance(seconds)
    return sleep, holding, release


def client_after_body(body, then, events):
    """receive() delivering `body` once, then answering every later call with `then()`."""
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            events.append('body')
            return {'type': 'http.request', 'body': body, 'more_body': False}
        events.append('watching')
        return await then()
    return receive


def leaves_when(gone):
    async def disconnect():
        await gone.wait()
        return {'type': 'http.disconnect'}
    return disconnect


async def eventually(condition):
    async def poll():
        while not condition():
            await asyncio.sleep(0)
    await asyncio.wait_for(poll(), 5)


def held_then_accepted(clock):
    """An upstream rejecting the first send with Retry-After 2, accepting every later one."""
    return answers(clock, (429, {'retry-after': '2'}, b'{"message":"rate limited"}'),
                   (200, {}, json.dumps(CHAT_UPSTREAM).encode()))


@pytest.mark.anyio
@pytest.mark.parametrize(('path', 'dialect', 'body', 'headers'), [
    ('/api/chat', 'ollama', OLLAMA_CHAT, {}),
    ('/v1/messages', 'anthropic', ANTHROPIC_MESSAGES, {'anthropic-version': '2023-06-01'}),
])
async def test_adapter_exhaustion_keeps_the_dialect_error_and_carries_rate_limit_headers(
        capsys, path, dialect, body, headers):
    clock = Clock()
    app, _, sent, _ = harness(
        upstream=answers(clock, (429, RATE_LIMITED_HEADERS, RATE_LIMITED_BODY)),
        rate_limit_config=SHORT_WAIT, gate_factory=clocked_gate(clock))
    diagnostics.configure_stdout()
    try:
        async with app.router.lifespan_context(app):
            response = await asyncio.wait_for(call(app, 'POST', path, body=body, headers=headers), 5)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    finally:
        diagnostics.disable_stdout()
    # Retry-After 30 s cannot be honoured within the 0.5 s maximum wait: one send,
    # then the gate's materialized 429, translated into the route's dialect.
    assert len(sent) == 1
    assert response.status_code == 429
    assert response.json() == compat.adapter_error(dialect, 429, compat.upstream_error_hint(429))[1]
    assert 'Rate limit exceeded' not in response.text
    if dialect == 'anthropic':
        assert response.json()['error']['type'] == 'rate_limit_error'
    assert response.headers['retry-after'] == '30'
    assert response.headers['x-ratelimit-limit-req-minute'] == '5'
    assert response.headers['x-ratelimit-remaining-req-minute'] == '0'
    row = rows[-1]
    assert (row['outcome'], row['status']) == ('rate_limited', 429)
    assert row['request_id'] == response.headers['x-request-id']
    assert 'Rate limit exceeded' not in json.dumps(rows)


@pytest.mark.anyio
@pytest.mark.parametrize(('path', 'body', 'headers'), [
    ('/v1/chat/completions', OLLAMA_CHAT, {}),
    ('/api/chat', OLLAMA_CHAT, {}),
    ('/v1/messages', ANTHROPIC_MESSAGES, {'anthropic-version': '2023-06-01'}),
])
async def test_an_exhausted_429_with_non_ascii_guidance_reaches_the_client_byte_exact(
        path, body, headers):
    # A latin-1 x-ratelimit-* value and mixed-case names: the exhausted 429 is
    # still a 429 (not a 502), carrying the upstream bytes unchanged.
    clock = Clock()

    def upstream(request):
        return httpx2.Response(429, content=RATE_LIMITED_BODY, headers=[
            (b'Content-Type', b'application/json'), (b'Retry-After', b'30'),
            (b'X-RateLimit-Reset', b'caf\xe9')])

    app, _, sent, _ = harness(upstream=upstream, rate_limit_config=SHORT_WAIT,
                              gate_factory=clocked_gate(clock))
    async with app.router.lifespan_context(app):
        response = await asyncio.wait_for(call(app, 'POST', path, body=body, headers=headers), 5)
    assert len(sent) == 1
    assert response.status_code == 429
    raw = dict(response.headers.raw)
    assert (raw[b'retry-after'], raw[b'x-ratelimit-reset']) == (b'30', b'caf\xe9')


@pytest.mark.anyio
async def test_an_adapter_client_leaving_during_its_hold_is_cancelled_and_never_resent():
    clock = Clock()
    sleep, holding, release = holding_sleep(clock)
    gone = asyncio.Event()
    app, session, sent, _ = harness(upstream=held_then_accepted(clock),
                                    gate_factory=clocked_gate(clock, sleep=sleep))
    messages, scopes = [], []
    async with app.router.lifespan_context(app):
        gate = app.state.rate_gate
        task = asyncio.create_task(raw_asgi(
            app, 'POST', '/api/chat', events=session.events, messages=messages, scopes=scopes,
            receive=client_after_body(OLLAMA_CHAT, leaves_when(gone), session.events)))
        try:
            await asyncio.wait_for(holding.wait(), 5)
            # Held after the 429: one send, nothing toward the client.
            assert len(sent) == 1 and messages == [] and not task.done()
            gone.set()
            # An ordinary disconnect: the app returns normally, within the bound.
            await asyncio.wait_for(task, 5)
            assert gate.held == 0
            # Ending the hold now finds no waiter left to send.
            release.set()
            for _ in range(10):
                await asyncio.sleep(0)
        finally:
            task.cancel()
    assert len(sent) == 1
    assert scopes[0]['mvibe.diagnostic'].outcome == 'cancelled'
    starts = [m for m in messages if m['type'] == 'http.response.start']
    assert not starts or [m['status'] for m in starts] == [204]
    assert body_of(messages) == b''


@pytest.mark.anyio
async def test_an_adapter_request_waiting_behind_a_hold_and_abandoned_is_never_sent():
    clock = Clock()
    sleep, holding, release = holding_sleep(clock)
    gone = asyncio.Event()
    second = json.dumps({'model': 'mistral-large-4', 'stream': False,
                         'messages': [{'role': 'user', 'content': 'second-request'}]}).encode()
    app, session, sent, _ = harness(upstream=held_then_accepted(clock),
                                    gate_factory=clocked_gate(clock, sleep=sleep))
    first_messages, messages, scopes, events = [], [], [], []
    async with app.router.lifespan_context(app):
        gate = app.state.rate_gate
        first = asyncio.create_task(raw_asgi(
            app, 'POST', '/api/chat', body=OLLAMA_CHAT, events=session.events,
            messages=first_messages))
        task = None
        try:
            await asyncio.wait_for(holding.wait(), 5)
            # The model is now held: the next request for it waits behind the hold.
            task = asyncio.create_task(raw_asgi(
                app, 'POST', '/api/chat', events=events, messages=messages, scopes=scopes,
                receive=client_after_body(second, leaves_when(gone), events)))
            await eventually(lambda: gate.held == 2 and 'watching' in events)
            gone.set()
            await asyncio.wait_for(task, 5)
            assert gate.held == 1
            release.set()
            await asyncio.wait_for(first, 5)
        finally:
            first.cancel()
            if task is not None:
                task.cancel()
    # The abandoned request produced zero upstream sends; the first was re-sent once.
    assert not [request for request in sent if b'second-request' in request.content]
    assert len(sent) == 2
    assert scopes[0]['mvibe.diagnostic'].outcome == 'cancelled'
    starts = [m for m in messages if m['type'] == 'http.response.start']
    assert not starts or [m['status'] for m in starts] == [204]
    assert body_of(messages) == b''
    assert first_messages[0]['status'] == 200
    assert json.loads(body_of(first_messages))['message'] == {'role': 'assistant', 'content': 'OK'}


@pytest.mark.anyio
@pytest.mark.parametrize(('failure', 'status', 'outcome', 'message'), [
    (lambda: RuntimeError('receive failed'), 502, 'connection_failed', 'Upstream connection failed'),
    (lambda: AuthError(AuthFailure('rejected')), 401, 'auth_failed',
     'Vibe authentication failed; run proxy.py --login'),
], ids=['runtime-error', 'auth-error'])
async def test_a_failing_receive_during_an_adapter_hold_is_answered_safely(failure, status, outcome, message):
    clock = Clock()
    sleep, holding, release = holding_sleep(clock)

    async def broken():
        await holding.wait()
        raise failure()

    app, session, sent, _ = harness(upstream=held_then_accepted(clock),
                                    gate_factory=clocked_gate(clock, sleep=sleep))
    messages, scopes = [], []
    async with app.router.lifespan_context(app):
        gate = app.state.rate_gate
        # Returns normally: the failure becomes the 502 answer, not an escaping error.
        await asyncio.wait_for(raw_asgi(
            app, 'POST', '/api/chat', events=session.events, messages=messages, scopes=scopes,
            receive=client_after_body(OLLAMA_CHAT, broken, session.events)), 5)
        assert gate.held == 0
        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
    assert len(sent) == 1
    # The watcher's task group wraps the failure: a group of auth failures is
    # the 401 answer, anything else the 502 connection failure.
    assert messages[0]['type'] == 'http.response.start' and messages[0]['status'] == status
    assert json.loads(body_of(messages)) == {'error': {'message': message}}
    assert scopes[0]['mvibe.diagnostic'].outcome == outcome


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['auth', 'connection'])
async def test_adapter_pre_response_failures_keep_their_safe_answers(failure):
    app, session, sent, _ = harness(fail_send=failure == 'connection')
    if failure == 'auth':
        session.failure = AuthFailure('rejected')
    async with app.router.lifespan_context(app):
        response = await asyncio.wait_for(call(app, 'POST', '/api/chat', body=OLLAMA_CHAT), 5)
    assert 'secret' not in response.text
    if failure == 'auth':
        assert response.status_code == 401 and '--login' in response.text and not sent
    else:
        assert response.status_code == 502
        assert response.json() == {'error': {'message': 'Upstream connection failed'}}


@pytest.mark.anyio
async def test_a_non_429_adapter_error_carries_no_rate_limit_headers():
    clock = Clock()
    app, _, sent, _ = harness(
        upstream=answers(clock, (400, RATE_LIMITED_HEADERS, b'{"message":"bad request"}')),
        rate_limit_config=SHORT_WAIT, gate_factory=clocked_gate(clock))
    async with app.router.lifespan_context(app):
        response = await asyncio.wait_for(call(app, 'POST', '/api/chat', body=OLLAMA_CHAT), 5)
    assert len(sent) == 1
    assert response.status_code == 400
    assert response.json() == compat.adapter_error('ollama', 400, compat.upstream_error_hint(400))[1]
    assert 'retry-after' not in response.headers
    assert not [name for name in response.headers if name.lower().startswith('x-ratelimit-')]
