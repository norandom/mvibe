"""Offline unit contracts for the token-economics configuration (proxy-econ-client 1.1),
the fake mmhmem stdio double with synthetic dialect fixtures (proxy-econ-client 1.2),
the eligibility of historical file-read results (proxy-econ-client 2.1), the bounded
memo of outcomes (proxy-econ-client 2.2), the minimal supervised stdio JSON-RPC
client (proxy-econ-client 2.3), the background warm-up queue (proxy-econ-client 2.4),
the request-path rewrite with marker and counters (proxy-econ-client 2.5), the
diagnostics allowlist with the per-request and warm econ events (proxy-econ-client 3.1),
and the proxy wiring: --econ switch, lifespan-owned shunt and supervised stdio child,
and the fail-open hook in make_acquire (proxy-econ-client 3.2), and the end-to-end
fail-open and determinism sweep of the whole wired app against every fake failure
mode (proxy-econ-client 4.1)."""
import asyncio
import copy
import dataclasses
import hashlib
import json
import re
import sys
import time
import types
from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest

import proxy
from econ_helpers import (
    ChildStdinWriter, FakeMmhmem, HIST_BASH_ID, HIST_MMHMEM_ID, HIST_READ_ID,
    HIST_READ_PATH, MMHMEM_RESULT, SHELL_RESULT, TRAILING_BASH_ID, TRAILING_READ_ID,
    TRAILING_READ_PATH, anthropic_econ_body, ollama_econ_body, openai_econ_body,
    start_fake, synthetic_read_content,
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


def econ_harness(econ_settings: EconConfig | None = None, econ_client=None):
    """The test_proxy harness() pattern with a chat-shaped upstream response.

    econ_settings/econ_client wire economics into the app the way proxy.main
    does, with an injected-stream client replacing the spawned child (3.2)."""
    session = Session()
    requests = []

    def client_factory(**kwargs):
        async def upstream(request):
            requests.append(request)
            return httpx2.Response(200, json=UPSTREAM_CHAT_RESPONSE)
        return httpx2.AsyncClient(transport=httpx2.MockTransport(upstream),
                                  follow_redirects=False)

    app = proxy.create_app(lambda: session, client_factory,
                           econ_config=econ_settings, econ_client=econ_client)
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


# --- 2.3: minimal stdio JSON-RPC client with child supervision -------------------
#
# The real StdioMcpClient driven against FakeMmhmem injected streams: handshake
# ordering and wire arguments, response matching by id under interleaved
# traffic, every failure surfaced as one sanitized McpError (tool, protocol,
# timeout, terminated — never content, never paths), and supervision (fail
# in-flight fast + a restart recorded with capped 1 s -> 30 s back-off). The
# spawn path never executes here: create_subprocess_exec is patched to fail
# loudly and the teardown asserts it was never called (Requirement 6.1; the
# Windows ProactorEventLoop subprocess semantics stay out of the gate).

SENTINEL_BIN = '/sentinel/mmhmem-bin-never-spawned'


@pytest.fixture
def no_spawn(monkeypatch):
    """Prove the injected-stream client never spawns: the spawn entry point
    fails loudly if reached, and the teardown asserts it stayed uncalled."""
    calls = []

    def deny(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('StdioMcpClient must never spawn a subprocess in tests')

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', deny)
    yield calls
    assert calls == []


@asynccontextmanager
async def mcp_client(mode: str = 'summarized', **kwargs):
    """A started StdioMcpClient against a fresh FakeMmhmem; both stopped after."""
    fake = FakeMmhmem(mode, **kwargs)
    await fake.start()
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=fake.reader, writer=fake.writer)
    await client.start()
    try:
        yield client, fake
    finally:
        await client.shutdown()
        await fake.stop()


async def shunt(client: econ.StdioMcpClient, content: str, *,
                timeout_ms: int = 2_000, project: str = 'mvibe',
                source_hint: str | None = None):
    """shunt_content with the design defaults filled in."""
    return await client.shunt_content(project=project, content=content, kind='file_read',
                                      source_hint=source_hint, timeout_ms=timeout_ms)


def test_shunt_result_field_set_matches_the_design_contract_and_is_frozen():
    assert [field.name for field in dataclasses.fields(econ.ShuntResult)] == [
        'outcome', 'summary', 'bypass_reason', 'content_sha256', 'line_count',
        'avoided_input_tokens_est']
    result = econ.ShuntResult(outcome='bypassed', summary=None,
                              bypass_reason='below_threshold', content_sha256='0' * 64,
                              line_count=3, avoided_input_tokens_est=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.outcome = 'summarized'


@pytest.mark.anyio
async def test_client_round_trip_summarized_matches_the_companion_contract(no_spawn):
    content = synthetic_read_content()
    sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
    async with mcp_client('summarized') as (client, _fake):
        result = await shunt(client, content, source_hint=HIST_READ_PATH)
        assert result.outcome == 'summarized'
        assert result.summary is not None and result.summary.startswith(f'SUM<{sha[:8]}>')
        assert result.bypass_reason is None
        assert result.content_sha256 == sha  # the real sha256, not a placeholder
        assert result.line_count == len(content.splitlines())
        assert result.avoided_input_tokens_est == len(content) // 4
        # a healthy round trip resets the restart back-off ladder
        client._backoff_s = 8.0
        await shunt(client, content)
        assert client._backoff_s == econ.RESTART_BACKOFF_S


@pytest.mark.anyio
async def test_client_handshake_order_and_wire_arguments_match_the_design(no_spawn):
    content = synthetic_read_content()
    async with mcp_client('summarized') as (client, fake):
        await shunt(client, content, project='proj-x', source_hint=HIST_READ_PATH)
        assert [method for method, _ in fake.calls] == [
            'initialize', 'notifications/initialized', 'tools/call']
        initialize = json.loads(fake.lines[0])
        assert initialize['id'] == 1
        assert initialize['params']['protocolVersion'] == '2025-06-18'
        assert initialize['params']['capabilities'] == {}
        assert initialize['params']['clientInfo']['name']
        notification = json.loads(fake.lines[1])
        assert notification['method'] == 'notifications/initialized'
        assert 'id' not in notification  # a notification is never answered
        method, params = fake.calls[2]
        assert (method, params['name']) == ('tools/call', 'shunt_content')
        assert params['arguments'] == {
            'project': 'proj-x', 'agent_harness': 'mvibe-proxy',
            'content': content, 'kind': 'file_read',
            'source_hint': HIST_READ_PATH,
        }
        assert json.loads(fake.lines[2])['id'] == 2  # the response is matched by id
        # source_hint is omitted entirely when there is none
        await shunt(client, 'tiny')
        assert 'source_hint' not in fake.calls[3][1]['arguments']
        # an idle healthy child is left running: start() re-initializes nothing
        await client.start()
        assert [method for method, _ in fake.calls] == [
            'initialize', 'notifications/initialized', 'tools/call', 'tools/call']


@pytest.mark.anyio
async def test_client_cached_and_bypassed_outcomes_and_absent_optional_fields(no_spawn):
    cached_content = synthetic_read_content()
    bypassed_content = synthetic_read_content(TRAILING_READ_PATH)
    override = {hashlib.sha256(cached_content.encode('utf-8')).hexdigest(): 'cached'}
    async with mcp_client('bypassed', modes_by_sha=override,
                          bypass_reason='security_critical') as (client, _fake):
        cached = await shunt(client, cached_content)
        assert cached.outcome == 'cached'
        assert cached.summary is not None and cached.summary.startswith('SUM<')
        assert cached.bypass_reason is None
        assert cached.content_sha256 == hashlib.sha256(
            cached_content.encode('utf-8')).hexdigest()
        # the fake's bypassed object omits latency_ms and worker_model entirely:
        # absent optional fields are tolerated, not required to be null
        bypassed = await shunt(client, bypassed_content)
        assert bypassed.outcome == 'bypassed'
        assert bypassed.summary is None
        assert bypassed.bypass_reason == 'security_critical'
        assert bypassed.content_sha256 == hashlib.sha256(
            bypassed_content.encode('utf-8')).hexdigest()
        assert bypassed.line_count == len(bypassed_content.splitlines())
        assert bypassed.avoided_input_tokens_est == 0


@pytest.mark.anyio
async def test_client_tool_error_is_one_sanitized_error_type(no_spawn):
    content = synthetic_read_content()
    async with mcp_client('tool_error') as (client, _fake):
        with pytest.raises(econ.McpError, match='^tool: worker not configured$') as excinfo:
            await shunt(client, content, source_hint=HIST_READ_PATH)
        assert isinstance(excinfo.value, RuntimeError)  # one error type: McpError
        message = str(excinfo.value)
        assert content[:80] not in message  # never the request content
        assert HIST_READ_PATH not in message  # never the source hint
        assert SENTINEL_BIN not in message  # never the binary path
        assert 'synthetic' not in message


@pytest.mark.anyio
async def test_client_malformed_line_is_a_protocol_error_without_echo(no_spawn):
    async with mcp_client('malformed') as (client, _fake):
        with pytest.raises(econ.McpError, match='^protocol:') as excinfo:
            await shunt(client, synthetic_read_content(), source_hint=HIST_READ_PATH)
        assert SENTINEL_BIN not in str(excinfo.value)
        assert 'boom' not in str(excinfo.value)  # the bad line is never echoed


@pytest.mark.anyio
async def test_client_timeout_on_a_hanging_call_leaves_the_child_usable(no_spawn):
    hanging = 'hangs-forever-content'
    healthy = synthetic_read_content()
    override = {hashlib.sha256(hanging.encode('utf-8')).hexdigest(): 'hang'}
    async with mcp_client('summarized', modes_by_sha=override) as (client, fake):
        with pytest.raises(econ.McpError, match='^timeout:') as excinfo:
            await shunt(client, hanging, timeout_ms=60)
        assert 'hangs-forever-content' not in str(excinfo.value)
        # a slow answer is not a dead child: the next call round-trips
        result = await shunt(client, healthy)
        assert result.outcome == 'summarized'
        assert sum(1 for method, _ in fake.calls if method == 'tools/call') == 2


@pytest.mark.anyio
async def test_client_matches_responses_by_id_under_interleaved_traffic(no_spawn):
    slow = 'slow-hash-content'
    fast = synthetic_read_content()
    override = {hashlib.sha256(slow.encode('utf-8')).hexdigest(): 'hang'}
    async with mcp_client('summarized', modes_by_sha=override) as (client, _fake):
        slow_task = asyncio.create_task(shunt(client, slow, timeout_ms=150))
        await asyncio.sleep(0)  # the slow request is written first
        fast_result = await shunt(client, fast)
        assert fast_result.content_sha256 == hashlib.sha256(
            fast.encode('utf-8')).hexdigest()
        # the pending slow call still times out: the ids never got confused
        with pytest.raises(econ.McpError, match='^timeout:'):
            await slow_task


@pytest.mark.anyio
async def test_client_eof_mid_request_fails_fast_and_schedules_capped_restart(no_spawn):
    async with mcp_client('close') as (client, fake):
        with pytest.raises(econ.McpError, match='^terminated:') as excinfo:
            await shunt(client, 'content-at-death', source_hint=HIST_READ_PATH)
        assert 'content-at-death' not in str(excinfo.value)
        assert SENTINEL_BIN not in str(excinfo.value)
        assert fake.closed is True
        # the restart is scheduled with the first capped back-off step (1 s)
        assert client.restart_backoffs == [econ.RESTART_BACKOFF_S]
        assert client.restart_at is not None
        assert client.restart_at > time.monotonic()
        # the next call fails fast too: no hang, no spawn, no retry storm
        with pytest.raises(econ.McpError, match='^terminated:'):
            await shunt(client, 'after-death', timeout_ms=100)


def raw_streams() -> tuple[asyncio.StreamReader, ChildStdinWriter]:
    """A brainless injected stream pair: every response is pre-fed by the test."""
    sink = asyncio.StreamReader(limit=2 ** 20)
    reader = asyncio.StreamReader(limit=2 ** 20)
    return reader, ChildStdinWriter(sink)


def feed_line(reader: asyncio.StreamReader, payload: dict) -> None:
    reader.feed_data((json.dumps(payload) + '\n').encode('utf-8'))


BAD_SHUNT_RESULTS = [
    {},
    {'content': []},
    {'content': [{'type': 'text'}]},
    {'content': [{'type': 'text', 'text': 'not-json{'}]},
    {'content': [{'type': 'text', 'text': '[1, 2]'}]},
    {'content': [{'type': 'text', 'text': json.dumps({
        'outcome': 'exploded', 'content_sha256': '0' * 64, 'line_count': 1,
        'avoided_input_tokens_est': 1})}]},
    {'content': [{'type': 'text', 'text': json.dumps({
        'outcome': 'summarized', 'line_count': 1,
        'avoided_input_tokens_est': 1})}]},
    'result-is-not-an-object',
]


@pytest.mark.anyio
@pytest.mark.parametrize('result', BAD_SHUNT_RESULTS)
async def test_client_protocol_violations_surface_as_sanitized_protocol_errors(
        no_spawn, result):
    reader, writer = raw_streams()
    feed_line(reader, {'jsonrpc': '2.0', 'id': 1, 'result': {'capabilities': {}}})
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=reader, writer=writer)
    await client.start()
    feed_line(reader, {'jsonrpc': '2.0', 'id': 2, 'result': result})
    with pytest.raises(econ.McpError, match='^protocol:') as excinfo:
        await shunt(client, 'protocol-fodder')
    assert 'protocol-fodder' not in str(excinfo.value)
    assert SENTINEL_BIN not in str(excinfo.value)
    await client.shutdown()


@pytest.mark.anyio
async def test_client_shutdown_fails_inflight_fast_closes_stdin_and_is_idempotent(
        no_spawn):
    hanging = 'inflight-at-shutdown'
    override = {hashlib.sha256(hanging.encode('utf-8')).hexdigest(): 'hang'}
    async with mcp_client('summarized', modes_by_sha=override) as (client, fake):
        inflight = asyncio.create_task(shunt(client, hanging, timeout_ms=10_000))
        await asyncio.sleep(0)
        await client.shutdown(timeout_s=0.2)
        with pytest.raises(econ.McpError, match='^terminated:'):
            await inflight
        with pytest.raises(ConnectionResetError):
            fake.writer.write(b'{"jsonrpc": "2.0"}\n')  # stdin is closed
        await client.shutdown(timeout_s=0.2)  # idempotent
        with pytest.raises(econ.McpError, match='^terminated:'):
            await shunt(client, 'post-shutdown', timeout_ms=50)


@pytest.mark.anyio
async def test_client_calls_before_start_fail_fast_without_spawning(no_spawn):
    fake = FakeMmhmem('summarized')
    await fake.start()
    try:
        client = econ.StdioMcpClient(SENTINEL_BIN, reader=fake.reader, writer=fake.writer)
        with pytest.raises(econ.McpError, match='^terminated:'):
            await shunt(client, 'never-started', timeout_ms=100)
        assert fake.calls == []  # nothing was ever written
        await client.shutdown()
    finally:
        await fake.stop()


def test_restart_backoff_doubles_from_one_second_up_to_the_thirty_second_cap():
    assert econ.RESTART_BACKOFF_S == 1.0
    assert econ.RESTART_BACKOFF_MAX_S == 30.0
    delays = []
    backoff = econ.RESTART_BACKOFF_S
    for _ in range(8):
        delays.append(backoff)
        backoff = econ._capped_backoff(backoff)
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


# The 2.3 review riders, applied with task 2.4: (a) the JSON-RPC error code is
# type-checked before it reaches an McpError message, (b) a server-originated
# request carrying a colliding id is never misrouted to a pending waiter, and
# (c) an EOF mid-request fails every in-flight waiter, not just the first.


@pytest.mark.anyio
@pytest.mark.parametrize('code', ['DROP TABLE tools', None, 1.5, True, [7], {'c': 1}])
async def test_client_rpc_error_codes_render_only_integers(no_spawn, code):
    reader, writer = raw_streams()
    feed_line(reader, {'jsonrpc': '2.0', 'id': 1, 'result': {'capabilities': {}}})
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=reader, writer=writer)
    await client.start()
    feed_line(reader, {'jsonrpc': '2.0', 'id': 2,
                       'error': {'code': code, 'message': 'server text'}})
    with pytest.raises(econ.McpError, match='^rpc error invalid$') as excinfo:
        await shunt(client, 'code-fodder')
    assert 'server text' not in str(excinfo.value)  # only the bounded tag is echoed
    await client.shutdown()


