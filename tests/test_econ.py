"""Offline unit contracts for the token-economics configuration (proxy-econ-client 1.1),
the fake mmhmem stdio double with synthetic dialect fixtures (proxy-econ-client 1.2),
and the eligibility of historical file-read results (proxy-econ-client 2.1)."""
import asyncio
import copy
import dataclasses
import hashlib
import json
import re
import sys

import httpx
import httpx2
import pytest

import proxy
from econ_helpers import (
    HIST_BASH_ID, HIST_MMHMEM_ID, HIST_READ_ID, HIST_READ_PATH, MMHMEM_RESULT,
    SHELL_RESULT, TRAILING_BASH_ID, TRAILING_READ_ID, TRAILING_READ_PATH,
    anthropic_econ_body, ollama_econ_body, openai_econ_body, start_fake,
    synthetic_read_content,
)
from mvibe_proxy import compat, econ
from mvibe_proxy.econ import EconConfig, EconConfigError, HARD_EXCLUDED
from mvibe_auth.request_metadata import (
    RequestContext, attach_request_metadata, build_request_metadata,
)
from test_proxy import Session

ECON_KEYS = (
    'MVIBE_ECON_MMHMEM_BIN', 'MVIBE_ECON_PROJECT', 'MVIBE_ECON_TOOLS',
    'MVIBE_ECON_MIN_BYTES', 'MVIBE_ECON_TIMEOUT_MS', 'MVIBE_ECON_MEMO_MAX',
    'MVIBE_ECON_RETRY_S', 'MVIBE_ECON_CONCURRENCY',
)


def valid_env(**overrides):
    """A minimal valid environment; the Python interpreter stands in for the binary."""
    env = {'MVIBE_ECON_MMHMEM_BIN': sys.executable}
    env.update(overrides)
    return env


def test_all_unset_disables_economics():
    assert EconConfig.from_env({}) is None


def test_no_binary_stays_disabled_even_with_malformed_other_keys():
    # Off by default (Req 1.1): without the binary key there is no economics
    # path, so nothing else is validated.
    assert EconConfig.from_env({'MVIBE_ECON_MIN_BYTES': 'abc',
                                'MVIBE_ECON_TOOLS': 'bash'}) is None


def test_blank_binary_disables_economics():
    assert EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': ''}) is None
    assert EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': '   '}) is None


def test_from_env_reads_os_environ_when_env_is_none(monkeypatch):
    for key in ECON_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert EconConfig.from_env() is None
    monkeypatch.setenv('MVIBE_ECON_MMHMEM_BIN', sys.executable)
    config = EconConfig.from_env()
    assert config is not None and config.mmhmem_bin == sys.executable


def test_complete_env_applies_documented_defaults():
    config = EconConfig.from_env(valid_env())
    assert config.mmhmem_bin == sys.executable
    assert config.project == 'mvibe'
    assert config.tools == frozenset({'read'})
    assert (config.min_bytes, config.timeout_ms, config.memo_max,
            config.retry_s, config.concurrency) == (4096, 45000, 2000, 300, 2)


def test_complete_env_honors_explicit_values_and_normalizes_tools():
    config = EconConfig.from_env(valid_env(
        MVIBE_ECON_PROJECT='proj-x',
        MVIBE_ECON_TOOLS=' Read, grep ,Read',
        MVIBE_ECON_MIN_BYTES='1024',
        MVIBE_ECON_TIMEOUT_MS='5000',
        MVIBE_ECON_MEMO_MAX='50',
        MVIBE_ECON_RETRY_S='10',
        MVIBE_ECON_CONCURRENCY='4'))
    assert config.project == 'proj-x'
    assert config.tools == frozenset({'read', 'grep'})  # case-folded, deduplicated
    assert config.min_bytes == 1024
    assert config.timeout_ms == 5000
    assert config.memo_max == 50
    assert config.retry_s == 10
    assert config.concurrency == 4


def test_retry_s_zero_is_valid():
    assert EconConfig.from_env(valid_env(MVIBE_ECON_RETRY_S='0')).retry_s == 0


def test_config_is_frozen():
    config = EconConfig.from_env(valid_env())
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.project = 'other'


def test_hard_excluded_set_is_the_v1_security_floor():
    assert isinstance(HARD_EXCLUDED, frozenset)
    assert HARD_EXCLUDED == frozenset(
        {'edit', 'write', 'bash', 'shell', 'execute', 'patch', 'multiedit'})


@pytest.mark.parametrize('key,value', [
    ('MVIBE_ECON_MIN_BYTES', 'abc'),
    ('MVIBE_ECON_MIN_BYTES', '100'),  # below the 256 floor
    ('MVIBE_ECON_TIMEOUT_MS', '0'),
    ('MVIBE_ECON_TIMEOUT_MS', 'soon'),
    ('MVIBE_ECON_MEMO_MAX', '0'),
    ('MVIBE_ECON_RETRY_S', '-1'),
    ('MVIBE_ECON_CONCURRENCY', '0'),
])
def test_malformed_numeric_settings_raise_errors_naming_the_key(key, value):
    with pytest.raises(EconConfigError, match=key):
        EconConfig.from_env(valid_env(**{key: value}))


def test_missing_binary_path_raises_naming_the_key(tmp_path):
    missing = str(tmp_path / 'absent' / 'mmhmem')
    with pytest.raises(EconConfigError, match='MVIBE_ECON_MMHMEM_BIN'):
        EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': missing})


def test_directory_binary_path_raises_naming_the_key(tmp_path):
    with pytest.raises(EconConfigError, match='MVIBE_ECON_MMHMEM_BIN'):
        EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': str(tmp_path)})


