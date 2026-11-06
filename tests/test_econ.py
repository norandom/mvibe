"""Offline unit contracts for the token-economics configuration (proxy-econ-client 1.1)
and the fake mmhmem stdio double with synthetic dialect fixtures (proxy-econ-client 1.2)."""
import asyncio
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