@pytest.mark.anyio
async def test_client_rpc_error_renders_an_integer_code_as_is(no_spawn):
    reader, writer = raw_streams()
    feed_line(reader, {'jsonrpc': '2.0', 'id': 1, 'result': {'capabilities': {}}})
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=reader, writer=writer)
    await client.start()
    feed_line(reader, {'jsonrpc': '2.0', 'id': 2,
                       'error': {'code': -32000, 'message': 'server text'}})
    with pytest.raises(econ.McpError, match='^rpc error -32000$') as excinfo:
        await shunt(client, 'code-fodder')
    assert 'server text' not in str(excinfo.value)
    await client.shutdown()


VALID_SHUNT_RESULT = {'content': [{'type': 'text', 'text': json.dumps({
    'outcome': 'summarized', 'summary': 'synthetic summary',
    'content_sha256': '0' * 64, 'line_count': 1,
    'avoided_input_tokens_est': 1})}]}


@pytest.mark.anyio
async def test_client_ignores_server_requests_carrying_a_colliding_id(no_spawn):
    reader, writer = raw_streams()
    feed_line(reader, {'jsonrpc': '2.0', 'id': 1, 'result': {'capabilities': {}}})
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=reader, writer=writer)
    await client.start()
    call = asyncio.create_task(shunt(client, 'colliding-id-fodder'))
    await asyncio.sleep(0)  # the call registered its waiter and wrote its request
    feed_line(reader, {'jsonrpc': '2.0', 'id': 2, 'method': 'sampling/createMessage',
                       'params': {'prompt': 'server-originated request'}})
    await asyncio.sleep(0)  # dispatched while the waiter is pending
    feed_line(reader, {'jsonrpc': '2.0', 'id': 2, 'result': VALID_SHUNT_RESULT})
    result = await call
    assert result.outcome == 'summarized'  # the colliding id never answered the call
    await client.shutdown()


@pytest.mark.anyio
async def test_client_eof_fails_every_inflight_waiter_at_once(no_spawn):
    async with mcp_client('close') as (client, fake):
        first = asyncio.create_task(shunt(client, 'dies-with-two-in-flight-one'))
        second = asyncio.create_task(shunt(client, 'dies-with-two-in-flight-two'))
        await asyncio.sleep(0)  # both requests written, both waiters pending
        for task in (first, second):
            with pytest.raises(econ.McpError,
                               match='^terminated: the mmhmem stdio child exited$'):
                await task
        assert fake.closed is True


# --- 2.4: background warm-up queue ----------------------------------------------
#
# WarmQueue between the sync request path and the client: schedule() is
# fire-and-forget, one fetch per unknown hash deduplicated while in flight and
# bounded by config.concurrency (Requirement 4.2), outcomes memoized (rendered
# summaries, permanent bypasses, failed hashes retried only after the back-off
# — Requirement 4.3), exactly one allowlisted econ_warm event per completion
# or failure (Requirement 5.4), and a bounded shutdown while a fetch hangs.


def econ_config(**overrides) -> EconConfig:
    """A directly constructed config (no env parsing) with the design defaults."""
    values = dict(mmhmem_bin=SENTINEL_BIN, project='mvibe',
                  tools=frozenset({'read'}), min_bytes=4096, timeout_ms=2_000,
                  memo_max=100, retry_s=300, concurrency=2)
    values.update(overrides)
    return EconConfig(**values)


def make_candidate(content: str, *, index: int = 0,
                   source_hint: str | None = HIST_READ_PATH) -> econ.Candidate:
    """A Candidate for arbitrary synthetic content, as Eligibility.select builds."""
    return econ.Candidate(
        index=index, tool_call_id=f'call-warm-{index}', tool='read',
        content=content, sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(),
        line_count=len(content.splitlines()), source_hint=source_hint)


def tools_calls(fake: FakeMmhmem) -> int:
    return sum(1 for method, _ in fake.calls if method == 'tools/call')