def test_validate_binary_not_executable_branch_with_injected_predicate(tmp_path):
    # Windows os.access reports X_OK for any existing file, so the
    # not-executable branch is proven against the pure validator.
    binary = tmp_path / 'mmhmem'
    binary.write_text('#!/bin/sh\n', encoding='utf-8')
    with pytest.raises(EconConfigError, match='MVIBE_ECON_MMHMEM_BIN'):
        econ._validate_binary(str(binary), lambda path: False)
    econ._validate_binary(str(binary), lambda path: True)  # the predicate decides


@pytest.mark.skipif(sys.platform == 'win32',
                    reason='Windows os.access X_OK is true for any existing file')
def test_non_executable_binary_raises_on_posix(tmp_path):
    binary = tmp_path / 'mmhmem'
    binary.write_text('#!/bin/sh\n', encoding='utf-8')
    binary.chmod(0o644)
    with pytest.raises(EconConfigError, match='MVIBE_ECON_MMHMEM_BIN'):
        EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': str(binary)})


def test_hard_excluded_tool_dropped_with_exactly_one_warning(capsys):
    config = EconConfig.from_env(valid_env(MVIBE_ECON_TOOLS='read,bash'))
    assert config.tools == frozenset({'read'})
    err = capsys.readouterr().err
    assert 'WARNING' in err
    assert 'bash' in err
    assert err.count('\n') == 1  # one warning, not one per entry


def test_mmhmem_prefixed_and_case_variants_dropped_and_named(capsys):
    config = EconConfig.from_env(valid_env(
        MVIBE_ECON_TOOLS='read,mmhmem_shunt,BASH,Edit'))
    assert config.tools == frozenset({'read'})
    err = capsys.readouterr().err
    for dropped in ('mmhmem_shunt', 'bash', 'edit'):
        assert dropped in err
    assert err.count('\n') == 1


# --- 1.2: fake mmhmem stdio double (Requirement 6.1) -------------------------
#
# The fake speaks the companion shunt_content contract over injected asyncio
# streams: initialize handshake, notifications/initialized, newline-delimited
# JSON-RPC tools/call responses with one text block holding a JSON object.
# No subprocess, no network, no credentials, no worker model.