async def eventually(predicate, timeout_s: float = 2.0) -> bool:
    """Poll a sync predicate until truthy or the timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return bool(predicate())


@asynccontextmanager
async def warm_queue(mode: str = 'summarized', *, config: EconConfig,
                     events: list, **kwargs):
    """A WarmQueue over a started client and fake; everything stopped after."""
    fake = FakeMmhmem(mode, **kwargs)
    await fake.start()
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=fake.reader, writer=fake.writer)
    await client.start()
    memo = econ.Memo(config.memo_max)
    queue = econ.WarmQueue(client, memo, config, events.append)
    try:
        yield queue, client, fake, memo
    finally:
        await queue.shutdown(timeout_s=0.5)
        await client.shutdown()
        await fake.stop()


@pytest.mark.anyio
async def test_warm_queue_deduplicates_a_hash_while_its_fetch_is_in_flight(no_spawn):
    events: list = []
    candidate = make_candidate('hangs-in-flight-content')
    config = econ_config(timeout_ms=10_000)
    async with warm_queue('hang', config=config,
                          events=events) as (queue, _client, fake, memo):
        assert queue.schedule(candidate) is True
        assert queue.schedule(candidate) is False  # in flight: no second fetch
        assert queue.schedule(candidate) is False
        assert await eventually(lambda: tools_calls(fake) == 1)
        await asyncio.sleep(0.05)  # a duplicate fetch would have landed by now
        assert tools_calls(fake) == 1  # exactly one call per distinct hash (Req 4.2)
        assert events == []  # nothing completed while hanging
        assert len(memo) == 0


@pytest.mark.anyio
async def test_warm_queue_respects_the_concurrency_bound(no_spawn):
    events: list = []
    candidates = [make_candidate(f'bound-content-{n}', index=n) for n in range(4)]
    config = econ_config(timeout_ms=10_000, concurrency=2)
    async with warm_queue('hang', config=config,
                          events=events) as (queue, client, fake, _memo):
        inflight = 0
        peak = 0
        real = client.shunt_content

        async def observing(**kwargs):
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            try:
                return await real(**kwargs)
            finally:
                inflight -= 1

        client.shunt_content = observing  # the real client stays the boundary
        for candidate in candidates:
            assert queue.schedule(candidate) is True
        assert await eventually(lambda: tools_calls(fake) == 2)
        await asyncio.sleep(0.05)  # a third call would have reached the child by now
        assert peak == 2  # never above config.concurrency (Req 4.2)
        assert tools_calls(fake) == 2  # the other two wait on the semaphore
        assert events == []


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['summarized', 'cached'])
async def test_warm_queue_memorizes_rendered_summary_and_emits_one_event(
        no_spawn, mode):
    events: list = []
    content = synthetic_read_content()
    candidate = make_candidate(content)
    async with warm_queue(mode, config=econ_config(),
                          events=events) as (queue, _client, fake, memo):
        assert queue.schedule(candidate) is True
        assert await eventually(lambda: len(events) == 1)
        [event] = events
        assert set(event) == {'event', 'econ_outcome', 'econ_latency_ms',
                              'econ_in_bytes'}  # the allowlist, nothing else (5.4)
        assert event['event'] == 'econ_warm'
        assert event['econ_outcome'] == mode
        assert event['econ_in_bytes'] == len(content)
        assert isinstance(event['econ_latency_ms'], int)
        assert event['econ_latency_ms'] >= 0
        exported = json.dumps(event)
        assert content[:80] not in exported  # never content (Req 1.4, 5.4)
        assert candidate.sha256 not in exported  # hashes stay in memory only
        assert HIST_READ_PATH not in exported  # never hints or paths
        memoized = memo.get(candidate.sha256)
        assert memoized is not None and memoized.kind == 'summary'
        marker = econ.MARKER.format(hint=HIST_READ_PATH, lines=candidate.line_count,
                                    sha12=candidate.sha256[:12])
        assert memoized.summary is not None
        assert memoized.summary.startswith(marker)  # rendered text is stored (3.3)
        assert f'SUM<{candidate.sha256[:8]}>' in memoized.summary  # then the summary
        method, params = fake.calls[2]  # handshake consumed the first two lines
        assert (method, params['name']) == ('tools/call', 'shunt_content')
        assert params['arguments'] == {
            'project': 'mvibe', 'agent_harness': 'mvibe-proxy',
            'content': content, 'kind': 'file_read',
            'source_hint': HIST_READ_PATH,
        }
        assert queue.schedule(candidate) is False  # memoized: never fetched again
        assert tools_calls(fake) == 1


@pytest.mark.anyio
async def test_warm_queue_memorizes_a_bypass_permanently(no_spawn):
    events: list = []
    candidate = make_candidate(synthetic_read_content())
    async with warm_queue('bypassed', bypass_reason='security_critical',
                          config=econ_config(),
                          events=events) as (queue, _client, fake, memo):
        assert queue.schedule(candidate) is True
        assert await eventually(lambda: len(events) == 1)
        bypassed = memo.get(candidate.sha256)
        assert bypassed is not None and bypassed.kind == 'bypassed'
        assert bypassed.summary is None
        assert bypassed.reason == 'security_critical'
        assert events[0]['econ_outcome'] == 'bypassed'
        assert queue.schedule(candidate) is False  # permanent for the process
        assert tools_calls(fake) == 1


@pytest.mark.anyio
async def test_warm_queue_failures_are_memoized_and_retried_only_after_backoff(
        no_spawn):
    events: list = []
    candidate = make_candidate(synthetic_read_content())
    config = econ_config(retry_s=0.15)  # a sub-second back-off, test-only
    async with warm_queue('tool_error', config=config,
                          events=events) as (queue, _client, fake, memo):
        assert queue.schedule(candidate) is True
        assert await eventually(lambda: len(events) == 1)
        [event] = events
        assert event['econ_outcome'] == 'failed'
        assert 'worker not configured' not in json.dumps(event)  # sanitized
        failed = memo.get(candidate.sha256)
        assert failed is not None and failed.kind == 'failed'
        assert failed.reason == 'McpError'  # the error class, never its message
        assert failed.at > 0.0
        # not retried while the back-off runs (Req 4.3)
        assert queue.schedule(candidate) is False
        await asyncio.sleep(0.05)
        assert queue.schedule(candidate) is False
        assert tools_calls(fake) == 1
        # retried once the back-off expired
        assert await eventually(lambda: queue.schedule(candidate) is True)
        assert await eventually(lambda: tools_calls(fake) == 2)
        assert await eventually(lambda: len(events) == 2)
        assert events[1]['econ_outcome'] == 'failed'


@pytest.mark.anyio
async def test_warm_queue_shutdown_is_bounded_while_a_fetch_hangs(no_spawn):
    events: list = []
    candidate = make_candidate('hangs-at-shutdown-content')
    config = econ_config(timeout_ms=10_000)
    fake = FakeMmhmem('hang')
    await fake.start()
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=fake.reader, writer=fake.writer)
    await client.start()
    queue = econ.WarmQueue(client, econ.Memo(config.memo_max), config, events.append)
    try:
        assert queue.schedule(candidate) is True
        assert await eventually(lambda: tools_calls(fake) == 1)
        started = time.monotonic()
        await queue.shutdown(timeout_s=0.5)
        assert time.monotonic() - started < 1.0  # bounded even mid-hang
        assert not queue._tasks  # the cancelled fetch released its in-flight slot
        await queue.shutdown(timeout_s=0.5)  # idempotent
        assert events == []  # a cancelled fetch is neither completion nor failure
    finally:
        await client.shutdown()
        await fake.stop()


# --- 2.5: request-path rewrite (EconShunt) ---------------------------------------
#
# The real EconShunt over the real Memo, WarmQueue, Eligibility and
# StdioMcpClient (FakeMmhmem injected streams): first sight relays the body raw
# and schedules the background warm-up (Req 3.4); a memoized summary replaces
# ONLY the result's content — role, call identifier, position and every other
# message stay byte-identical (Req 3.1) — beginning with the fixed marker line
# from 2.4, byte-identical on every repeat in the process (Req 3.2/3.3); the
# counters carry eligible/replaced/bypassed/pending, the bytes before and after
# the rewrite and the labelled //4 token estimate (Req 5.3's payload — the
# event itself is wired by the diagnostics tasks); nothing eligible means no
# rewrite, no counters, no fetch, nothing set on a passed diagnostic.

BYPASS_REASON = 'security_critical'
HIST_READ_INDEX = 2   # openai_econ_body: the historical read result's position
SECOND_READ_INDEX = 5  # two_read_body: the second historical read result
OTHER_READ_PATH = 'src/synthetic_other.py'


def small_read_body() -> dict:
    """A historical read result below min_bytes: never eligible (Req 2.2)."""
    return {
        'model': 'mistral-large-4',
        'messages': [
            {'role': 'user', 'content': 'list the small file'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'call-small-read', 'type': 'function', 'function': {
                    'name': 'read',
                    'arguments': json.dumps({'filePath': 'small.txt'})}}]},
            {'role': 'tool', 'tool_call_id': 'call-small-read', 'content': 'x' * 100},
            {'role': 'user', 'content': 'thanks'},
        ],
    }


def trailing_only_body() -> dict:
    """The only read result sits in the trailing group: never eligible (2.3)."""
    return {
        'model': 'mistral-large-4',
        'messages': [
            {'role': 'user', 'content': 'read the module'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': TRAILING_READ_ID, 'type': 'function', 'function': {
                    'name': 'read',
                    'arguments': json.dumps({'filePath': TRAILING_READ_PATH})}}]},
            {'role': 'tool', 'tool_call_id': TRAILING_READ_ID,
             'content': synthetic_read_content(TRAILING_READ_PATH)},
        ],
    }


def shell_history_body() -> dict:
    """A large historical shell result: the allowlist never matches (2.2/2.5)."""
    return {
        'model': 'mistral-large-4',
        'messages': [
            {'role': 'user', 'content': 'run the suite twice'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': HIST_BASH_ID, 'type': 'function', 'function': {
                    'name': 'bash',
                    'arguments': json.dumps({'command': 'pytest -q'})}}]},
            {'role': 'tool', 'tool_call_id': HIST_BASH_ID,
             'content': SHELL_RESULT * 200},
            {'role': 'user', 'content': 'thanks'},
        ],
    }


def two_read_body() -> dict:
    """Two distinct historical read results (positions 2 and 4), both eligible."""
    return {
        'model': 'mistral-large-4',
        'messages': [
            {'role': 'user', 'content': 'read both modules'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': HIST_READ_ID, 'type': 'function', 'function': {
                    'name': 'read',
                    'arguments': json.dumps({'filePath': HIST_READ_PATH})}}]},
            {'role': 'tool', 'tool_call_id': HIST_READ_ID,
             'content': synthetic_read_content(HIST_READ_PATH)},
            {'role': 'user', 'content': 'now the other one'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'call-other-read', 'type': 'function', 'function': {
                    'name': 'read',
                    'arguments': json.dumps({'filePath': OTHER_READ_PATH})}}]},
            {'role': 'tool', 'tool_call_id': 'call-other-read',
             'content': synthetic_read_content(OTHER_READ_PATH)},
            {'role': 'user', 'content': 'compare them'},
        ],
    }


@asynccontextmanager
async def econ_shunt(mode: str = 'summarized', *, config: EconConfig | None = None,
                     events: list | None = None, **kwargs):
    """A real EconShunt over a started client + fake; everything stopped after."""
    if events is None:
        events = []
    fake = FakeMmhmem(mode, **kwargs)
    await fake.start()
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=fake.reader, writer=fake.writer)
    await client.start()
    shunt = econ.EconShunt(config or econ_config(), client, events.append)
    try:
        yield shunt, client, fake, events
    finally:
        await shunt.shutdown()
        await fake.stop()


def test_econ_counters_field_set_matches_the_design_contract_and_is_frozen():
    # The exact design field names: they feed the diagnostics allowlist
    # (econ_eligible .. econ_est_saved_tokens), ints only, nothing else.
    assert [field.name for field in dataclasses.fields(econ.EconCounters)] == [
        'eligible', 'replaced', 'bypassed', 'pending',
        'in_bytes', 'out_bytes', 'est_saved_tokens']
    counters = econ.EconCounters(1, 1, 0, 0, 4096, 128, 992)
    assert all(isinstance(getattr(counters, name), int) for name in
               ('eligible', 'replaced', 'bypassed', 'pending',
                'in_bytes', 'out_bytes', 'est_saved_tokens'))
    assert 'est' in 'est_saved_tokens'  # the estimate stays labelled (Req 5.1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        counters.eligible = 2


@pytest.mark.anyio
async def test_apply_first_sight_relays_raw_and_schedules_warmup(no_spawn):
    events: list = []
    body = openai_econ_body()
    pristine = copy.deepcopy(body)
    original = pristine['messages'][HIST_READ_INDEX]['content']
    original_message = body['messages'][HIST_READ_INDEX]
    async with econ_shunt(config=econ_config(), events=events) as (shunt, _c, fake, _e):
        counters = shunt.apply(body)
        assert counters is not None
        assert (counters.eligible, counters.replaced,
                counters.bypassed, counters.pending) == (1, 0, 0, 1)
        assert body == pristine  # raw relay: the body is untouched (Req 3.4)
        assert body['messages'][HIST_READ_INDEX] is original_message  # same object
        assert counters.in_bytes == len(original.encode('utf-8'))
        # Before/after over the eligible set: nothing replaced, so after == before
        # and the estimate is honestly zero, not the pending bytes (Req 5.1).
        assert counters.out_bytes == counters.in_bytes
        assert counters.est_saved_tokens == 0
        assert await eventually(lambda: tools_calls(fake) == 1)  # warm-up scheduled
        method, params = fake.calls[2]  # handshake consumed the first two lines
        assert (method, params['name']) == ('tools/call', 'shunt_content')
        assert params['arguments']['content'] == original
        assert params['arguments']['source_hint'] == HIST_READ_PATH
        await eventually(lambda: len(events) == 1)  # the scheduled fetch completes


@pytest.mark.anyio
async def test_apply_replaces_only_the_memoized_content_byte_identically(no_spawn):
    events: list = []
    config = econ_config()
    body = openai_econ_body()
    pristine = copy.deepcopy(body)
    original_message = body['messages'][HIST_READ_INDEX]
    async with econ_shunt(config=config, events=events) as (shunt, _c, _f, _e):
        assert shunt.apply(body).pending == 1
        assert await eventually(lambda: len(events) == 1)  # the warm fetch finished
        candidate = econ.select(pristine['messages'], config)[0]
        memoized = shunt.memo.get(candidate.sha256)
        assert memoized is not None and memoized.kind == 'summary'
        assert memoized.summary is not None

        counters = shunt.apply(body)
        assert counters is not None
        assert (counters.eligible, counters.replaced,
                counters.bypassed, counters.pending) == (1, 1, 0, 0)
        assert counters.in_bytes == len(candidate.content.encode('utf-8'))
        assert counters.out_bytes == len(memoized.summary.encode('utf-8'))
        assert counters.est_saved_tokens == (
            counters.in_bytes - counters.out_bytes) // 4

        expected = copy.deepcopy(pristine)
        expected['messages'][HIST_READ_INDEX]['content'] = memoized.summary
        assert body == expected  # ONLY that content differs, deep compare (3.1)
        assert json.dumps(body) == json.dumps(expected)  # byte-level, same order
        replaced = body['messages'][HIST_READ_INDEX]
        assert replaced is original_message  # mutated in place: position kept
        assert replaced['role'] == 'tool'  # role kept
        assert replaced['tool_call_id'] == HIST_READ_ID  # call identifier kept
        assert replaced['content'].startswith(  # the fixed marker line (3.2)
            econ.MARKER.format(hint=HIST_READ_PATH, lines=candidate.line_count,
                               sha12=candidate.sha256[:12]))
        assert f'SUM<{candidate.sha256[:8]}>' in replaced['content']  # then summary

        # Byte-identical on every repeat within the process (Req 3.2/3.3)
        shunt.apply(body)
        assert body['messages'][HIST_READ_INDEX]['content'] == memoized.summary


@pytest.mark.anyio
@pytest.mark.parametrize('build', [small_read_body, trailing_only_body,
                                   shell_history_body])
async def test_apply_returns_none_without_counters_or_fetch_when_nothing_eligible(
        no_spawn, build):
    events: list = []
    body = build()
    pristine = copy.deepcopy(body)
    diagnostic = types.SimpleNamespace()
    async with econ_shunt(config=econ_config(), events=events) as (shunt, _c, fake, _e):
        assert shunt.apply(body, diagnostic) is None  # None: no counters (5.3)
        assert body == pristine  # and no mutation at all
        assert tools_calls(fake) == 0  # no warm-up was ever scheduled
        assert events == []
        assert not hasattr(diagnostic, 'econ')  # nothing set on the diagnostic


@pytest.mark.anyio
async def test_apply_counts_a_memoized_bypass_and_relays_raw(no_spawn):
    events: list = []
    body = openai_econ_body()
    config = econ_config()
    async with econ_shunt('bypassed', bypass_reason=BYPASS_REASON, config=config,
                          events=events) as (shunt, _c, fake, _e):
        first = shunt.apply(body)
        assert (first.replaced, first.bypassed, first.pending) == (0, 0, 1)
        assert await eventually(lambda: len(events) == 1)  # bypass memoized
        second = shunt.apply(body)
        assert (second.eligible, second.replaced,
                second.bypassed, second.pending) == (1, 0, 1, 0)
        raw = openai_econ_body()['messages'][HIST_READ_INDEX]['content']
        assert body['messages'][HIST_READ_INDEX]['content'] == raw  # relayed raw
        assert second.out_bytes == second.in_bytes  # after == before: raw in full
        assert second.est_saved_tokens == 0
        assert tools_calls(fake) == 1  # a bypass is permanent: no refetch


@pytest.mark.anyio
async def test_apply_counts_a_recent_failure_as_pending_without_refetching(no_spawn):
    events: list = []
    body = openai_econ_body()
    config = econ_config(retry_s=300)  # the failure stays inside its back-off
    async with econ_shunt('tool_error', config=config,
                          events=events) as (shunt, _c, fake, _e):
        first = shunt.apply(body)
        assert (first.replaced, first.bypassed, first.pending) == (0, 0, 1)
        assert await eventually(lambda: len(events) == 1)
        assert events[0]['econ_outcome'] == 'failed'
        second = shunt.apply(body)
        # A failed fetch is not-yet-summarized: the pending bucket, not bypassed
        assert (second.replaced, second.bypassed, second.pending) == (0, 0, 1)
        assert second.est_saved_tokens == 0
        assert tools_calls(fake) == 1  # back-off: no new fetch (Req 4.3)
        raw = openai_econ_body()['messages'][HIST_READ_INDEX]['content']
        assert body['messages'][HIST_READ_INDEX]['content'] == raw  # raw relay


@pytest.mark.anyio
async def test_apply_counter_arithmetic_is_exact_on_a_constructed_case(no_spawn):
    events: list = []
    body = two_read_body()
    first_content = body['messages'][2]['content']
    second_content = body['messages'][SECOND_READ_INDEX]['content']
    first_sha = hashlib.sha256(first_content.encode('utf-8')).hexdigest()
    second_sha = hashlib.sha256(second_content.encode('utf-8')).hexdigest()
    config = econ_config(retry_s=300)
    async with econ_shunt(config=config, events=events,
                          modes_by_sha={first_sha: 'summarized',
                                        second_sha: 'tool_error'}) as (shunt, _c, fake, _e):
        opening = shunt.apply(body)
        assert (opening.eligible, opening.replaced,
                opening.bypassed, opening.pending) == (2, 0, 0, 2)
        assert await eventually(lambda: len(events) == 2)  # one summary, one failure

        memoized = shunt.memo.get(first_sha)
        assert memoized is not None and memoized.summary is not None
        counters = shunt.apply(body)
        # 2 eligible: 1 replaced, 1 pending (the failed one inside its back-off)
        assert (counters.eligible, counters.replaced,
                counters.bypassed, counters.pending) == (2, 1, 0, 1)
        first_bytes = len(first_content.encode('utf-8'))
        second_bytes = len(second_content.encode('utf-8'))
        rendered_bytes = len(memoized.summary.encode('utf-8'))
        assert counters.in_bytes == first_bytes + second_bytes  # before
        # after: the replacement plus the raw-relayed pending result in full
        assert counters.out_bytes == rendered_bytes + second_bytes
        assert counters.est_saved_tokens == (first_bytes - rendered_bytes) // 4
        assert counters.est_saved_tokens > 0
        assert tools_calls(fake) == 2  # one fetch per hash, none retried
        assert body['messages'][2]['content'] == memoized.summary
        assert body['messages'][SECOND_READ_INDEX]['content'] == second_content  # raw


@pytest.mark.anyio
async def test_apply_sets_plain_counters_on_a_passed_diagnostic_namespace(no_spawn):
    events: list = []
    body = openai_econ_body()
    async with econ_shunt(config=econ_config(), events=events) as (shunt, _c, _f, _e):
        assert shunt.apply(body).pending == 1
        assert await eventually(lambda: len(events) == 1)  # summary memoized
        diagnostic = types.SimpleNamespace()
        counters = shunt.apply(body, diagnostic)
        assert counters is not None
        assert diagnostic.econ is counters  # plain attribute: the 3.1/3.2 seam


@pytest.mark.anyio
async def test_warm_queue_survives_a_raising_emit_and_still_memoizes(no_spawn):
    # 2.4 review rider: a raising emit sink must not kill the fetch task or
    # cost it its already-recorded memo outcome (schedule()'s never-raise
    # discipline applied to the emit edge).
    def exploding_emit(event):
        raise RuntimeError('export sink down')

    content = synthetic_read_content()
    candidate = make_candidate(content)
    memo = econ.Memo(100)
    fake = FakeMmhmem('summarized')
    await fake.start()
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=fake.reader, writer=fake.writer)
    await client.start()
    queue = econ.WarmQueue(client, memo, econ_config(), exploding_emit)
    try:
        assert queue.schedule(candidate) is True
        task = queue._tasks[candidate.sha256]
        await asyncio.wait_for(task, 2.0)  # completes cleanly despite the emit
        memoized = memo.get(candidate.sha256)
        assert memoized is not None and memoized.kind == 'summary'
        assert memoized.summary is not None
    finally:
        await queue.shutdown()
        await client.shutdown()
        await fake.stop()


# --- 3.1: diagnostics allowlist and econ event fields -----------------------------
#
# The per-request diagnostic carries the econ counters EconShunt.apply records
# and emits them as ONE allowlisted event through the existing stdout and export
# channels — the counts, the bytes before and after, and the labelled //4 token
# estimate (Req 5.1), present only when economics acted and absent otherwise
# (Req 5.2/5.3), never carrying content, hints, paths or hashes (Req 1.4).
# Warm events ride the same channels with exactly WarmQueue's four fields
# (Req 5.4); the observability allowlist is the one export gate for both.

REQUEST_ECON_KEYS = frozenset({
    'econ_outcome', 'econ_eligible', 'econ_replaced', 'econ_bypassed',
    'econ_pending', 'econ_in_bytes', 'econ_out_bytes', 'econ_est_saved_tokens',
})
ALLOWED_ECON_KEYS = REQUEST_ECON_KEYS | {'econ_latency_ms'}
WARM_OUTCOMES = {'applied', 'none', 'error', 'summarized', 'cached',
                 'bypassed', 'failed'}


class ExportSink:
    """The exporter surface diagnostics talks to: enqueue(record) only."""

    def __init__(self):
        self.records: list = []

    def enqueue(self, record):
        self.records.append(dict(record))


def test_observability_allowlist_admits_exactly_the_design_econ_fields():
    # Req 5.1/1.4: the design's Data Models econ event table is the whole econ
    # surface of the allowlist; any other econ_* key is rejected outright.
    from mvibe_proxy.observability import _ALLOWED_FIELDS, allowlisted
    assert {key for key in _ALLOWED_FIELDS if key.startswith('econ_')} == ALLOWED_ECON_KEYS
    exported = allowlisted({
        'event': 'proxy_request', 'request_id': 'r1',
        'econ_outcome': 'applied', 'econ_eligible': 2, 'econ_replaced': 1,
        'econ_bypassed': 0, 'econ_pending': 1, 'econ_in_bytes': 8192,
        'econ_out_bytes': 4224, 'econ_est_saved_tokens': 992,
        'econ_latency_ms': None,
        'econ_content': 'SECRET-CONTENT', 'econ_source_hint': 'src/SECRET_PATH.py',
        'econ_summary': 'SECRET-CONTENT', 'econ_sha256': 'SECRET-HASH',
        'econ_path': 'src/SECRET_PATH.py', 'econ_tokens': 992,
    })
    assert exported == {
        'service': 'mvibe-proxy', 'event': 'proxy_request', 'request_id': 'r1',
        'econ_outcome': 'applied', 'econ_eligible': 2, 'econ_replaced': 1,
        'econ_bypassed': 0, 'econ_pending': 1, 'econ_in_bytes': 8192,
        'econ_out_bytes': 4224, 'econ_est_saved_tokens': 992,
    }


def test_warm_event_passes_the_allowlist_with_exactly_warmqueue_fields():
    # Req 5.4: WarmQueue emits {'event','econ_outcome','econ_latency_ms',
    # 'econ_in_bytes'}; the allowlist passes exactly those and drops content.
    from mvibe_proxy.observability import allowlisted
    exported = allowlisted({
        'event': 'econ_warm', 'econ_outcome': 'summarized',
        'econ_latency_ms': 12, 'econ_in_bytes': 5000,
        'content': 'SECRET-CONTENT', 'summary': 'SECRET-CONTENT',
        'source_hint': 'src/SECRET_PATH.py', 'econ_latency_ns': 12_345_678,
    })
    assert exported == {'service': 'mvibe-proxy', 'event': 'econ_warm',
                        'econ_outcome': 'summarized', 'econ_latency_ms': 12,
                        'econ_in_bytes': 5000}


def test_request_diagnostic_defaults_to_no_econ_fields(capsys):
    # Req 5.2/5.3: a request economics never touched emits no econ field.
    from mvibe_proxy import diagnostics
    sink = ExportSink()
    diagnostics.configure_stdout()
    diagnostics.configure_exporter(sink)
    try:
        diagnostic = diagnostics.RequestDiagnostic()
        assert diagnostic.econ is None
        diagnostic.emit()
    finally:
        diagnostics.disable_stdout()
        diagnostics.clear_exporter()
    row = json.loads(capsys.readouterr().out)
    assert not [key for key in row if key.startswith('econ_')]
    assert not [key for key in sink.records[0] if key.startswith('econ_')]


@pytest.mark.anyio
async def test_request_econ_event_present_when_acted_absent_otherwise(no_spawn, capsys):
    # Req 5.1/5.2/5.3: apply acting sets the counters on the diagnostic; emit
    # carries exactly the allowlisted per-request econ fields through both the
    # stdout and export channels; nothing eligible sets nothing at all.
    from mvibe_proxy import diagnostics
    from mvibe_proxy.observability import allowlisted
    sink = ExportSink()
    diagnostics.configure_stdout()
    diagnostics.configure_exporter(sink)
    try:
        body = openai_econ_body()
        diagnostic = diagnostics.RequestDiagnostic()
        async with econ_shunt() as (shunt, _client, _fake, _events):
            assert shunt.apply(small_read_body(), diagnostic) is None
            assert diagnostic.econ is None  # nothing eligible: no event (5.3)
            counters = shunt.apply(body, diagnostic)
            assert counters is not None
            assert diagnostic.econ is counters  # 2.5's seam, kept intact
        diagnostic.emit()
        row = json.loads(capsys.readouterr().out)
        assert {key for key in row if key.startswith('econ_')} == REQUEST_ECON_KEYS
        assert row['econ_outcome'] == 'applied'
        assert (row['econ_eligible'], row['econ_replaced'], row['econ_bypassed'],
                row['econ_pending']) == (counters.eligible, counters.replaced,
                                         counters.bypassed, counters.pending)
        assert (row['econ_in_bytes'], row['econ_out_bytes'],
                row['econ_est_saved_tokens']) == (counters.in_bytes,
                                                  counters.out_bytes,
                                                  counters.est_saved_tokens)
        assert len(sink.records) == 1
        exported = allowlisted(sink.records[0])
        assert {key for key in exported if key.startswith('econ_')} == REQUEST_ECON_KEYS
        assert exported['econ_eligible'] == counters.eligible
        assert exported['econ_est_saved_tokens'] == counters.est_saved_tokens
    finally:
        diagnostics.disable_stdout()
        diagnostics.clear_exporter()


def test_negative_est_saved_tokens_is_relayed_unchanged(capsys):
    # 2.5→3.1 handoff: a summary longer than its original makes the estimate
    # negative; the honest figure is emitted, never clamped or assumed >= 0.
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    try:
        diagnostic = diagnostics.RequestDiagnostic()
        diagnostic.econ = econ.EconCounters(
            eligible=1, replaced=1, bypassed=0, pending=0,
            in_bytes=500, out_bytes=900, est_saved_tokens=-100)
        diagnostic.emit()
    finally:
        diagnostics.disable_stdout()
    row = json.loads(capsys.readouterr().out)
    assert row['econ_outcome'] == 'applied'
    assert row['econ_est_saved_tokens'] == -100


def test_error_path_econ_mapping_emits_only_allowlisted_keys(capsys):
    # The proxy's apply wrapper records failures as a plain mapping (the
    # design's {'econ_outcome': 'error'}); only allowlisted names may leave.
    from mvibe_proxy import diagnostics
    sink = ExportSink()
    diagnostics.configure_stdout()
    diagnostics.configure_exporter(sink)
    try:
        diagnostic = diagnostics.RequestDiagnostic()
        diagnostic.econ = {'econ_outcome': 'error',
                           'content': 'SECRET-CONTENT',
                           'econ_source_hint': 'src/SECRET_PATH.py'}
        diagnostic.emit()
    finally:
        diagnostics.disable_stdout()
        diagnostics.clear_exporter()
    rendered = capsys.readouterr().out
    assert 'SECRET' not in rendered
    row = json.loads(rendered)
    assert {key for key in row if key.startswith('econ_')} == {'econ_outcome'}
    assert row['econ_outcome'] == 'error'
    assert {key for key in sink.records[0] if key.startswith('econ_')} == {'econ_outcome'}
    assert 'SECRET' not in json.dumps(sink.records[0])


@pytest.mark.anyio
async def test_no_content_hint_or_path_substrings_in_any_econ_field(no_spawn, capsys):
    # Req 1.4: a candidate whose summary would leak its content, path and hash
    # if wrongly included; the emitted event carries ints and the outcome enum
    # only — every value is checked, not just the known keys.
    from mvibe_proxy import diagnostics
    from mvibe_proxy.observability import allowlisted
    sink = ExportSink()
    diagnostics.configure_stdout()
    diagnostics.configure_exporter(sink)
    secret_path = 'src/SECRET_PATH/leaky.py'
    content = synthetic_read_content(path=secret_path) + 'SECRET-CONTENT sentinel\n'
    body = openai_econ_body()
    body['messages'][HIST_READ_INDEX]['content'] = content
    diagnostic = diagnostics.RequestDiagnostic()
    try:
        async with econ_shunt() as (shunt, _client, _fake, _events):
            assert shunt.apply(body, diagnostic) is not None
        diagnostic.emit()
    finally:
        diagnostics.disable_stdout()
        diagnostics.clear_exporter()
    rendered = capsys.readouterr().out + json.dumps(sink.records)
    assert 'SECRET-CONTENT' not in rendered
    assert secret_path not in rendered
    assert 'leaky' not in rendered
    exported = allowlisted(sink.records[0])
    for key, value in exported.items():
        if key == 'econ_outcome':
            assert value in WARM_OUTCOMES
        elif key.startswith('econ_'):
            assert isinstance(value, int)


@pytest.mark.anyio
async def test_warm_events_ride_the_same_stdout_and_export_channels(no_spawn, capsys):
    # Req 5.4: WarmQueue's event reaches the operator through diagnostics'
    # channels — emit_econ_warm is the sink the proxy hands the queue — with
    # exactly the four fields WarmQueue emits and nothing else.
    from mvibe_proxy import diagnostics
    from mvibe_proxy.observability import allowlisted
    sink = ExportSink()
    diagnostics.configure_stdout()
    diagnostics.configure_exporter(sink)
    candidate = make_candidate(synthetic_read_content())
    fake = FakeMmhmem('summarized')
    await fake.start()
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=fake.reader, writer=fake.writer)
    await client.start()
    queue = econ.WarmQueue(client, econ.Memo(100), econ_config(),
                           diagnostics.emit_econ_warm)
    try:
        assert queue.schedule(candidate) is True
        assert await eventually(lambda: sink.records != [])
    finally:
        await queue.shutdown()
        await client.shutdown()
        await fake.stop()
        diagnostics.disable_stdout()
        diagnostics.clear_exporter()
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    warm_rows = [row for row in rows if row.get('event') == 'econ_warm']
    assert len(warm_rows) == 1
    assert warm_rows[0]['econ_outcome'] == 'summarized'
    assert isinstance(warm_rows[0]['econ_latency_ms'], int)
    assert warm_rows[0]['econ_in_bytes'] == len(candidate.content)
    assert set(warm_rows[0]) == {'event', 'econ_outcome', 'econ_latency_ms',
                                 'econ_in_bytes'}
    assert len(sink.records) == 1
    exported = allowlisted(sink.records[0])
    assert 'timestamp' in exported  # the one allowlisted extra, as for requests
    exported.pop('timestamp')
    assert exported == {'service': 'mvibe-proxy', 'event': 'econ_warm',
                        'econ_outcome': 'summarized',
                        'econ_latency_ms': warm_rows[0]['econ_latency_ms'],
                        'econ_in_bytes': len(candidate.content)}
    assert 'synthetic_symbol' not in json.dumps(sink.records)


# --- 3.2: proxy wiring — switch, lifespan, hook ------------------------------------
#
# The --econ switch fails fast naming the missing or invalid setting (Req 1.5)
# while the unswitched proxy stays silently off (Req 1.1); the lifespan creates
# EconShunt on app.state.econ, spawns and supervises the mmhmem stdio child at
# startup, and stops it on shutdown; the hook runs after attach_request_metadata
# inside a guard that relays the original attributed body on any exception
# (Req 4.5) and skips bodies without tool results (Req 4.6). The upstream
# request carries the replaced content after warm-up for every dialect through
# the same translated-body rules (Req 2.4), responses stay byte-exact (Req 3.5)
# and the metadata output stays unchanged (Req 3.6).


def clear_econ_env(monkeypatch):
    for key in ECON_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_econ_switch_without_configuration_exits_two_naming_the_key(monkeypatch, capsys):
    # Req 1.5: --econ with nothing configured exits non-zero naming the key.
    clear_econ_env(monkeypatch)
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *a, **k: pytest.fail('must not serve'))
    with pytest.raises(SystemExit) as error:
        proxy.main(['--econ'])
    assert error.value.code == 2
    rendered = capsys.readouterr().err
    assert 'MVIBE_ECON_MMHMEM_BIN' in rendered
    assert '--econ' in rendered


def test_econ_switch_with_an_invalid_binary_exits_two_naming_the_key(monkeypatch, capsys, tmp_path):
    clear_econ_env(monkeypatch)
    monkeypatch.setenv('MVIBE_ECON_MMHMEM_BIN', str(tmp_path / 'missing-mmhmem'))
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *a, **k: pytest.fail('must not serve'))
    with pytest.raises(SystemExit) as error:
        proxy.main(['--econ'])
    assert error.value.code == 2
    assert 'MVIBE_ECON_MMHMEM_BIN' in capsys.readouterr().err


def test_econ_switch_with_a_malformed_numeric_setting_exits_two_naming_the_key(monkeypatch, capsys):
    clear_econ_env(monkeypatch)
    monkeypatch.setenv('MVIBE_ECON_MMHMEM_BIN', sys.executable)
    monkeypatch.setenv('MVIBE_ECON_MIN_BYTES', 'abc')
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *a, **k: pytest.fail('must not serve'))
    with pytest.raises(SystemExit) as error:
        proxy.main(['--econ'])
    assert error.value.code == 2
    assert 'MVIBE_ECON_MIN_BYTES' in capsys.readouterr().err


def test_invalid_econ_env_without_the_switch_warns_and_serves_disabled(monkeypatch, capsys, tmp_path):
    # Req 1.1/1.2: without the explicit switch an invalid config warns once and
    # the proxy serves with economics disabled (no child, no rewrite).
    clear_econ_env(monkeypatch)
    monkeypatch.setenv('MVIBE_ECON_MMHMEM_BIN', str(tmp_path / 'missing-mmhmem'))
    served = []
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda app, **k: served.append(app))
    proxy.main([])
    assert len(served) == 1
    assert served[0].state.econ is None
    rendered = capsys.readouterr().err
    assert 'MVIBE_ECON_MMHMEM_BIN' in rendered
    assert 'WARNING' in rendered


def run_main_capturing_app(monkeypatch, argv):
    """Drive proxy.main without serving; capture what create_app received."""
    captured = {}
    real_create_app = proxy.create_app

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_create_app(*args, **kwargs)

    monkeypatch.setattr(proxy, 'create_app', spy)
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda app, **k: None)
    proxy.main(argv)
    return captured


def test_a_complete_econ_environment_reaches_the_app_factory(monkeypatch):
    clear_econ_env(monkeypatch)
    monkeypatch.setenv('MVIBE_ECON_MMHMEM_BIN', sys.executable)
    captured = run_main_capturing_app(monkeypatch, ['--econ'])
    assert captured['econ_config'] is not None
    assert captured['econ_config'].mmhmem_bin == sys.executable


def test_no_econ_environment_passes_a_disabled_app_factory(monkeypatch):
    clear_econ_env(monkeypatch)
    captured = run_main_capturing_app(monkeypatch, [])
    assert captured['econ_config'] is None


@asynccontextmanager
async def wired_app(mode: str = 'summarized', *, config: EconConfig | None = None,
                    **kwargs):
    """A full app with economics wired: EconShunt on app.state.econ over an
    injected-stream client (FakeMmhmem behind it), so nothing ever spawns.
    ``config`` overrides the default econ_config() — 4.1's e2e cases drive
    their own back-off windows and timeouts through the real wiring."""
    fake = FakeMmhmem(mode, **kwargs)
    await fake.start()
    client = econ.StdioMcpClient(SENTINEL_BIN, reader=fake.reader, writer=fake.writer)
    app, _session, requests = econ_harness(econ_settings=config or econ_config(),
                                           econ_client=client)
    try:
        yield app, requests, client, fake
    finally:
        await fake.stop()


@pytest.mark.anyio
async def test_disabled_by_default_spawns_no_child_and_relays_history_raw(no_spawn):
    # Req 1.1: with no MVIBE_ECON_MMHMEM_BIN the app wires no economics object
    # (import-time default included) and relays byte-exact; the no_spawn
    # fixture fails loudly if any subprocess were attempted.
    app, _, requests = econ_harness()
    body = openai_econ_body()
    raw = json.dumps(body).encode()
    expected = attach_request_metadata(raw)
    assert getattr(proxy.app.state, 'econ', None) is None
    async with app.router.lifespan_context(app):
        assert app.state.econ is None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.post('/v1/chat/completions', content=raw)
            assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].content == expected


@pytest.mark.anyio
async def test_enabled_lifespan_creates_the_shunt_and_stops_the_child_on_shutdown(no_spawn):
    async with wired_app() as (app, _requests, client, _fake):
        async with app.router.lifespan_context(app):
            assert isinstance(app.state.econ, econ.EconShunt)
            assert app.state.econ.client is client
            assert client._closed is False
        assert client._closed is True  # the lifespan shutdown stopped the child
        assert app.state.econ is None


class ExplodingStartClient:
    """Stand-in client whose spawn/handshake fails, as a dead binary would."""

    def __init__(self):
        self.shutdown_calls = 0

    async def start(self):
        raise econ.McpError('spawn: permission denied (os error 13)')

    async def shutdown(self, timeout_s: float = 2.0) -> None:
        self.shutdown_calls += 1


@pytest.mark.anyio
async def test_child_startup_failure_fails_fast_with_an_actionable_error(no_spawn):
    stub = ExplodingStartClient()
    app, _, _requests = econ_harness(econ_settings=econ_config(), econ_client=stub)
    with pytest.raises(RuntimeError) as excinfo:
        async with app.router.lifespan_context(app):
            pass
    rendered = str(excinfo.value)
    assert 'MVIBE_ECON_MMHMEM_BIN' in rendered
    assert 'mmhmem stdio child' in rendered
    assert 'os error' in rendered  # the sanitized transport detail survives
    assert stub.shutdown_calls == 1  # bounded cleanup before the raise
    assert app.state.econ is None


@pytest.mark.anyio
@pytest.mark.parametrize('endpoint,build,headers,expect_replaced', [
    ('/v1/chat/completions', openai_econ_body, {}, True),
    ('/v1/messages', anthropic_econ_body, {'anthropic-version': '2023-06-01'}, True),
    ('/api/chat', ollama_econ_body, {}, False),
])
async def test_all_three_dialects_apply_economics_after_translation(
        no_spawn, endpoint, build, headers, expect_replaced):
    # Req 2.4: the hook sees the upstream-shaped body of every dialect. OpenAI
    # and Anthropic histories carry correlatable tool ids and are replaced
    # after warm-up; the Ollama translation drops tool ids entirely (the
    # pinned dialect reality from task 1.2), so the same eligibility rules
    # relay it raw on every resend.
    body = build()
    content = synthetic_read_content()
    sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
    raw = json.dumps(body).encode()

    def response_bytes(response):
        # The Ollama translator stamps a wall-clock created_at, so cross-run
        # byte equality is impossible for that dialect with or without
        # economics; compare everything except that field there.
        if endpoint == '/api/chat':
            row = json.loads(response.content)
            row.pop('created_at')
            return json.dumps(row, sort_keys=True)
        return response.content

    control_app, _, control_requests = econ_harness()
    async with control_app.router.lifespan_context(control_app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app),
                                     base_url='http://local') as client:
            control_response = await client.post(endpoint, content=raw, headers=headers)
    assert control_response.status_code == 200
    control_bytes = control_requests[0].content
    control_body = response_bytes(control_response)

    async with wired_app() as (app, requests, _client, _fake):
        async with app.router.lifespan_context(app):
            shunt = app.state.econ
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local') as client:
                first = await client.post(endpoint, content=raw, headers=headers)
                assert first.status_code == 200
                # First sight relays raw (Req 3.4): byte-identical to disabled.
                assert requests[0].content == control_bytes
                assert response_bytes(first) == control_body  # Req 3.5
                if expect_replaced:
                    assert await eventually(lambda: shunt.memo.get(sha) is not None)
                second = await client.post(endpoint, content=raw, headers=headers)
                assert second.status_code == 200
                assert response_bytes(second) == control_body  # Req 3.5
    assert len(requests) == 2
    if expect_replaced:
        before = json.loads(control_bytes)
        after = json.loads(requests[1].content)
        memoized = shunt.memo.get(sha)
        assert memoized is not None and memoized.summary is not None
        [index] = [i for i, message in enumerate(before['messages'])
                   if message.get('tool_call_id') == HIST_READ_ID]
        assert before['messages'][index]['content'] == content
        expected = copy.deepcopy(before)
        expected['messages'][index]['content'] = memoized.summary
        assert after == expected  # ONLY that content changed (Req 3.1 via wiring)
        assert after['metadata'] == before['metadata']  # Req 3.6
        assert after['messages'][index]['content'].startswith(  # the fixed marker
            econ.MARKER.format(hint=HIST_READ_PATH,
                               lines=len(content.splitlines()), sha12=sha[:12]))
    else:
        # Ollama: no correlatable ids, so every resend stays byte-identical raw.
        assert requests[1].content == control_bytes


@pytest.mark.anyio
async def test_apply_exception_relays_the_original_body_and_errors_the_outcome(
        no_spawn, capsys, monkeypatch):
    # Req 4.5: any exception inside the component relays the original attributed
    # body byte-identically (equal to the disabled run) and records the
    # allowlisted econ_outcome=error event.
    from mvibe_proxy import diagnostics
    raw = json.dumps(openai_econ_body()).encode()
    control_app, _, control_requests = econ_harness()
    async with control_app.router.lifespan_context(control_app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app),
                                     base_url='http://local') as client:
            control_response = await client.post('/v1/chat/completions', content=raw)
    control_bytes = control_requests[0].content

    diagnostics.configure_stdout()
    try:
        async with wired_app() as (app, requests, _client, _fake):
            async with app.router.lifespan_context(app):
                def explode(payload, diagnostic=None):
                    raise RuntimeError('boom SECRET-CONTENT')

                monkeypatch.setattr(app.state.econ, 'apply', explode)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                             base_url='http://local') as client:
                    response = await client.post('/v1/chat/completions', content=raw)
                    assert response.status_code == 200
                    assert response.content == control_response.content  # Req 3.5
        assert len(requests) == 1
        assert requests[0].content == control_bytes  # byte-identical raw relay
    finally:
        diagnostics.disable_stdout()
    row = json.loads(capsys.readouterr().out)
    assert row['econ_outcome'] == 'error'
    assert {key for key in row if key.startswith('econ_')} == {'econ_outcome'}
    assert 'SECRET' not in json.dumps(row)


@pytest.mark.anyio
async def test_bodies_without_tool_results_never_reach_apply(no_spawn):
    # Req 4.6: bodies without tool-result messages never call apply and never
    # fetch (no self-calls, no warm-up).
    plain = {'model': 'mistral-large-4', 'messages': [
        {'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'hi'}]}
    no_messages = {'model': 'mistral-large-4'}
    async with wired_app() as (app, requests, _client, fake):
        async with app.router.lifespan_context(app):
            seen = []
            real_apply = app.state.econ.apply

            def spying_apply(payload, diagnostic=None):
                seen.append(payload)
                return real_apply(payload, diagnostic)

            app.state.econ.apply = spying_apply
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local') as client:
                for body in (plain, no_messages):
                    response = await client.post('/v1/chat/completions',
                                                 content=json.dumps(body).encode())
                    assert response.status_code == 200
            assert seen == []
            assert tools_calls(fake) == 0
    assert len(requests) == 2


# --- 4.1: fail-open and determinism end to end --------------------------------------
#
# The whole app from 3.2 — econ_harness wiring: switch-level EconConfig, a
# lifespan-owned EconShunt, the hook inside make_acquire — driven against the
# fake child in its error, hang, malformed and bypass modes (Req 6.1): every
# POST relays raw until a summary exists, failures and bypasses are memoized
# with back-off and recovery (Req 4.3), replacement is byte-identical across
# many requests in one process (Req 3.3), the bounded memo's eviction costs
# exactly one further raw relay before re-warm (Req 4.4), and no emitted
# record ever carries content, hints or hashes (Req 1.4) — completing Req
# 6.2's coverage list beside the suite-wide gates of Req 6.4.

E2E_RETRY_S = 0.3      # sub-second back-off window, test-only (as in 2.4)
E2E_TIMEOUT_MS = 100   # a hanging child resolves fast, never blocking a POST
HANG_TIMEOUT_MS = 250  # long enough to POST again while the fetch is pending
E2E_POSTS = 10         # 'many requests' for the one-process determinism sweep


def hist_result(body: dict) -> str:
    """The historical read result's content in an upstream-shaped body."""
    [message] = [message for message in body['messages']
                 if message.get('tool_call_id') == HIST_READ_ID]
    return message['content']


def body_with_hist_content(content: str) -> bytes:
    """The OpenAI fixture with the historical read result's content swapped."""
    body = openai_econ_body()
    body['messages'][HIST_READ_INDEX]['content'] = content
    return json.dumps(body).encode()


async def post_chat(client: httpx.AsyncClient, raw: bytes) -> httpx.Response:
    """One full-app chat POST; a fail-open proxy always answers 200 here."""
    response = await client.post('/v1/chat/completions', content=raw)
    assert response.status_code == 200
    return response


@pytest.mark.anyio
async def test_e2e_tool_error_relays_raw_backs_off_then_recovers(no_spawn):
    # Req 4.3 e2e (tool error): the first POST relays raw, the failure is
    # memoized, no POST inside the back-off window reaches the child again,
    # and after expiry exactly one retry against a recovered child replaces
    # the content from then on.
    config = econ_config(retry_s=E2E_RETRY_S)
    content = synthetic_read_content()
    sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
    raw = json.dumps(openai_econ_body()).encode()
    async with wired_app('tool_error', config=config) as (app, requests, _c, fake):
        async with app.router.lifespan_context(app):
            shunt = app.state.econ
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local') as client:
                await post_chat(client, raw)
                assert hist_result(json.loads(requests[0].content)) == content
                assert await eventually(lambda: tools_calls(fake) == 1)
                failed = shunt.memo.get(sha)
                assert failed is not None
                assert (failed.kind, failed.reason) == ('failed', 'McpError')
                # Three more POSTs inside the back-off window: raw,
                # byte-identical, and the child stays silent.
                for _ in range(3):
                    await post_chat(client, raw)
                await asyncio.sleep(0.05)  # a wrong refetch would have landed
                assert tools_calls(fake) == 1
                assert {request.content for request in requests} == {
                    requests[0].content}
                # Back-off expiry with a recovered child: one retry fetch.
                fake.mode = 'summarized'
                assert await eventually(lambda: shunt.memo.retry_due(
                    failed, config.retry_s, time.monotonic()))
                await post_chat(client, raw)
                assert await eventually(lambda: tools_calls(fake) == 2)
                summary = shunt.memo.get(sha)
                assert summary is not None and summary.kind == 'summary'
                marker = econ.MARKER.format(hint=HIST_READ_PATH,
                                            lines=len(content.splitlines()),
                                            sha12=sha[:12])
                assert summary.summary is not None
                assert summary.summary.startswith(marker)
                # The next POST carries the marker replacement.
                await post_chat(client, raw)
                assert hist_result(json.loads(requests[-1].content)) == summary.summary
    assert len(requests) == 6