@pytest.mark.anyio
async def test_fake_answers_initialize_and_records_handshake_order():
    fake, driver = await start_fake()
    try:
        initialized = await driver.handshake()
        assert initialized['jsonrpc'] == '2.0'
        assert initialized['id'] == 1
        assert initialized['result']['capabilities'] == {'tools': {}}
        assert [method for method, _ in fake.calls] == ['initialize']
        await driver.call_shunt('synthetic-content')
        answered = await driver.receive()
        assert answered['id'] == 2  # the notification is never answered
        assert [method for method, _ in fake.calls] == [
            'initialize', 'notifications/initialized', 'tools/call']
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_records_every_tools_call_with_verbatim_arguments():
    fake, driver = await start_fake()
    try:
        await driver.handshake()
        content = synthetic_read_content()
        await driver.call_shunt(content, project='proj-x', source_hint=HIST_READ_PATH)
        await driver.receive()
        await driver.call_shunt('small', project='mvibe')
        await driver.receive()
        assert len(fake.calls) == 4
        method, params = fake.calls[2]
        assert (method, params['name']) == ('tools/call', 'shunt_content')
        assert params['arguments'] == {
            'project': 'proj-x', 'agent_harness': 'mvibe-proxy',
            'content': content, 'kind': 'file_read',
            'source_hint': HIST_READ_PATH,
        }
        assert fake.calls[3][1]['arguments'] == {
            'project': 'mvibe', 'agent_harness': 'mvibe-proxy',
            'content': 'small', 'kind': 'file_read',
        }
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_summarized_wire_matches_companion_contract():
    fake, driver = await start_fake('summarized')
    try:
        await driver.handshake()
        content = synthetic_read_content()
        await driver.call_shunt(content, source_hint=HIST_READ_PATH)
        raw = await driver.receive_raw()
        assert raw.endswith(b'\n')
        assert raw.count(b'\n') == 1  # exactly one newline-terminated line
        message = json.loads(raw)
        assert message['id'] == 2
        result = message['result']
        assert 'isError' not in result
        [block] = result['content']
        assert block['type'] == 'text'
        inner = json.loads(block['text'])
        sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
        assert set(inner) == {'outcome', 'summary', 'content_sha256', 'line_count',
                              'avoided_input_tokens_est', 'latency_ms', 'worker_model'}
        assert inner['outcome'] == 'summarized'
        assert inner['content_sha256'] == sha
        assert inner['line_count'] == len(content.splitlines())
        assert inner['avoided_input_tokens_est'] == len(content) // 4
        assert inner['summary'].startswith(f'SUM<{sha[:8]}>')
        assert None not in inner.values()  # absent fields are omitted, not null
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_cached_mode_same_shape_with_cached_outcome():
    fake, driver = await start_fake('cached')
    try:
        await driver.handshake()
        content = synthetic_read_content()
        await driver.call_shunt(content)
        inner = json.loads((await driver.receive())['result']['content'][0]['text'])
        assert inner['outcome'] == 'cached'
        assert inner['content_sha256'] == hashlib.sha256(content.encode('utf-8')).hexdigest()
        assert inner['summary'].startswith('SUM<')
        assert 'bypass_reason' not in inner
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_bypassed_mode_omits_summary_and_names_reason():
    fake, driver = await start_fake('bypassed', bypass_reason='security_critical')
    try:
        await driver.handshake()
        content = synthetic_read_content()
        await driver.call_shunt(content)
        inner = json.loads((await driver.receive())['result']['content'][0]['text'])
        assert inner == {
            'outcome': 'bypassed',
            'bypass_reason': 'security_critical',
            'content_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
            'line_count': len(content.splitlines()),
            'avoided_input_tokens_est': 0,
        }
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_tool_error_mode_iserror_with_sanitized_message():
    fake, driver = await start_fake('tool_error')
    try:
        await driver.handshake()
        await driver.call_shunt(synthetic_read_content())
        message = await driver.receive()
        assert message['id'] == 2
        result = message['result']
        assert result['isError'] is True
        assert result['content'] == [{'type': 'text', 'text': 'worker not configured'}]
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_malformed_mode_writes_a_non_json_line():
    fake, driver = await start_fake('malformed')
    try:
        await driver.handshake()
        await driver.call_shunt('anything')
        raw = await driver.receive_raw()
        assert raw.endswith(b'\n') and raw.count(b'\n') == 1
        with pytest.raises(ValueError):
            json.loads(raw)
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_hang_mode_records_but_never_answers():
    fake, driver = await start_fake('hang')
    try:
        await driver.handshake()
        await driver.call_shunt('hanging-content')
        with pytest.raises(asyncio.TimeoutError):
            await driver.receive_raw(timeout=0.3)
        method, params = fake.calls[-1]  # the call was still recorded
        assert method == 'tools/call'
        assert params['arguments']['content'] == 'hanging-content'
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_close_mode_ends_stream_and_breaks_stdin():
    fake, driver = await start_fake('close')
    try:
        await driver.handshake()
        await driver.call_shunt('about-to-die')
        assert await driver.receive_raw() == b''  # EOF: the child died mid-request
        assert fake.closed is True
        with pytest.raises(ConnectionResetError):
            driver.writer.write(b'{"jsonrpc":"2.0","id":9,"method":"tools/call"}\n')
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_mode_can_be_overridden_per_content_hash():
    first = synthetic_read_content()
    second = synthetic_read_content(TRAILING_READ_PATH)
    override = hashlib.sha256(second.encode('utf-8')).hexdigest()
    fake, driver = await start_fake('summarized', modes_by_sha={override: 'tool_error'})
    try:
        await driver.handshake()
        await driver.call_shunt(first)
        assert (await driver.receive())['result'].get('isError') is None
        await driver.call_shunt(second)
        assert (await driver.receive())['result']['isError'] is True
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_echoes_arbitrary_client_request_ids():
    fake, driver = await start_fake()
    try:
        await driver.handshake()  # initialize consumed id 1
        await driver.call_shunt('x', request_id=41)
        assert (await driver.receive())['id'] == 41
        await driver.call_shunt('y', request_id=7)
        assert (await driver.receive())['id'] == 7
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_responses_are_byte_identical_for_identical_content():
    fake, driver = await start_fake()
    try:
        await driver.handshake()
        content = synthetic_read_content()
        await driver.call_shunt(content, request_id=5)
        first = await driver.receive_raw()
        await driver.call_shunt(content, request_id=5)
        second = await driver.receive_raw()
        assert first == second
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_fake_stop_cancels_driver_and_is_idempotent():
    fake, driver = await start_fake()
    await driver.handshake()
    task = fake.task
    await fake.stop()
    assert task.done()
    assert fake.task is None
    await fake.stop()  # stopping twice is safe
    assert await driver.receive_raw() == b''  # the response stream ends at EOF
    with pytest.raises(ConnectionResetError):
        driver.writer.write(b'{"jsonrpc":"2.0","id":9,"method":"tools/call"}\n')


# --- 1.2: synthetic dialect fixtures -----------------------------------------
#
# Synthetic bodies for the three dialects the proxy parses, with a multi-call
# assistant turn (read + bash), one historical large read result, a shell
# result, an mmhmem tool result, further turns, and a trailing tool group.

def test_synthetic_read_content_is_large_headered_and_deterministic():
    content = synthetic_read_content()
    assert len(content.encode('utf-8')) >= 4096
    lines = content.splitlines()
    assert re.match(rf'^Read file {re.escape(HIST_READ_PATH)}, lines 1-\d+$', lines[0])
    assert lines[0] == f'Read file {HIST_READ_PATH}, lines 1-{len(lines) - 1}'
    assert synthetic_read_content() == content  # deterministic
    assert synthetic_read_content(TRAILING_READ_PATH) != content
    filler = lines[1:]
    assert any(line.startswith('import ') for line in filler)
    assert any(line.startswith('def ') for line in filler)


def test_openai_fixture_multi_call_history_and_trailing_group():
    body = openai_econ_body()
    messages = body['messages']
    called = {call['id']: call['function']['name']
              for message in messages for call in message.get('tool_calls', [])}
    assert called == {
        HIST_READ_ID: 'read', HIST_BASH_ID: 'bash',
        HIST_MMHMEM_ID: 'mmhmem_mem_search',
        TRAILING_READ_ID: 'read', TRAILING_BASH_ID: 'bash',
    }
    by_id = {message['tool_call_id']: message
             for message in messages if message.get('role') == 'tool'}
    historical = by_id[HIST_READ_ID]['content']
    assert len(historical.encode('utf-8')) >= 4096
    assert historical.startswith(f'Read file {HIST_READ_PATH}, lines ')
    assert by_id[HIST_BASH_ID]['content'] == SHELL_RESULT
    assert by_id[HIST_MMHMEM_ID]['content'] == MMHMEM_RESULT
    # The trailing group is the contiguous run of tool results at the end.
    assert [message['role'] for message in messages[-2:]] == ['tool', 'tool']
    trailing = by_id[TRAILING_READ_ID]['content']
    assert len(trailing.encode('utf-8')) >= 4096
    assert trailing.startswith(f'Read file {TRAILING_READ_PATH}, lines ')


def test_anthropic_fixture_pairs_tool_use_and_tool_result_blocks():
    body = anthropic_econ_body()
    assert isinstance(body['system'], str) and body['system']
    tool_uses = {block['id']: block['name']
                 for message in body['messages'] for block in message['content']
                 if isinstance(block, dict) and block.get('type') == 'tool_use'}
    results = {block['tool_call_id']: block['content']
               for message in body['messages'] for block in message['content']
               if isinstance(block, dict) and block.get('type') == 'tool_result'}
    assert set(tool_uses) == set(results)
    assert tool_uses[HIST_READ_ID] == 'read'
    assert len(results[HIST_READ_ID].encode('utf-8')) >= 4096
    assert results[HIST_BASH_ID] == SHELL_RESULT
    assert tool_uses[HIST_MMHMEM_ID] == 'mmhmem_mem_search'
    # The trailing tool results sit in the final message.
    last = body['messages'][-1]
    assert {block['tool_call_id'] for block in last['content']} == {
        TRAILING_READ_ID, TRAILING_BASH_ID}


def test_ollama_fixture_shape():
    body = ollama_econ_body()
    assert body['model'] and body['stream'] is False
    assistant_calls = [call for message in body['messages']
                       for call in message.get('tool_calls', [])]
    assert {call['id']: call['function']['name'] for call in assistant_calls} == {
        HIST_READ_ID: 'read', HIST_BASH_ID: 'bash',
        HIST_MMHMEM_ID: 'mmhmem_mem_search',
        TRAILING_READ_ID: 'read', TRAILING_BASH_ID: 'bash',
    }
    tool_messages = [message for message in body['messages'] if message['role'] == 'tool']
    assert len(tool_messages) == 5
    assert [message['role'] for message in body['messages'][-2:]] == ['tool', 'tool']


def test_anthropic_fixture_translates_to_upstream_tool_messages():
    upstream = compat.anthropic_messages_request(anthropic_econ_body())
    tools = [message for message in upstream['messages'] if message['role'] == 'tool']
    assert {message['tool_call_id'] for message in tools} == {
        HIST_READ_ID, HIST_BASH_ID, HIST_MMHMEM_ID, TRAILING_READ_ID, TRAILING_BASH_ID}
    historical = next(message for message in tools
                      if message['tool_call_id'] == HIST_READ_ID)
    assert historical['content'] == synthetic_read_content()
    assert {message['role'] for message in upstream['messages'][-2:]} == {'tool'}


def test_ollama_fixture_translation_preserves_content_verbatim():
    upstream = compat.ollama_chat_request(ollama_econ_body())
    assert upstream['model'] == 'mistral-large-4'
    tool_messages = [message for message in upstream['messages'] if message['role'] == 'tool']
    # The Ollama translation keeps role and content; correlation ids do not survive.
    assert all(set(message) == {'role', 'content'} for message in tool_messages)
    contents = [message['content'] for message in tool_messages]
    assert synthetic_read_content() in contents
    assert synthetic_read_content(TRAILING_READ_PATH) in contents
    assert SHELL_RESULT in contents and MMHMEM_RESULT in contents
    assert upstream['messages'][-1]['role'] == 'tool'


def test_fixture_builders_return_fresh_bodies():
    first = openai_econ_body()
    first['messages'][0]['content'] = 'mutated'
    assert openai_econ_body()['messages'][0]['content'] != 'mutated'
    anthropic_a = anthropic_econ_body()
    anthropic_a['messages'][0] = {}
    assert anthropic_econ_body()['messages'][0]
    ollama_a = ollama_econ_body()
    ollama_a['model'] = 'mutated'
    assert ollama_econ_body()['model'] == 'mistral-large-4'