@pytest.mark.anyio
async def test_e2e_hang_times_out_fail_open_and_app_stays_responsive(no_spawn):
    # Req 4.3/4.1 e2e (hang): the POST never waits on the child, the hanging
    # fetch is deduplicated, times out into one memoized failure, the warm
    # queue drains, and the app keeps serving byte-identical raw relays inside
    # the back-off — with no unbounded task growth.
    config = econ_config(timeout_ms=HANG_TIMEOUT_MS, retry_s=300)
    content = synthetic_read_content()
    sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
    raw = json.dumps(openai_econ_body()).encode()
    async with wired_app('hang', config=config) as (app, requests, _c, fake):
        async with app.router.lifespan_context(app):
            shunt = app.state.econ
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local') as client:
                baseline = len(asyncio.all_tasks())
                await post_chat(client, raw)
                assert sha in shunt.warm._tasks  # exactly one fetch in flight
                # Another POST succeeds while the hang is pending (Req 4.1).
                await post_chat(client, raw)
                assert tools_calls(fake) == 1  # deduplicated (Req 4.2)
                assert requests[1].content == requests[0].content  # raw both
                # The timeout memoizes one failure and drains the queue.
                assert await eventually(lambda: shunt.memo.get(sha) is not None
                                        and shunt.memo.get(sha).kind == 'failed')
                assert shunt.warm._tasks == {}
                # Subsequent POSTs stay raw with no further child call.
                await post_chat(client, raw)
                await asyncio.sleep(0.05)
                assert tools_calls(fake) == 1
                assert hist_result(json.loads(requests[-1].content)) == content
                # No unbounded task growth across the whole sequence.
                assert len(asyncio.all_tasks()) <= baseline + 2
    assert len(requests) == 3