UPSTREAM_CHAT_RESPONSE = {'id': 'chat-econ-1', 'model': 'mistral-large-4', 'choices': [
    {'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'OK'}}],
    'usage': {'prompt_tokens': 8, 'completion_tokens': 2}}


def econ_harness():
    """The test_proxy harness() pattern with a chat-shaped upstream response."""
    session = Session()
    requests = []

    def client_factory(**kwargs):
        async def upstream(request):
            requests.append(request)
            return httpx2.Response(200, json=UPSTREAM_CHAT_RESPONSE)
        return httpx2.AsyncClient(transport=httpx2.MockTransport(upstream),
                                  follow_redirects=False)

    app = proxy.create_app(lambda: session, client_factory)
    return app, session, requests


@pytest.mark.anyio
async def test_all_three_dialect_fixtures_load_through_the_proxy_harness():
    app, _, requests = econ_harness()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            openai_response = await client.post('/v1/chat/completions',
                                                content=json.dumps(openai_econ_body()).encode())
            assert openai_response.status_code == 200
            anthropic_response = await client.post('/v1/messages',
                                                   content=json.dumps(anthropic_econ_body()).encode(),
                                                   headers={'anthropic-version': '2023-06-01'})
            assert anthropic_response.status_code == 200
            ollama_response = await client.post('/api/chat',
                                                content=json.dumps(ollama_econ_body()).encode())
            assert ollama_response.status_code == 200
    assert len(requests) == 3
    for request in requests:
        forwarded = json.loads(request.content)
        tool_contents = [message['content'] for message in forwarded['messages']
                         if message.get('role') == 'tool'
                         and isinstance(message.get('content'), str)]
        assert any(content.startswith(f'Read file {HIST_READ_PATH}') for content in tool_contents)
        assert any(content.startswith(f'Read file {TRAILING_READ_PATH}') for content in tool_contents)


# --- 1.3: single body parse shared with account-attribution metadata --------


@pytest.mark.anyio
async def test_forwarded_chat_body_bytes_with_economics_disabled_match_today():
    """Req 1.1/3.6/6.4: with economics disabled the forwarded chat-completion
    bytes are exactly the ones today's bytes-level attachment produces."""
    app, _, requests = econ_harness()
    body = openai_econ_body()
    raw = json.dumps(body).encode()  # deliberately non-compact input bytes
    expected = attach_request_metadata(raw)
    assert expected != raw  # attribution re-serialization really happens
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.post('/v1/chat/completions', content=raw)
            assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].content == expected
    forwarded = json.loads(requests[0].content)
    assert forwarded['messages'] == body['messages']
    # The trailing tool results make this a secondary call; metadata equals a
    # fresh build with no account plan (the harness session has none).
    assert forwarded['metadata'] == build_request_metadata(RequestContext(iteration=1))
    assert 'user_plan' not in forwarded['metadata']


@pytest.mark.anyio
@pytest.mark.parametrize('raw', [b'', b'not json', b'[1,2]', b'null', b'\xff',
                                 b'{"metadata": []}', b'{"value": NaN}',
                                 b'{"model":"mistral-large-4","metadata":7}'])
async def test_unattributable_chat_bodies_relay_byte_exact_with_economics_disabled(raw):
    """Req 1.1: bodies the attribution leaves intact are relayed byte-exact."""
    app, _, requests = econ_harness()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.post('/v1/chat/completions', content=raw)
            assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].content == raw


@pytest.mark.anyio
async def test_chat_completion_body_parsed_once_on_the_acquire_path(monkeypatch):
    """Design risk 'large bodies parsed twice': the acquire path JSON-parses
    the request body exactly once (model inspection and metadata attachment
    share one parse)."""
    app, _, requests = econ_harness()
    raw = json.dumps(openai_econ_body()).encode()
    expected = attach_request_metadata(raw)  # computed before the patch window
    body_parses = []
    real_loads = json.loads

    def counting_loads(data, *args, **kwargs):
        if isinstance(data, (bytes, bytearray)) and bytes(data) == raw:
            body_parses.append(bytes(data))
        return real_loads(data, *args, **kwargs)

    monkeypatch.setattr(json, 'loads', counting_loads)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.post('/v1/chat/completions', content=raw)
            assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].content == expected
    assert len(body_parses) == 1


# --- 2.1: eligibility of historical file-read results -------------------------
#
# Pure-function contracts over the upstream-shaped messages list (the OpenAI
# shape every dialect has been translated into before economics runs):
# correlation of tool_call_id -> tool name across multi-call assistant
# messages, the trailing-group rule in both of its shapes, the allowlist /
# string-type / minimum-size gates, and the three source-hint origins
# (Requirements 2.1, 2.2, 2.3, 2.6; the 2.5 hard-exclusion defense and the
# 2.4 id-free dialect reality are covered as well).

OPENAI_HIST_READ_INDEX = 2       # tool result for HIST_READ_ID in openai_econ_body
OPENAI_TRAILING_START = 9        # first message of the trailing tool group


def small_bytes_config(min_bytes: int = 256, tools: str = 'read') -> EconConfig:
    """A config with a low size floor so byte-exact thresholds are easy."""
    return EconConfig.from_env(valid_env(MVIBE_ECON_MIN_BYTES=str(min_bytes),
                                         MVIBE_ECON_TOOLS=tools))


def direct_config(tools: frozenset[str], min_bytes: int = 256) -> EconConfig:
    """A config built directly, bypassing the config-time allowlist scrub, so
    select's own hard-exclusion defense can be proven (Requirement 2.5)."""
    return EconConfig(mmhmem_bin=sys.executable, project='mvibe', tools=tools,
                      min_bytes=min_bytes, timeout_ms=45_000, memo_max=2_000,
                      retry_s=300, concurrency=2)


def assistant_call_message(*calls: tuple[str, str, object]) -> dict:
    """One assistant message with tool_calls built from (id, name, arguments).

    Dict arguments are JSON-dumped like the upstream shape; anything else is
    passed through verbatim so malformed argument strings can be fed.
    """
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': call_id, 'type': 'function',
         'function': {'name': name,
                      'arguments': json.dumps(args) if isinstance(args, dict) else args}}
        for call_id, name, args in calls]}


def tool_result(call_id: str, content: object) -> dict:
    return {'role': 'tool', 'tool_call_id': call_id, 'content': content}


def read_result_messages(content: object, *, call_id: str = 'call-read',
                         arguments: dict | None = None) -> list[dict]:
    """A minimal history: user, one read call, its result, a closing turn."""
    return [
        {'role': 'user', 'content': 'read it'},
        assistant_call_message(
            (call_id, 'read', arguments if arguments is not None
             else {'filePath': 'src/small.py'})),
        tool_result(call_id, content),
        {'role': 'assistant', 'content': 'done'},
    ]


def test_correlate_maps_every_call_id_to_lowercased_name_and_parsed_args():
    calls = econ.correlate(openai_econ_body()['messages'])
    assert calls == {
        HIST_READ_ID: ('read', {'filePath': HIST_READ_PATH, 'limit': 200}),
        HIST_BASH_ID: ('bash', {'command': 'pytest -q tests/'}),
        HIST_MMHMEM_ID: ('mmhmem_mem_search', {'query': 'widget'}),
        TRAILING_READ_ID: ('read', {'filePath': TRAILING_READ_PATH}),
        TRAILING_BASH_ID: ('bash', {'command': 'pytest -q tests/test_econ.py'}),
    }


def test_correlate_parses_non_json_arguments_to_empty_dict():
    messages = [
        {'role': 'user', 'content': 'hi'},
        assistant_call_message(('c1', 'Read', 'not-json{'),
                               ('c2', 'read', '[1, 2]'),
                               ('c3', 'read', None)),
    ]
    assert econ.correlate(messages) == {'c1': ('read', {}), 'c2': ('read', {}),
                                        'c3': ('read', {})}


def test_correlate_ignores_conversations_without_tool_calls():
    messages = [{'role': 'user', 'content': 'hi'},
                {'role': 'assistant', 'content': 'hello'}]
    assert econ.correlate(messages) == {}


def test_trailing_start_marks_the_contiguous_tool_run_at_the_end():
    messages = openai_econ_body()['messages']
    assert econ.trailing_start(messages) == OPENAI_TRAILING_START
    assert messages[OPENAI_TRAILING_START]['tool_call_id'] == TRAILING_READ_ID


def test_trailing_start_is_len_when_no_trailing_tool_results():
    # Shape: the list ends with the assistant's tool_calls awaiting results.
    awaiting = openai_econ_body()['messages'][:OPENAI_TRAILING_START]
    assert awaiting[-1]['role'] == 'assistant' and awaiting[-1]['tool_calls']
    assert econ.trailing_start(awaiting) == len(awaiting)
    # Design shape [..., tool, user]: a later user turn empties the group.
    followed = openai_econ_body()['messages'] + [{'role': 'user', 'content': 'go'}]
    assert econ.trailing_start(followed) == len(followed)
    assert econ.trailing_start([]) == 0


def test_select_returns_exactly_the_historical_read_from_the_fixture():
    body = openai_econ_body()
    config = EconConfig.from_env(valid_env())
    [candidate] = econ.select(body['messages'], config)
    content = synthetic_read_content()
    assert (candidate.index, candidate.tool_call_id, candidate.tool) == (
        OPENAI_HIST_READ_INDEX, HIST_READ_ID, 'read')
    assert candidate.content == content
    assert re.fullmatch(r'[0-9a-f]{64}', candidate.sha256)
    assert candidate.sha256 == hashlib.sha256(content.encode('utf-8')).hexdigest()
    assert candidate.line_count == len(content.splitlines())
    assert candidate.source_hint == HIST_READ_PATH


def test_select_never_takes_results_in_the_trailing_group():
    config = EconConfig.from_env(valid_env())
    messages = openai_econ_body()['messages']

    # Shape 1: [..., assistant(tool_calls), tool, tool] — the trailing pair is
    # the contiguous tool run at the end and is never eligible (Req 2.3).
    assert {c.tool_call_id for c in econ.select(messages, config)} == {HIST_READ_ID}

    # Shape 2: the list ends with the assistant's tool_calls still awaiting
    # results — the trailing group is empty, history stays eligible.
    awaiting = messages[:OPENAI_TRAILING_START]
    assert {c.tool_call_id for c in econ.select(awaiting, config)} == {HIST_READ_ID}

    # Design shape [..., tool, user]: the user's follow-up reopens the earlier
    # results as history, so the trailing group is empty again.
    followed = messages + [{'role': 'user', 'content': 'continue'}]
    assert econ.trailing_start(followed) == len(followed)
    assert {c.tool_call_id for c in econ.select(followed, config)} == {
        HIST_READ_ID, TRAILING_READ_ID}


def test_multi_call_turn_correlates_each_result_to_its_own_tool():
    # Results arrive in a different order than the calls: correlation is by
    # tool_call_id, never by position, and only allowlisted tools pass.
    config = small_bytes_config(min_bytes=256, tools='read,grep')
    messages = [
        {'role': 'user', 'content': 'find it'},
        assistant_call_message(
            ('call-read', 'read', {'filePath': 'src/widget.py'}),
            ('call-bash', 'bash', {'command': 'pytest -q'}),
            ('call-grep', 'grep', {'pattern': 'widget'})),
        tool_result('call-bash', 'x' * 500),
        tool_result('call-read', 'y' * 500),
        tool_result('call-grep', 'z' * 500),
        {'role': 'assistant', 'content': 'done'},
    ]
    assert [(c.index, c.tool_call_id, c.tool) for c in econ.select(messages, config)] == [
        (3, 'call-read', 'read'), (4, 'call-grep', 'grep')]


@pytest.mark.parametrize('name', ['read', 'Read', 'READ', 'rEaD'])
def test_select_matches_tool_names_case_insensitively(name):
    config = EconConfig.from_env(valid_env())  # allowlist is lowercase 'read'
    messages = [
        {'role': 'user', 'content': 'read it'},
        assistant_call_message(('call-read', name, {'filePath': 'src/small.py'})),
        tool_result('call-read', 'x' * 5000),
        {'role': 'assistant', 'content': 'done'},
    ]
    [matched] = econ.select(messages, config)
    assert matched.tool == 'read'


@pytest.mark.parametrize('length,eligible', [(255, False), (256, True), (257, True)])
def test_select_applies_the_minimum_size_in_utf8_bytes(length, eligible):
    content = 'x' * length
    assert len(content.encode('utf-8')) == length  # exact byte lengths
    selected = econ.select(read_result_messages(content),
                           small_bytes_config(min_bytes=256))
    assert [c.tool_call_id for c in selected] == (['call-read'] if eligible else [])