@pytest.mark.anyio
async def test_e2e_malformed_line_relays_raw_backs_off_then_recovers(no_spawn):
    # Req 4.3 e2e (protocol error): a non-JSON stdout line memoizes one
    # failure, every POST relays raw until the back-off expires, and the
    # recovered child's summary replaces the content from then on.
    config = econ_config(retry_s=E2E_RETRY_S)
    content = synthetic_read_content()
    sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
    raw = json.dumps(openai_econ_body()).encode()
    async with wired_app('malformed', config=config) as (app, requests, _c, fake):
        async with app.router.lifespan_context(app):
            shunt = app.state.econ
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local') as client:
                await post_chat(client, raw)
                assert await eventually(lambda: tools_calls(fake) == 1)
                failed = shunt.memo.get(sha)
                assert failed is not None
                assert (failed.kind, failed.reason) == ('failed', 'McpError')
                for _ in range(2):
                    await post_chat(client, raw)
                await asyncio.sleep(0.05)
                assert tools_calls(fake) == 1  # silent inside the back-off
                for request in requests:
                    assert hist_result(json.loads(request.content)) == content
                fake.mode = 'summarized'  # the child starts behaving
                assert await eventually(lambda: shunt.memo.retry_due(
                    failed, config.retry_s, time.monotonic()))
                await post_chat(client, raw)
                assert await eventually(lambda: tools_calls(fake) == 2)
                await post_chat(client, raw)
                marker = econ.MARKER.format(hint=HIST_READ_PATH,
                                            lines=len(content.splitlines()),
                                            sha12=sha[:12])
                replaced = hist_result(json.loads(requests[-1].content))
                assert replaced.startswith(marker)
                assert f'SUM<{sha[:8]}>' in replaced
                assert shunt.memo.get(sha).kind == 'summary'
    assert len(requests) == 5