def test_select_measures_size_in_bytes_not_characters():
    config = small_bytes_config(min_bytes=256)
    assert econ.select(read_result_messages('é' * 128), config)      # 256 bytes
    assert econ.select(read_result_messages('é' * 127), config) == []  # 254 bytes


def test_select_skips_non_string_content_without_crashing():
    # OpenAI tool messages may carry content parts or a null content; only a
    # plain string result is ever a candidate (Req 2.2).
    messages = [
        {'role': 'user', 'content': 'read it'},
        assistant_call_message(
            ('call-parts', 'read', {'filePath': 'src/parts.py'}),
            ('call-none', 'read', {'filePath': 'src/none.py'}),
            ('call-text', 'read', {'filePath': 'src/text.py'})),
        tool_result('call-parts', [{'type': 'text', 'text': 'x' * 5000}]),
        tool_result('call-none', None),
        tool_result('call-text', 'y' * 5000),
        {'role': 'assistant', 'content': 'done'},
    ]
    assert [c.tool_call_id for c in econ.select(messages, EconConfig.from_env(valid_env()))] \
        == ['call-text']


def test_source_hint_prefers_the_read_header_in_the_content():
    content = synthetic_read_content('from/header.py')
    messages = [
        {'role': 'user', 'content': 'read it'},
        assistant_call_message(('call-read', 'read', {'filePath': 'from/args.py'})),
        tool_result('call-read', content),
        {'role': 'assistant', 'content': 'done'},
    ]
    [candidate] = econ.select(messages, EconConfig.from_env(valid_env()))
    assert candidate.source_hint == 'from/header.py'


@pytest.mark.parametrize('key', ['filePath', 'file_path', 'path'])
def test_source_hint_falls_back_to_the_call_path_argument(key):
    messages = [
        {'role': 'user', 'content': 'read it'},
        assistant_call_message(('call-read', 'read', {key: 'from/args.py'})),
        tool_result('call-read', 'no header here\n' + 'filler line\n' * 100),
        {'role': 'assistant', 'content': 'done'},
    ]
    [candidate] = econ.select(messages, small_bytes_config())
    assert candidate.source_hint == 'from/args.py'


def test_source_hint_is_none_without_header_or_path_argument():
    messages = [
        {'role': 'user', 'content': 'read it'},
        assistant_call_message(('call-read', 'read', {'limit': 10}),
                               ('call-num', 'read', {'path': 42})),
        tool_result('call-read', 'no header\n' + 'filler line\n' * 100),
        tool_result('call-num', 'no header\n' + 'filler line\n' * 100),
        {'role': 'assistant', 'content': 'done'},
    ]
    selected = econ.select(messages, small_bytes_config())
    assert {c.tool_call_id: c.source_hint for c in selected} == {
        'call-read': None, 'call-num': None}  # a non-string path value is no hint


def test_select_correlate_and_trailing_start_never_mutate_the_input():
    body = openai_econ_body()
    # Pessimize the body: non-string content and malformed argument text.
    body['messages'][OPENAI_HIST_READ_INDEX]['content'] = [
        {'type': 'text', 'text': synthetic_read_content()}]
    body['messages'][1]['tool_calls'][0]['function']['arguments'] = 'not-json{'
    snapshot = copy.deepcopy(body)
    config = EconConfig.from_env(valid_env())
    econ.correlate(body['messages'])
    econ.trailing_start(body['messages'])
    econ.select(body['messages'], config)
    assert body == snapshot


def test_ollama_translated_tool_results_without_ids_are_not_eligible():
    # Dialect reality: the Ollama translation keeps role/content but drops the
    # tool ids entirely, so its results cannot be correlated to a tool name
    # and are never eligible (id-free path, Req 2.4).
    upstream = compat.ollama_chat_request(ollama_econ_body())
    tool_messages = [message for message in upstream['messages']
                     if message.get('role') == 'tool']
    assert tool_messages and all('tool_call_id' not in message
                                 for message in tool_messages)
    assert econ.select(upstream['messages'], EconConfig.from_env(valid_env())) == []


def test_orphan_tool_call_ids_are_never_eligible():
    messages = [{'role': 'user', 'content': 'read it'},
                tool_result('call-unknown', 'x' * 5000),
                {'role': 'assistant', 'content': 'done'}]
    assert econ.select(messages, EconConfig.from_env(valid_env())) == []


def test_hard_excluded_tools_are_defended_in_select_not_only_in_config():
    # Requirement 2.5 defended here: a config that still lists mmhmem/shell/
    # edit names (possible through direct construction) must not make their
    # results eligible; config-time dropping alone is not the only guard.
    config = direct_config(frozenset({'read', 'bash', 'mmhmem_mem_search', 'edit'}))
    messages = [
        {'role': 'user', 'content': 'go'},
        assistant_call_message(
            ('call-read', 'read', {'filePath': 'src/a.py'}),
            ('call-mem', 'mmhmem_mem_search', {'query': 'a'}),
            ('call-bash', 'bash', {'command': 'ls'}),
            ('call-edit', 'edit', {'path': 'src/a.py'})),
        tool_result('call-read', 'x' * 300),
        tool_result('call-mem', 'y' * 300),
        tool_result('call-bash', 'z' * 300),
        tool_result('call-edit', 'w' * 300),
        {'role': 'assistant', 'content': 'done'},
    ]
    assert [c.tool_call_id for c in econ.select(messages, config)] == ['call-read']


# --- 2.2: bounded memo of outcomes ---------------------------------------------
#
# The memo is the determinism and bounds component: outcomes keyed by sha256
# content hash, least-recently-used eviction at capacity where a read counts
# as a use (Requirement 4.4), byte-identical summaries across repeated reads
# because the rendered replacement text is stored once and never re-rendered
# (Requirement 3.3), and the failure back-off check (Requirement 4.3).

MEMO_AT = 1_000.0


def memo_key(n: int) -> str:
    """A distinct, well-formed sha256 hex digest per n."""
    return f'{n:064x}'


def summary_outcome(n: int, at: float = MEMO_AT) -> econ.Outcome:
    """A representative stored outcome: the final rendered marker + summary."""
    text = (f'[mvibe econ] Summary of an earlier file read (src/mod{n}.py, '
            f'{n + 1} lines, sha256:{n:012x}).\n'
            'structure: module docstring, imports, one class, one function')
    return econ.Outcome(kind='summary', summary=text, at=at)


def fill_memo(memo: econ.Memo, count: int, at: float = MEMO_AT) -> None:
    for n in range(count):
        memo.put(memo_key(n), summary_outcome(n, at))


def test_memo_evicts_the_least_recently_used_entry_at_capacity():
    memo = econ.Memo(3)
    fill_memo(memo, 3)
    assert len(memo) == 3
    memo.put(memo_key(3), summary_outcome(3))       # full: key 0 is the LRU entry
    assert len(memo) == 3                           # bounded in entries (Req 4.4)
    assert memo.get(memo_key(0)) is None            # the oldest entry was evicted
    assert [memo.get(memo_key(n)) for n in (1, 2, 3)] == [
        summary_outcome(1), summary_outcome(2), summary_outcome(3)]


def test_memo_get_touches_the_lru_order():
    memo = econ.Memo(3)
    fill_memo(memo, 3)                              # LRU -> MRU: keys 0, 1, 2
    assert memo.get(memo_key(0)) is not None        # reading key 0 makes key 1 LRU
    memo.put(memo_key(3), summary_outcome(3))
    assert memo.get(memo_key(1)) is None            # the untouched key was evicted
    assert memo.get(memo_key(0)) is not None        # the touched key survived
    assert memo.get(memo_key(3)) is not None


def test_memo_summaries_stay_byte_identical_across_repeated_reads():
    memo = econ.Memo(4)
    fill_memo(memo, 3)                              # LRU -> MRU: keys 0, 1, 2
    memo.put(memo_key(7), summary_outcome(7))
    first = memo.get(memo_key(7))
    memo.put(memo_key(3), summary_outcome(3))       # full: evicts key 0, not key 7
    second = memo.get(memo_key(7))
    memo.put(memo_key(4), summary_outcome(4))       # more traffic, key 7 still warm
    third = memo.get(memo_key(7))
    assert first is second is third                 # fixed once stored, never re-rendered
    assert first.summary == second.summary == third.summary
    assert first.summary == summary_outcome(7).summary
    assert isinstance(first.summary, str)


def test_memo_back_off_blocks_retry_until_retry_due_elapses():
    memo = econ.Memo(2)
    failed = econ.Outcome(kind='failed', reason='McpError', at=MEMO_AT)
    memo.put(memo_key(5), failed)
    assert memo.get(memo_key(5)) is failed
    assert memo.retry_due(failed, retry_s=300, now=MEMO_AT + 299) is False
    assert memo.retry_due(failed, retry_s=300, now=MEMO_AT + 300) is True
    assert memo.retry_due(failed, retry_s=300, now=MEMO_AT + 301) is True


def test_memo_retry_due_is_always_true_for_non_failed_outcomes():
    memo = econ.Memo(2)
    not_failed = [summary_outcome(0),
                  econ.Outcome(kind='bypassed', reason='security_critical', at=MEMO_AT)]
    for outcome in not_failed:
        assert memo.retry_due(outcome, retry_s=300, now=0.0) is True


def test_memo_unknown_key_returns_none_so_a_fetch_may_start():
    memo = econ.Memo(2)
    assert memo.get(memo_key(99)) is None
    assert len(memo) == 0


def test_memo_put_existing_key_refreshes_value_and_lru_position():
    memo = econ.Memo(2)
    fill_memo(memo, 2)                              # LRU -> MRU: keys 0, 1
    refreshed = summary_outcome(0, at=MEMO_AT + 50)
    memo.put(memo_key(0), refreshed)                # key 0 becomes MRU, size unchanged
    assert len(memo) == 2
    assert memo.get(memo_key(0)) is refreshed
    memo.put(memo_key(2), summary_outcome(2))       # now key 1 is the LRU entry
    assert memo.get(memo_key(1)) is None
    assert memo.get(memo_key(0)) is refreshed
    assert len(memo) == 2


def test_memo_put_existing_key_at_capacity_never_grows_the_store():
    memo = econ.Memo(3)
    fill_memo(memo, 3)
    memo.put(memo_key(1), summary_outcome(1, at=MEMO_AT + 5))
    assert len(memo) == 3
    for n in range(3):
        assert memo.get(memo_key(n)) is not None


def test_outcome_field_set_and_defaults_match_the_design_contract():
    assert [field.name for field in dataclasses.fields(econ.Outcome)] == [
        'kind', 'summary', 'reason', 'at']
    outcome = econ.Outcome(kind='bypassed', reason='below_threshold')
    assert outcome.summary is None
    assert outcome.at == 0.0


def test_outcome_is_frozen_so_stored_entries_never_drift():
    outcome = summary_outcome(0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.kind = 'failed'