@pytest.mark.anyio
async def test_e2e_bypass_is_permanent_raw_relay_with_zero_further_calls(
        no_spawn, capsys):
    # Req 4.3 e2e (bypass): a security_critical bypass is memoized
    # permanently — every POST relays raw, the child is called exactly once
    # (retry_s=0 proves the bypass outlives any back-off), and the request
    # event reports bypassed=1 once the decision is memoized.
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    try:
        config = econ_config(retry_s=0)
        content = synthetic_read_content()
        sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
        raw = json.dumps(openai_econ_body()).encode()
        async with wired_app('bypassed', bypass_reason='security_critical',
                             config=config) as (app, requests, _c, fake):
            async with app.router.lifespan_context(app):
                shunt = app.state.econ
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                             base_url='http://local') as client:
                    await post_chat(client, raw)
                    assert await eventually(lambda: shunt.memo.get(sha) is not None)
                    bypassed = shunt.memo.get(sha)
                    assert bypassed is not None
                    assert (bypassed.kind, bypassed.reason) == (
                        'bypassed', 'security_critical')
                    for _ in range(5):
                        await post_chat(client, raw)
                    assert tools_calls(fake) == 1  # never refetched, ever
                    for request in requests:
                        assert hist_result(json.loads(request.content)) == content
    finally:
        diagnostics.disable_stdout()
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    request_rows = [row for row in rows if row.get('event') == 'proxy_request']
    assert len(request_rows) == 6
    assert request_rows[0]['econ_pending'] == 1  # first sight: raw + scheduled
    for row in request_rows[1:]:
        assert (row['econ_bypassed'], row['econ_replaced'],
                row['econ_pending']) == (1, 0, 0)
    warm_rows = [row for row in rows if row.get('event') == 'econ_warm']
    assert [row['econ_outcome'] for row in warm_rows] == ['bypassed']


@pytest.mark.anyio
async def test_e2e_replacement_byte_identical_across_many_requests(no_spawn):
    # Req 3.3 e2e: after one warm-up the same body POSTed ten times in one
    # app process forwards byte-identical upstream bodies — equal to the
    # disabled control with exactly the historical content swapped for the
    # marker text — from exactly one child call (Req 4.2).
    raw = json.dumps(openai_econ_body()).encode()
    control_app, _, control_requests = econ_harness()
    async with control_app.router.lifespan_context(control_app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app),
                                     base_url='http://local') as client:
            await post_chat(client, raw)
    control_bytes = control_requests[0].content
    control_body = json.loads(control_bytes)

    content = synthetic_read_content()
    sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
    async with wired_app('summarized') as (app, requests, _c, fake):
        async with app.router.lifespan_context(app):
            shunt = app.state.econ
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local') as client:
                await post_chat(client, raw)
                assert requests[0].content == control_bytes  # raw first sight
                assert await eventually(lambda: shunt.memo.get(sha) is not None
                                        and shunt.memo.get(sha).kind == 'summary')
                for _ in range(E2E_POSTS):
                    await post_chat(client, raw)
                assert tools_calls(fake) == 1  # one call per distinct hash
    assert len(requests) == 1 + E2E_POSTS
    memoized = shunt.memo.get(sha)
    assert memoized is not None and memoized.summary is not None
    expected = copy.deepcopy(control_body)
    expected['messages'][HIST_READ_INDEX]['content'] = memoized.summary
    assert len({request.content for request in requests[1:]}) == 1  # Req 3.3
    assert json.loads(requests[1].content) == expected  # only that content
    assert hist_result(json.loads(requests[1].content)) == memoized.summary


@pytest.mark.anyio
async def test_e2e_memo_lru_bounds_eviction_costs_one_raw_relay(no_spawn):
    # Req 4.4 e2e: a two-entry memo evicts the oldest warmed hash once a
    # third distinct content arrives; the evicted hash relays raw exactly one
    # more time, re-warms and is replaced again — never an error.
    config = econ_config(memo_max=2)
    contents = [synthetic_read_content(path=f'src/synthetic_lru_{n}.py')
                for n in range(3)]
    shas = [hashlib.sha256(content.encode('utf-8')).hexdigest()
            for content in contents]
    raws = [body_with_hist_content(content) for content in contents]
    async with wired_app('summarized', config=config) as (app, requests, _c, fake):
        async with app.router.lifespan_context(app):
            shunt = app.state.econ
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local') as client:
                for raw, sha in zip(raws[:2], shas[:2]):
                    await post_chat(client, raw)
                    assert await eventually(
                        lambda sha=sha: shunt.memo.get(sha) is not None
                        and shunt.memo.get(sha).kind == 'summary')
                assert len(shunt.memo) == 2
                await post_chat(client, raws[2])  # a third distinct hash
                assert await eventually(
                    lambda: len(shunt.memo) == 2
                    and shunt.memo.get(shas[2]) is not None)
                assert shunt.memo.get(shas[0]) is None  # oldest evicted
                assert tools_calls(fake) == 3
                # The evicted hash: one further raw relay, then a fresh fetch.
                await post_chat(client, raws[0])
                assert hist_result(json.loads(requests[-1].content)) == contents[0]
                assert await eventually(lambda: tools_calls(fake) == 4)
                assert await eventually(
                    lambda: shunt.memo.get(shas[0]) is not None
                    and shunt.memo.get(shas[0]).kind == 'summary')
                await post_chat(client, raws[0])
                marker = econ.MARKER.format(hint='src/synthetic_lru_0.py',
                                            lines=len(contents[0].splitlines()),
                                            sha12=shas[0][:12])
                replaced = hist_result(json.loads(requests[-1].content))
                assert replaced.startswith(marker)
                assert f'SUM<{shas[0][:8]}>' in replaced
    assert len(requests) == 5  # warm x3, one raw relay after eviction, one replaced


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['summarized', 'tool_error', 'malformed',
                                  'hang', 'bypassed'])
async def test_e2e_no_content_hint_or_hash_in_any_emitted_record(
        no_spawn, capsys, mode):
    # Req 1.4 e2e: secret content and a secret path in the read header (the
    # source hint) never appear in any stdout event row or exported record,
    # across every fake mode; every econ_* value is an int or the outcome
    # enum. (No auth token exists anywhere in the econ path — stdio pipes
    # need none — and the observability suite pins token redaction.)
    from mvibe_proxy import diagnostics
    from mvibe_proxy.observability import allowlisted
    sink = ExportSink()
    diagnostics.configure_stdout()
    diagnostics.configure_exporter(sink)
    try:
        secret_path = 'src/SECRET_PATH/vault.py'
        secret_content = (synthetic_read_content(path=secret_path)
                          + 'SECRET-CONTENT sentinel\n')
        sha = hashlib.sha256(secret_content.encode('utf-8')).hexdigest()
        raw = body_with_hist_content(secret_content)
        kwargs = {'bypass_reason': 'security_critical'} if mode == 'bypassed' else {}
        async with wired_app(mode, config=econ_config(
                timeout_ms=E2E_TIMEOUT_MS), **kwargs) as (app, _req, _c, _fake):
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                             base_url='http://local') as client:
                    for _ in range(3):
                        await post_chat(client, raw)
                    await asyncio.sleep(0.25)  # let warm failures time out
    finally:
        diagnostics.disable_stdout()
        diagnostics.clear_exporter()
    out = capsys.readouterr().out
    rows = [json.loads(line) for line in out.splitlines()]
    assert any('econ_outcome' in row for row in rows)  # non-vacuous sweep
    assert any(record.get('event') == 'econ_warm' for record in sink.records)
    for row in rows:
        assert {key for key in row if key.startswith('econ_')} <= ALLOWED_ECON_KEYS
        for key, value in row.items():
            if key == 'econ_outcome':
                assert value in WARM_OUTCOMES
            elif key.startswith('econ_'):
                assert isinstance(value, int)
    for record in sink.records:
        exported = allowlisted(record)
        assert set(exported) - {'service'} <= set(record)
        for key, value in exported.items():
            if key == 'econ_outcome':
                assert value in WARM_OUTCOMES
            elif key.startswith('econ_'):
                assert isinstance(value, int)
    rendered = out + json.dumps(sink.records)
    assert 'SECRET-CONTENT' not in rendered
    assert secret_path not in rendered
    assert 'vault' not in rendered
    assert sha not in rendered  # hashes stay in memory only
