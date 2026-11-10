"""Offline unit contracts for the proxy stats module (proxy-stats-propagation):
operator configuration parsed from MVIBE_STATS_* (task 2.1, Requirements 2.5,
2.7, 5.2), the passive upstream usage observer (task 2.2, Requirements 1.1,
1.2, 1.4, 1.5, 1.6) and the in-process ledger and counters fed by the
diagnostics sink (task 2.3, Requirements 1.7, 3.1, 3.4), the throttled usage
reporter and the `mmhmem stats --json` runner (task 2.4, Requirements 2.1,
2.2, 2.3, 2.4, 3.2, 3.3)."""
import ast
import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone
import inspect
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

import pytest

from fakes import Clock
from mvibe_proxy import diagnostics, stats
from mvibe_proxy.diagnostics import GateRecord, RequestDiagnostic
from mvibe_proxy.econ import EconCounters, McpError, StdioMcpClient
from mvibe_proxy.stats import (
    ProxyCounters,
    StatsConfig,
    StatsConfigError,
    Usage,
    UsageLedger,
    UsageObserver,
)

USAGE = 'MVIBE_STATS_USAGE'
INTERVAL = 'MVIBE_STATS_REPORT_INTERVAL_S'
TIMEOUT = 'MVIBE_STATS_MMHMEM_TIMEOUT_S'
STATS_BIN = 'MVIBE_STATS_MMHMEM_BIN'
ECON_BIN = 'MVIBE_ECON_MMHMEM_BIN'
PROJECT = 'MVIBE_ECON_PROJECT'
STATS_KEYS = (USAGE, INTERVAL, TIMEOUT, STATS_BIN, ECON_BIN, PROJECT)


@pytest.fixture
def executable(tmp_path):
    """Return a factory for existing executable files under tmp_path."""
    def make(name: str = 'mmhmem') -> str:
        path = tmp_path / name
        path.write_bytes(b'#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
        return str(path)
    return make


# --- configuration defaults (Requirements 5.2, 2.7) ---------------------------

def test_unset_env_yields_inert_defaults():
    config = StatsConfig.from_env({})
    assert config.usage is False
    assert config.report_interval_s == 60
    assert config.mmhmem_timeout_s == 10
    assert config.mmhmem_bin is None
    assert config.project == 'mvibe'
    assert isinstance(config.report_interval_s, float)
    assert isinstance(config.mmhmem_timeout_s, float)


def test_direct_construction_uses_documented_defaults():
    assert StatsConfig() == StatsConfig.from_env({})


def test_blank_values_are_treated_as_unset():
    for blank in ('', '   '):
        assert StatsConfig.from_env({key: blank for key in STATS_KEYS}) == StatsConfig.from_env({})


def test_unrelated_environment_is_ignored():
    env = {'MISTRAL_API_KEY': 'x', 'MVIBE_ECON_MIN_BYTES': 'abc', 'MVIBE_RATE_LIMIT_RPS': 'zero'}
    assert StatsConfig.from_env(env) == StatsConfig.from_env({})


def test_from_env_reads_os_environ_only_when_env_is_none(monkeypatch, executable):
    binary = executable()
    for key in STATS_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert StatsConfig.from_env().usage is False
    monkeypatch.setenv(USAGE, '1')
    monkeypatch.setenv(STATS_BIN, binary)
    config = StatsConfig.from_env()
    assert config.usage is True and config.mmhmem_bin == binary
    # An explicit mapping is the only source when given.
    assert StatsConfig.from_env({}) == StatsConfig()


def test_config_is_frozen_and_carries_no_economics_switch():
    config = StatsConfig.from_env({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.usage = True
    # Requirement 2.7: the stats config only knows its own switches.
    assert [f.name for f in dataclasses.fields(StatsConfig)] == [
        'usage', 'report_interval_s', 'mmhmem_timeout_s', 'mmhmem_bin', 'project']


def test_config_error_is_a_value_error():
    assert issubclass(StatsConfigError, ValueError)


# --- the usage switch (Requirements 2.5, 5.2) ---------------------------------

def test_observable_enabled_usage_with_a_stats_binary(executable):
    binary = executable()
    config = StatsConfig.from_env({USAGE: '1', STATS_BIN: binary})
    assert config.usage is True
    assert config.mmhmem_bin == binary


@pytest.mark.parametrize('value', ('1', 'true', 'TRUE', 'True', ' yes ', 'YES', 'on', 'On', '\ton\n'))
def test_truthy_forms_enable_usage(value, executable):
    assert StatsConfig.from_env({USAGE: value, STATS_BIN: executable()}).usage is True


@pytest.mark.parametrize('value', ('0', 'false', 'FALSE', 'no', 'No', 'off', ' OFF ', '', '   '))
def test_falsy_and_blank_forms_leave_usage_off(value):
    assert StatsConfig.from_env({USAGE: value}).usage is False


@pytest.mark.parametrize('value', ('2', 'enabled', 'y', 'tru', 'onn', '1.0', '-1', 'yes please'))
def test_unrecognized_usage_value_raises_naming_the_variable(value):
    # Echoing is covered by test_errors_never_echo_any_environment_value; short
    # values such as 'y' are substrings of the fixed option list in the message.
    with pytest.raises(StatsConfigError, match=USAGE):
        StatsConfig.from_env({USAGE: value})


def test_enabled_usage_without_any_binary_raises_naming_both_settings():
    with pytest.raises(StatsConfigError) as excinfo:
        StatsConfig.from_env({USAGE: 'yes'})
    message = str(excinfo.value)
    assert USAGE in message and STATS_BIN in message


def test_enabled_usage_with_blank_binaries_raises_naming_both_settings():
    with pytest.raises(StatsConfigError) as excinfo:
        StatsConfig.from_env({USAGE: 'on', STATS_BIN: '  ', ECON_BIN: ''})
    message = str(excinfo.value)
    assert USAGE in message and STATS_BIN in message


# --- numeric settings ---------------------------------------------------------

def test_explicit_numbers_are_parsed():
    config = StatsConfig.from_env({INTERVAL: ' 30 ', TIMEOUT: '2.5'})
    assert config.report_interval_s == 30.0
    assert config.mmhmem_timeout_s == 2.5


INVALID_NUMBERS = ('abc', '0', '0.0', '-1', '-0.5', 'nan', 'inf', '-inf', '1e', '0x10')


@pytest.mark.parametrize('key', (INTERVAL, TIMEOUT))
@pytest.mark.parametrize('value', INVALID_NUMBERS)
def test_invalid_numbers_raise_naming_the_variable(key, value):
    with pytest.raises(StatsConfigError, match=key) as excinfo:
        StatsConfig.from_env({key: value})
    assert 'positive number' in str(excinfo.value)


# --- the mmhmem binary (Requirements 2.5, 2.7) --------------------------------

def test_econ_binary_is_the_fallback(executable):
    binary = executable()
    config = StatsConfig.from_env({ECON_BIN: binary})
    assert config.mmhmem_bin == binary
    # The fallback never switches usage on by itself.
    assert config.usage is False
    assert StatsConfig.from_env({USAGE: 'true', ECON_BIN: binary}).usage is True


def test_stats_binary_alone_does_not_enable_usage(executable):
    config = StatsConfig.from_env({STATS_BIN: executable()})
    assert config.usage is False and config.mmhmem_bin is not None


def test_surrounding_whitespace_is_stripped_from_the_binary(executable):
    binary = executable()
    assert StatsConfig.from_env({STATS_BIN: f'  {binary}\t'}).mmhmem_bin == binary


def test_the_same_binary_under_both_keys_is_accepted(executable, monkeypatch, tmp_path):
    binary = executable()
    assert StatsConfig.from_env({STATS_BIN: binary, ECON_BIN: binary}).mmhmem_bin == binary
    # Relative and absolute spellings of one file are the same binary.
    monkeypatch.chdir(tmp_path)
    config = StatsConfig.from_env({STATS_BIN: 'mmhmem', ECON_BIN: binary})
    assert config.mmhmem_bin == 'mmhmem'


def test_conflicting_binaries_raise_naming_both_keys(executable):
    first, second = executable('mmhmem-a'), executable('mmhmem-b')
    with pytest.raises(StatsConfigError) as excinfo:
        StatsConfig.from_env({STATS_BIN: first, ECON_BIN: second})
    message = str(excinfo.value)
    assert STATS_BIN in message and ECON_BIN in message
    assert first not in message and second not in message
    assert 'mmhmem-a' not in message and 'mmhmem-b' not in message


@pytest.mark.parametrize('key', (STATS_BIN, ECON_BIN))
def test_missing_binary_raises_naming_its_key(key, tmp_path):
    missing = str(tmp_path / 'absent' / 'mmhmem-missing')
    with pytest.raises(StatsConfigError, match=key) as excinfo:
        StatsConfig.from_env({key: missing})
    assert 'mmhmem-missing' not in str(excinfo.value)


def test_directory_binary_raises_naming_its_key(tmp_path):
    with pytest.raises(StatsConfigError, match=STATS_BIN) as excinfo:
        StatsConfig.from_env({STATS_BIN: str(tmp_path)})
    assert str(tmp_path) not in str(excinfo.value)


def test_invalid_binary_raises_even_while_usage_is_off(tmp_path):
    # main() decides between exit 2 and a warning; the config always validates.
    with pytest.raises(StatsConfigError, match=STATS_BIN):
        StatsConfig.from_env({USAGE: 'off', STATS_BIN: str(tmp_path / 'nope')})


def test_not_executable_branch_with_patched_predicate(executable, monkeypatch):
    # Windows os.access reports X_OK for any existing file, so the
    # not-executable branch is proven through the module predicate.
    binary = executable()
    monkeypatch.setattr(stats, '_is_executable_file', lambda path: False)
    with pytest.raises(StatsConfigError, match=STATS_BIN):
        StatsConfig.from_env({STATS_BIN: binary})


@pytest.mark.skipif(sys.platform == 'win32',
                    reason='Windows os.access X_OK is true for any existing file')
def test_non_executable_binary_raises_on_posix(tmp_path):
    binary = tmp_path / 'mmhmem'
    binary.write_bytes(b'')
    binary.chmod(0o644)
    with pytest.raises(StatsConfigError, match=STATS_BIN):
        StatsConfig.from_env({STATS_BIN: str(binary)})


# --- project -----------------------------------------------------------------

def test_project_comes_from_the_econ_project_setting():
    assert StatsConfig.from_env({PROJECT: '  team-alpha '}).project == 'team-alpha'
    assert StatsConfig.from_env({PROJECT: '   '}).project == 'mvibe'


# --- messages echo no values ---------------------------------------------------

def test_errors_never_echo_any_environment_value(tmp_path, executable):
    binary = executable('sentinel-mmhmem-binary')
    other = executable('sentinel-other-binary')
    base = {
        PROJECT: 'sentinel-project-omega',
        INTERVAL: '37.25',
        TIMEOUT: '13.75',
        'MISTRAL_API_KEY': 'sk-sentinel-secret-value',
    }
    cases = [
        {USAGE: 'sentinel-switch'},
        {INTERVAL: 'sentinel-interval'},
        {TIMEOUT: 'sentinel-timeout'},
        {STATS_BIN: str(tmp_path / 'sentinel-missing-stats')},
        {ECON_BIN: str(tmp_path / 'sentinel-missing-econ')},
        {STATS_BIN: binary, ECON_BIN: other},
        {USAGE: '1'},
    ]
    for case in cases:
        env = {**base, **case}
        with pytest.raises(StatsConfigError) as excinfo:
            StatsConfig.from_env(env)
        message = str(excinfo.value)
        assert 'sentinel' not in message, (case, message)
        assert str(tmp_path) not in message, (case, message)
        for value in base.values():
            assert value not in message, (case, message)


# --- module boundary (design: dependency direction diagnostics -> stats -> proxy)

def test_stats_never_imports_the_proxy_or_streaming_layers():
    tree = ast.parse(Path(stats.__file__).read_text(encoding='utf-8'))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f'{node.module}.{alias.name}' for alias in node.names)
    for forbidden in ('proxy', 'mvibe_proxy.streaming', 'mvibe_proxy.compat'):
        assert forbidden not in imported


# --- UsageObserver (Requirements 1.1, 1.2, 1.4, 1.5, 1.6) ---------------------

MODEL = 'mistral-large-latest'
SSE = 'text/event-stream'
JSON_TYPE = 'application/json'


def _observe(chunks, *, content_type, model=MODEL):
    observer = UsageObserver(content_type=content_type, model=model)
    for chunk in chunks:
        observer.feed(chunk)
    return observer.finish()


def _split(data: bytes, size: int) -> list[bytes]:
    return [data[index:index + size] for index in range(0, len(data), size)]


def _sse_event(payload) -> bytes:
    return b'data: ' + json.dumps(payload).encode() + b'\n\n'


def _delta(text: str) -> dict:
    return {'id': 'c1', 'object': 'chat.completion.chunk', 'model': MODEL,
            'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]}


STREAM = b''.join([
    _sse_event(_delta('Hello')),
    _sse_event(_delta(' world')),
    _sse_event({'id': 'c1', 'object': 'chat.completion.chunk', 'model': MODEL,
                'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 21, 'completion_tokens': 3, 'total_tokens': 24}}),
    b'data: [DONE]\n\n',
])


def test_observable_sse_usage_line():
    usage = _observe([b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n'],
                     content_type=SSE)
    assert usage == Usage(5, 7, MODEL)


@pytest.mark.parametrize('size', (1, 2, 3, 7, 64, len(STREAM)))
def test_sse_usage_split_across_chunks(size):
    assert _observe(_split(STREAM, size), content_type=SSE) == Usage(21, 3, MODEL)


@pytest.mark.parametrize('size', (1, 5, len(STREAM)))
def test_sse_tolerates_crlf_line_endings(size):
    crlf = STREAM.replace(b'\n', b'\r\n')
    assert _observe(_split(crlf, size), content_type=SSE) == Usage(21, 3, MODEL)


@pytest.mark.parametrize('content_type', (
    'text/event-stream; charset=utf-8', 'Text/Event-Stream', ' text/event-stream'))
def test_sse_mode_is_chosen_by_content_type(content_type):
    assert _observe([STREAM], content_type=content_type) == Usage(21, 3, MODEL)


def test_sse_keeps_the_last_usage_seen():
    stream = (_sse_event({'usage': {'prompt_tokens': 1, 'completion_tokens': 1}})
              + _sse_event({'usage': None, 'choices': []})
              + _sse_event({'usage': {'prompt_tokens': 9, 'completion_tokens': 4}})
              + _sse_event({'usage': None}))
    assert _observe([stream], content_type=SSE) == Usage(9, 4, MODEL)


def test_sse_without_usage_yields_none():
    stream = _sse_event(_delta('a')) + _sse_event(_delta('b')) + b'data: [DONE]\n\n'
    assert _observe(_split(stream, 4), content_type=SSE) is None


def test_sse_joins_multi_line_data_blocks():
    stream = (b'data: {"usage":\n'
              b'data: {"prompt_tokens": 11,\n'
              b'data:  "completion_tokens": 2}}\n\n'
              b'data: [DONE]\n\n')
    assert _observe(_split(stream, 3), content_type=SSE) == Usage(11, 2, MODEL)


def test_sse_ignores_comments_and_other_fields():
    stream = (b': keep-alive\n\n'
              b'event: completion\nid: 7\nretry: 100\n'
              b'data: {"usage":{"prompt_tokens":4,"completion_tokens":6}}\n\n'
              b': {"usage":{"prompt_tokens":99,"completion_tokens":99}}\n\n')
    assert _observe([stream], content_type=SSE) == Usage(4, 6, MODEL)


def test_sse_final_event_without_trailing_blank_line_is_read_at_finish():
    stream = _sse_event(_delta('x')) + b'data: {"usage":{"prompt_tokens":2,"completion_tokens":8}}'
    assert _observe(_split(stream, 5), content_type=SSE) == Usage(2, 8, MODEL)


def test_sse_parses_only_lines_that_mention_usage(monkeypatch):
    calls = []

    def counting_loads(data, *args, **kwargs):
        calls.append(len(data))
        return json.loads(data, *args, **kwargs)

    monkeypatch.setattr(stats, 'json', SimpleNamespace(loads=counting_loads))
    # Escaped "usage" inside content is not a key and is never parsed.
    mention = _sse_event(_delta('the "usage" field'))
    stream = mention + b''.join(_sse_event(_delta(str(n))) for n in range(50)) + STREAM
    assert _observe(_split(stream, 13), content_type=SSE) == Usage(21, 3, MODEL)
    assert len(calls) == 1


def test_sse_malformed_usage_event_is_ignored_and_the_last_valid_usage_kept():
    stream = (_sse_event({'usage': {'prompt_tokens': 3, 'completion_tokens': 1}})
              + b'data: {"usage": {"prompt_tokens": 5,\n\n'
              + _sse_event({'usage': {'prompt_tokens': -1, 'completion_tokens': 1}}))
    assert _observe([stream], content_type=SSE) == Usage(3, 1, MODEL)


def test_sse_line_over_the_bound_disables_the_observer():
    oversized = b'data: {"choices": "' + b'x' * UsageObserver.JSON_LIMIT
    usage_event = b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n'
    assert _observe([oversized, b'"}\n\n', usage_event], content_type=SSE) is None


def test_json_body_usage_split_across_chunks():
    body = json.dumps({
        'id': 'r1', 'object': 'chat.completion', 'model': MODEL,
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'hi'},
                     'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 12, 'completion_tokens': 34, 'total_tokens': 46},
    }).encode()
    assert _observe(_split(body, 9), content_type=JSON_TYPE) == Usage(12, 34, MODEL)


@pytest.mark.parametrize('content_type', (None, 'application/json; charset=utf-8', 'text/plain'))
def test_non_sse_content_types_use_json_mode(content_type):
    body = b'{"usage": {"prompt_tokens": 1, "completion_tokens": 2}}'
    assert _observe([body], content_type=content_type) == Usage(1, 2, MODEL)


def test_embeddings_usage_has_zero_completion_tokens():
    body = json.dumps({'object': 'list', 'model': 'mistral-embed',
                       'data': [{'object': 'embedding', 'index': 0, 'embedding': [0.1, 0.2]}],
                       'usage': {'prompt_tokens': 8, 'total_tokens': 8}}).encode()
    assert _observe([body], content_type=JSON_TYPE, model='mistral-embed') == Usage(8, 0, 'mistral-embed')


def test_null_completion_tokens_count_as_zero():
    body = b'{"usage": {"prompt_tokens": 8, "completion_tokens": null}}'
    assert _observe([body], content_type=JSON_TYPE) == Usage(8, 0, MODEL)


def test_model_is_the_request_model_and_may_be_unknown():
    body = b'{"model": "response-model", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}'
    assert _observe([body], content_type=JSON_TYPE, model=None) == Usage(1, 1, None)
    assert _observe([body], content_type=JSON_TYPE, model='request-model').model == 'request-model'


def test_json_body_at_the_bound_is_parsed_and_one_byte_over_yields_none():
    head = b'{"usage": {"prompt_tokens": 1, "completion_tokens": 2}, "pad": "'
    tail = b'"}'
    pad = UsageObserver.JSON_LIMIT - len(head) - len(tail)
    exact = head + b'x' * pad + tail
    assert len(exact) == UsageObserver.JSON_LIMIT
    assert _observe(_split(exact, 1 << 20), content_type=JSON_TYPE) == Usage(1, 2, MODEL)
    over = head + b'x' * (pad + 1) + tail
    assert _observe(_split(over, 1 << 20), content_type=JSON_TYPE) is None


@pytest.mark.parametrize('body', (
    b'{"usage": {"prompt_tokens": 5,',            # truncated
    b'not json at all',
    b'',
    b'\xff\xfe\x00garbage',
    b'[{"usage": {"prompt_tokens": 1, "completion_tokens": 1}}]',  # not an object
    b'{"usage": "5 tokens"}',
    b'{"choices": []}',                           # no usage
    b'{"usage": null}',
))
def test_unparseable_or_usage_free_json_yields_none(body):
    assert _observe([body], content_type=JSON_TYPE) is None


@pytest.mark.parametrize('usage', (
    {'prompt_tokens': '5', 'completion_tokens': 7},
    {'prompt_tokens': 5, 'completion_tokens': '7'},
    {'prompt_tokens': -1, 'completion_tokens': 7},
    {'prompt_tokens': 5, 'completion_tokens': -7},
    {'prompt_tokens': 5.0, 'completion_tokens': 7},
    {'prompt_tokens': 5, 'completion_tokens': 7.5},
    {'prompt_tokens': True, 'completion_tokens': 7},
    {'prompt_tokens': 5, 'completion_tokens': False},
    {'completion_tokens': 7},
    {'prompt_tokens': None, 'completion_tokens': 7},
    {},
))
def test_usage_numbers_must_be_non_negative_ints(usage):
    body = json.dumps({'usage': usage}).encode()
    assert _observe([body], content_type=JSON_TYPE) is None
    assert _observe([_sse_event({'usage': usage})], content_type=SSE) is None


@pytest.mark.parametrize('content_type', (SSE, JSON_TYPE))
def test_a_raising_parser_disables_the_observer_without_raising(content_type, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError('parser exploded')

    monkeypatch.setattr(stats, 'json', SimpleNamespace(loads=boom))
    observer = UsageObserver(content_type=content_type, model=MODEL)
    payload = b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n' \
        if content_type == SSE else b'{"usage":{"prompt_tokens":5,"completion_tokens":7}}'
    observer.feed(payload)
    assert observer.finish() is None
    assert observer.finish() is None


def test_after_an_internal_error_the_observer_stays_disabled(monkeypatch):
    real_loads = json.loads
    state = {'fail': True}

    def flaky(*args, **kwargs):
        if state['fail']:
            raise RuntimeError('once')
        return real_loads(*args, **kwargs)

    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return flaky(*args, **kwargs)

    monkeypatch.setattr(stats, 'json', SimpleNamespace(loads=counted))
    observer = UsageObserver(content_type=SSE, model=MODEL)
    observer.feed(b'data: {"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n')
    state['fail'] = False
    observer.feed(b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n')
    assert observer.finish() is None
    assert len(calls) == 1  # a disabled observer does no further work


def _retained_bytes(observer: UsageObserver) -> int:
    total = 0
    for value in vars(observer).values():
        if isinstance(value, (bytes, bytearray)):
            total += len(value)
        elif isinstance(value, list):
            total += sum(len(item) for item in value if isinstance(item, (bytes, bytearray)))
    return total


_QUARTER_MIB = b'x' * (1 << 18)


@pytest.mark.parametrize(('content_type', 'first', 'piece'), [
    (SSE, b'data: {"choices": "', _QUARTER_MIB),          # one partial line that never ends
    (SSE, b'', b'data: ' + _QUARTER_MIB + b'\n'),          # one event that never closes
    (JSON_TYPE, b'{"pad": "', _QUARTER_MIB),
])
def test_retained_bytes_stay_within_the_bound(content_type, first, piece):
    # Requirement 1.4: a response beyond the bound only yields no usage record.
    observer = UsageObserver(content_type=content_type, model=MODEL)
    observer.feed(first)
    peak = 0
    for _ in range(3 * UsageObserver.JSON_LIMIT // len(piece) + 1):
        observer.feed(piece)
        peak = max(peak, _retained_bytes(observer))
    assert peak <= UsageObserver.JSON_LIMIT
    assert observer.finish() is None


@pytest.mark.parametrize('content_type', (SSE, JSON_TYPE))
@pytest.mark.parametrize('chunk', ('a str chunk', None, 42, object()))
def test_feed_never_raises_on_unexpected_input(content_type, chunk):
    observer = UsageObserver(content_type=content_type, model=MODEL)
    observer.feed(chunk)
    observer.feed(b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n')
    assert observer.finish() is None


@pytest.mark.parametrize('content_type', (SSE, JSON_TYPE))
def test_feed_never_mutates_its_input(content_type):
    data = bytearray(b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\r\n\r\n')
    original = bytes(data)
    observer = UsageObserver(content_type=content_type, model=MODEL)
    observer.feed(data)
    observer.finish()
    assert bytes(data) == original


def test_json_body_is_parsed_once_and_finish_is_idempotent(monkeypatch):
    calls = []

    def counting_loads(data, *args, **kwargs):
        calls.append(1)
        return json.loads(data, *args, **kwargs)

    monkeypatch.setattr(stats, 'json', SimpleNamespace(loads=counting_loads))
    observer = UsageObserver(content_type=JSON_TYPE, model=MODEL)
    for chunk in _split(b'{"usage": {"prompt_tokens": 3, "completion_tokens": 4}}', 4):
        observer.feed(chunk)
    assert observer.finish() == Usage(3, 4, MODEL)
    assert observer.finish() == Usage(3, 4, MODEL)
    assert len(calls) == 1


def test_json_body_without_usage_key_is_not_parsed(monkeypatch):
    calls = []
    monkeypatch.setattr(stats, 'json', SimpleNamespace(loads=lambda *a, **k: calls.append(1)))
    assert _observe([b'{"object": "list", "data": []}'], content_type=JSON_TYPE) is None
    assert calls == []


@pytest.mark.parametrize('content_type', (SSE, JSON_TYPE))
def test_observer_retains_only_numbers_and_the_model(content_type):
    secret = 'SECRET-PROMPT-CONTENT'
    if content_type == SSE:
        body = (_sse_event(_delta(secret))
                + _sse_event({'choices': [{'delta': {'content': secret}}],
                              'usage': {'prompt_tokens': 5, 'completion_tokens': 7}}))
    else:
        body = json.dumps({'choices': [{'message': {'content': secret}}],
                           'usage': {'prompt_tokens': 5, 'completion_tokens': 7}}).encode()
    observer = UsageObserver(content_type=content_type, model=MODEL)
    for chunk in _split(body, 11):
        observer.feed(chunk)
    usage = observer.finish()
    assert usage == Usage(5, 7, MODEL)
    assert [f.name for f in dataclasses.fields(Usage)] == ['prompt_tokens', 'completion_tokens', 'model']
    assert secret not in repr(vars(observer))
    assert secret.encode() not in b''.join(
        bytes(value) for value in vars(observer).values() if isinstance(value, (bytes, bytearray)))


def test_usage_is_a_frozen_value():
    usage = Usage(1, 2, MODEL)
    with pytest.raises(dataclasses.FrozenInstanceError):
        usage.prompt_tokens = 3


# --- as_callable (Requirements 1.1, 1.7) -------------------------------------

def test_as_callable_stores_usage_on_the_diagnostic_at_the_terminal_none():
    diagnostic = RequestDiagnostic()
    observe = UsageObserver(content_type=SSE, model=MODEL).as_callable(diagnostic)
    for chunk in _split(STREAM, 6):
        observe(chunk)
    assert diagnostic.usage is None  # nothing is stored before completion
    observe(None)
    assert diagnostic.usage == (21, 3)


def test_as_callable_leaves_the_diagnostic_untouched_without_usage():
    diagnostic = RequestDiagnostic()
    observe = UsageObserver(content_type=JSON_TYPE, model=MODEL).as_callable(diagnostic)
    observe(b'{"choices": []}')
    observe(None)
    assert diagnostic.usage is None


def test_as_callable_never_raises(monkeypatch):
    class Frozen:
        def __setattr__(self, name, value):
            raise AttributeError('read-only diagnostic')

    observe = UsageObserver(content_type=JSON_TYPE, model=MODEL).as_callable(Frozen())
    observe(b'{"usage": {"prompt_tokens": 1, "completion_tokens": 2}}')
    observe(None)
    observe('not bytes')


def test_as_callable_feeds_the_emitted_diagnostic_fields(capsys):
    diagnostics.configure_stdout()
    try:
        diagnostic = RequestDiagnostic()
        observe = UsageObserver(content_type=SSE, model=MODEL).as_callable(diagnostic)
        observe(b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n')
        observe(None)
        diagnostic.emit()
        row = json.loads(capsys.readouterr().out.strip())
        assert (row['usage_prompt_tokens'], row['usage_completion_tokens']) == (5, 7)
    finally:
        diagnostics.disable_stdout()


# --- UsageLedger (Requirement 3.1) -------------------------------------------

def test_ledger_totals_and_by_model():
    ledger = UsageLedger()
    assert ledger.totals() == (0, 0)
    assert dict(ledger.by_model()) == {}
    assert ledger.revision == 0
    ledger.add(Usage(5, 7, 'a'))
    ledger.add(Usage(3, 0, 'b'))
    ledger.add(Usage(1, 1, None))
    ledger.add(Usage(2, 2, 'a'))
    assert ledger.totals() == (11, 10)
    assert dict(ledger.by_model()) == {'a': (7, 9), 'b': (3, 0), 'unknown': (1, 1)}
    assert ledger.revision == 4


def test_ledger_revision_increments_on_every_add():
    ledger = UsageLedger()
    revisions = []
    for _ in range(3):
        ledger.add(Usage(0, 0, MODEL))
        revisions.append(ledger.revision)
    assert revisions == [1, 2, 3]


def test_ledger_by_model_is_a_snapshot():
    ledger = UsageLedger()
    ledger.add(Usage(1, 1, 'a'))
    view = ledger.by_model()
    with pytest.raises(TypeError):
        view['b'] = (9, 9)
    ledger.add(Usage(1, 1, 'a'))
    assert dict(view) == {'a': (1, 1)}
    assert dict(ledger.by_model()) == {'a': (2, 2)}


# --- ProxyCounters (Requirements 1.7, 3.1, 3.4) ------------------------------

TOP_KEYS = {'started_at', 'requests', 'usage', 'econ', 'rate_limit'}
REQUEST_KEYS = {'total', 'by_outcome', 'by_status'}
USAGE_KEYS = {'prompt_tokens', 'completion_tokens', 'by_model', 'last_report_at', 'report_failures'}
MODEL_KEYS = {'prompt_tokens', 'completion_tokens', 'requests'}
ECON_KEYS = {'requests_with_econ', 'eligible', 'replaced', 'bypassed', 'pending', 'est_saved_tokens'}
RATE_LIMIT_KEYS = {'held_requests', 'queued_ms_total', 'rejections', 'exhausted'}
OUTCOMES = {'completed', 'auth_failed', 'connection_failed', 'unsupported_endpoint', 'invalid_request',
            'stream_interrupted', 'cancelled', 'cleanup_failed', 'rate_limited', 'upstream_error'}


def _diagnostic(*, status=200, model=MODEL, usage=None, econ=None, record=None, upstream=None,
                failure=None):
    diagnostic = RequestDiagnostic()
    diagnostic.model = model
    diagnostic.status = status
    diagnostic.usage = usage
    diagnostic.econ = econ
    diagnostic.rate_limit = record
    if upstream is not None:
        diagnostic.upstream(upstream)
    if failure is not None:
        diagnostic.failure(failure)
    return diagnostic


def test_observable_two_chat_usages_for_one_model_sum():
    counters = ProxyCounters(clock=Clock().time)
    counters.observe(_diagnostic(usage=(100, 20)))
    counters.observe(_diagnostic(usage=(50, 5)))
    by_model = counters.snapshot()['usage']['by_model']
    assert by_model[MODEL]['prompt_tokens'] == 150
    assert by_model[MODEL]['completion_tokens'] == 25
    assert by_model[MODEL]['requests'] == 2


def test_fresh_counters_snapshot():
    counters = ProxyCounters(clock=Clock().time)
    assert counters.snapshot() == {
        'started_at': '2023-11-14T22:13:20+00:00',
        'requests': {'total': 0, 'by_outcome': {}, 'by_status': {}},
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'by_model': {},
                  'last_report_at': None, 'report_failures': 0},
        'econ': {'requests_with_econ': 0, 'eligible': 0, 'replaced': 0, 'bypassed': 0,
                 'pending': 0, 'est_saved_tokens': 0},
        'rate_limit': {'held_requests': 0, 'queued_ms_total': 0, 'rejections': 0, 'exhausted': 0},
    }


def test_sink_aggregates_a_sequence_of_diagnostics():
    clock = Clock()
    counters = ProxyCounters(clock=clock.time)
    clock.advance(3600)  # started_at is taken at construction
    sequence = [
        # A completed chat request with usage and applied economics.
        _diagnostic(usage=(100, 20), econ=EconCounters(
            eligible=3, replaced=2, bypassed=1, pending=0, in_bytes=9000, out_bytes=1000,
            est_saved_tokens=2000), record=GateRecord(queued_ms=0.0, rejections=0)),
        # An upstream 429 the gate passed on (one rejection, then a final 429).
        _diagnostic(status=429, usage=None, upstream=429,
                    record=GateRecord(queued_ms=250.25, rejections=1)),
        # A 429 the gate answered itself after exhausting its budget.
        _diagnostic(status=429, upstream=429,
                    record=GateRecord(queued_ms=1500.5, rejections=3, exhausted=True,
                                      headers={'x-ratelimit-remaining': 'SECRET-HEADER'})),
        # The proxy's economics error path records an allowlisted mapping.
        _diagnostic(usage=(10, 0), model='mistral-embed', econ={'econ_outcome': 'error'}),
        _diagnostic(usage=(7, 3), model=None, econ={
            'econ_outcome': 'applied', 'econ_eligible': 1, 'econ_replaced': 1, 'econ_bypassed': 0,
            'econ_pending': 1, 'econ_est_saved_tokens': -4}),
        # A held request that eventually completed, and a cancelled request
        # that never saw a response start.
        _diagnostic(usage=(1, 1), record=GateRecord(queued_ms=2000.0, rejections=0)),
        _diagnostic(status=None, usage=None, failure='cancelled'),
        _diagnostic(status=502, upstream=502),
    ]
    for diagnostic in sequence:
        counters.observe(diagnostic)
    snapshot = counters.snapshot()
    assert snapshot == {
        'started_at': '2023-11-14T22:13:20+00:00',
        'requests': {
            'total': 8,
            'by_outcome': {'cancelled': 1, 'completed': 4, 'rate_limited': 1, 'upstream_error': 2},
            'by_status': {'200': 4, '429': 2, '502': 1},
        },
        'usage': {
            'prompt_tokens': 118, 'completion_tokens': 24,
            'by_model': {
                MODEL: {'prompt_tokens': 101, 'completion_tokens': 21, 'requests': 2},
                'mistral-embed': {'prompt_tokens': 10, 'completion_tokens': 0, 'requests': 1},
                'unknown': {'prompt_tokens': 7, 'completion_tokens': 3, 'requests': 1},
            },
            'last_report_at': None, 'report_failures': 0,
        },
        'econ': {'requests_with_econ': 3, 'eligible': 4, 'replaced': 3, 'bypassed': 1,
                 'pending': 1, 'est_saved_tokens': 1996},
        'rate_limit': {'held_requests': 3, 'queued_ms_total': 3750.75, 'rejections': 4,
                       'exhausted': 1},
    }
    assert counters.usage.totals() == (118, 24)
    assert counters.usage.revision == 4
    rendered = json.dumps(snapshot)
    for diagnostic in sequence:
        assert diagnostic.request_id not in rendered
        assert diagnostic.hint not in rendered
    assert 'SECRET-HEADER' not in rendered and 'x-ratelimit' not in rendered


@pytest.mark.parametrize(('record', 'held'), [
    (GateRecord(queued_ms=0.0, rejections=0), 0),
    (GateRecord(queued_ms=0.0, rejections=1), 1),
    (GateRecord(queued_ms=0.01, rejections=0), 1),
    (GateRecord(queued_ms=0.0, rejections=0, exhausted=True), 0),
])
def test_held_requests_count_any_queueing_or_rejection(record, held):
    counters = ProxyCounters(clock=Clock().time)
    counters.observe(_diagnostic(record=record))
    rate_limit = counters.snapshot()['rate_limit']
    assert rate_limit['held_requests'] == held
    assert rate_limit['exhausted'] == int(record.exhausted)


def test_snapshot_is_json_safe_with_only_documented_keys_and_model_ids():
    counters = ProxyCounters(clock=Clock().time)
    for diagnostic in (
        _diagnostic(usage=(5, 7)),
        _diagnostic(usage=(8, 0), model='mistral-embed'),
        _diagnostic(usage=(1, 2), model=None),
        _diagnostic(status=429, upstream=429, record=GateRecord(queued_ms=1.5, rejections=2)),
        _diagnostic(econ={'econ_outcome': 'error'}),
        _diagnostic(status=None, failure='interrupted'),
    ):
        counters.observe(diagnostic)
    counters.report_succeeded(Clock().time() + 60)
    counters.report_failed()
    snapshot = counters.snapshot()
    rendered = json.dumps(snapshot, allow_nan=False)
    assert json.loads(rendered) == snapshot
    assert set(snapshot) == TOP_KEYS
    assert set(snapshot['requests']) == REQUEST_KEYS
    assert set(snapshot['requests']['by_outcome']) <= OUTCOMES
    assert all(key.isdigit() for key in snapshot['requests']['by_status'])
    assert set(snapshot['usage']) == USAGE_KEYS
    assert set(snapshot['usage']['by_model']) == {MODEL, 'mistral-embed', 'unknown'}
    assert all(set(entry) == MODEL_KEYS for entry in snapshot['usage']['by_model'].values())
    assert set(snapshot['econ']) == ECON_KEYS
    assert set(snapshot['rate_limit']) == RATE_LIMIT_KEYS
    assert snapshot['usage']['last_report_at'] == '2023-11-14T22:14:20+00:00'
    assert snapshot['usage']['report_failures'] == 1


def test_report_bookkeeping_is_exposed_through_two_methods():
    counters = ProxyCounters(clock=Clock().time)
    assert counters.last_report_at is None and counters.report_failures == 0
    counters.report_failed()
    counters.report_failed()
    counters.report_succeeded(1_700_000_123.5)
    snapshot = counters.snapshot()['usage']
    assert snapshot['report_failures'] == 2
    assert snapshot['last_report_at'] == '2023-11-14T22:15:23.500000+00:00'
    assert counters.last_report_at == 1_700_000_123.5


def test_snapshot_is_a_copy():
    counters = ProxyCounters(clock=Clock().time)
    counters.observe(_diagnostic(usage=(1, 1)))
    snapshot = counters.snapshot()
    snapshot['requests']['by_outcome']['completed'] = 99
    snapshot['usage']['by_model'][MODEL]['prompt_tokens'] = 99
    again = counters.snapshot()
    assert again['requests']['by_outcome']['completed'] == 1
    assert again['usage']['by_model'][MODEL]['prompt_tokens'] == 1


def test_counters_ignore_values_that_are_not_counts():
    counters = ProxyCounters(clock=Clock().time)
    counters.observe(_diagnostic(status=True, usage=(-1, 2), econ={
        'econ_eligible': True, 'econ_replaced': '2', 'econ_bypassed': 1.5, 'econ_pending': None,
        'econ_est_saved_tokens': 'many'}, record=GateRecord(queued_ms=float('nan'), rejections=-1)))
    counters.observe(_diagnostic(usage=('5', 7)))
    counters.observe(_diagnostic(usage=(True, 1)))
    snapshot = counters.snapshot()
    json.dumps(snapshot, allow_nan=False)
    assert snapshot['requests']['total'] == 3
    assert snapshot['requests']['by_status'] == {'200': 2}
    assert snapshot['usage']['prompt_tokens'] == 0 and snapshot['usage']['by_model'] == {}
    assert snapshot['econ'] == {'requests_with_econ': 1, 'eligible': 0, 'replaced': 0, 'bypassed': 0,
                                'pending': 0, 'est_saved_tokens': 0}
    assert snapshot['rate_limit'] == {'held_requests': 0, 'queued_ms_total': 0, 'rejections': 0,
                                      'exhausted': 0}


def test_counters_are_fed_by_the_diagnostics_sink_on_emit():
    counters = ProxyCounters(clock=Clock().time)
    diagnostics.set_counters_sink(counters.observe)
    try:
        diagnostic = _diagnostic(usage=(5, 7))
        diagnostic.emit()
        _diagnostic(status=401, upstream=401).emit()
    finally:
        diagnostics.set_counters_sink(None)
    _diagnostic(usage=(1000, 1000)).emit()  # after clearing: not counted
    snapshot = counters.snapshot()
    assert snapshot['requests']['total'] == 2
    assert snapshot['requests']['by_outcome'] == {'completed': 1, 'upstream_error': 1}
    assert snapshot['usage']['by_model'] == {
        MODEL: {'prompt_tokens': 5, 'completion_tokens': 7, 'requests': 1}}


def test_counters_default_clock_is_wall_time():
    started = datetime.fromisoformat(ProxyCounters().snapshot()['started_at'])
    assert started.tzinfo is not None and started.utcoffset() == timedelta(0)
    assert abs((datetime.now(timezone.utc) - started).total_seconds()) < 60


# --- UsageReporter (task 2.4, Requirements 2.1, 2.2, 2.3, 2.4) ---------------

SMALL = 'mistral-small-latest'
REPORT_TOOL = 'session_resume_or_handoff'
FAILURE_WARNING = 'Stats report to mmhmem failed (McpError); will retry next interval'
FINAL_WARNING = ('Final stats report to mmhmem failed ({}); '
                 'usage since the last report was not recorded')
# mmhmem may answer a resume with checkpoint text: it must never be logged.
RESULT_SENTINEL = 'CHECKPOINT-TEXT-SENTINEL'
TASK_ID = re.compile(r'mvibe-proxy-\d{8}T\d{6}Z-[0-9a-f]{8}')


async def _until(predicate, timeout_s: float = 2.0) -> None:
    async def poll():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(poll(), timeout_s)


class RecordingClient:
    """A ToolCaller double recording (name, arguments, timeout_ms) per call.

    `failures` maps a call index to the exception that call raises; `hang`
    parks every call until it is cancelled; `during` runs inside each call.
    """

    def __init__(self, *, failures=None, hang=False, during=None):
        self.calls = []
        self.failures = dict(failures or {})
        self.hang = hang
        self.during = during
        self.cancelled = 0

    async def call_tool(self, name, arguments, *, timeout_ms):
        index = len(self.calls)
        self.calls.append((name, json.loads(json.dumps(arguments)), timeout_ms))
        await asyncio.sleep(0)
        if self.during is not None:
            self.during(index)
        if self.hang:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        error = self.failures.get(index)
        if error is not None:
            raise error
        return {'content': [{'type': 'text', 'text': RESULT_SENTINEL}]}

    def usages(self):
        return [arguments['client_usage'] for _, arguments, _ in self.calls]


class Ticks:
    """An injected sleep the test releases one reporter tick at a time."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.intervals = []
        self.parked = False
        self.cancelled = 0
        self._release = asyncio.Queue()

    async def sleep(self, seconds):
        self.intervals.append(seconds)
        self.parked = True
        try:
            await self._release.get()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.parked = False
        self.clock.advance(seconds)

    async def tick(self):
        """Release the parked sleep and wait until the loop parks again."""
        await _until(lambda: self.parked)
        count = len(self.intervals)
        self._release.put_nowait(None)
        await _until(lambda: len(self.intervals) > count and self.parked)


def _reporter(client, ledger, **kwargs):
    clock = kwargs.pop('clock', None) or Clock()
    warnings = kwargs.pop('warnings', None)
    options = {'project': 'mvibe', 'interval_s': 60, 'clock': clock.time}
    if warnings is not None:
        options['warn'] = warnings.append
    options.update(kwargs)
    return stats.UsageReporter(client, ledger, **options)


def _ledger(*usages):
    ledger = UsageLedger()
    for usage in usages:
        ledger.add(usage)
    return ledger


def _expected_call(task_id, model, prompt, completion, *, project='mvibe', timeout_ms=10_000):
    return (REPORT_TOOL, {
        'project': project,
        'agent_harness': 'mvibe-proxy',
        'workspace_task_id': task_id,
        'client_usage': {'prompt_tokens': prompt, 'completion_tokens': completion,
                         'source': f'mvibe-proxy {model}'},
    }, timeout_ms)


@pytest.mark.anyio
async def test_observable_successful_tick_sets_last_report_at_and_the_model_source():
    clock = Clock()
    ticks = Ticks(clock)
    client = RecordingClient()
    reporter = _reporter(client, _ledger(Usage(120, 30, MODEL)), clock=clock, sleep=ticks.sleep)
    assert reporter.last_report_at is None
    await reporter.start()
    try:
        await ticks.tick()
        assert ticks.intervals[0] == 60
        assert reporter.last_report_at == clock.time() == 1_700_000_060
        assert client.calls[0][1]['client_usage']['source'] == 'mvibe-proxy mistral-large-latest'
        assert reporter.failures == 0
    finally:
        await reporter.stop()


@pytest.mark.anyio
async def test_one_call_per_model_with_cumulative_totals_and_one_task_id():
    clock = Clock()
    ticks = Ticks(clock)
    client = RecordingClient()
    ledger = _ledger(Usage(100, 20, SMALL), Usage(5, 7, MODEL), Usage(1, 1, SMALL))
    reporter = _reporter(client, ledger, clock=clock, sleep=ticks.sleep, project='proj-x',
                         timeout_ms=2500)
    await reporter.start()
    try:
        await ticks.tick()
    finally:
        await reporter.stop()
    task_id = reporter.task_id
    # Sources are sorted, so large comes before small.
    assert client.calls == [
        _expected_call(task_id, MODEL, 5, 7, project='proj-x', timeout_ms=2500),
        _expected_call(task_id, SMALL, 101, 21, project='proj-x', timeout_ms=2500),
    ]


def test_default_task_id_is_per_process_utc_start_plus_random_suffix():
    clock = Clock()  # 1_700_000_000 is 2023-11-14T22:13:20Z
    first = _reporter(RecordingClient(), UsageLedger(), clock=clock)
    second = _reporter(RecordingClient(), UsageLedger(), clock=clock)
    assert TASK_ID.fullmatch(first.task_id)
    assert first.task_id.startswith('mvibe-proxy-20231114T221320Z-')
    assert first.task_id != second.task_id
    clock.advance(3600)
    assert first.task_id.startswith('mvibe-proxy-20231114T221320Z-')  # computed once
    assert _reporter(RecordingClient(), UsageLedger(), task_id='given').task_id == 'given'


@pytest.mark.anyio
async def test_an_explicit_task_id_is_used_in_every_call():
    client = RecordingClient()
    reporter = _reporter(client, _ledger(Usage(1, 2, MODEL), Usage(3, 4, SMALL)), task_id='t-1')
    assert await reporter.report() is True
    assert [arguments['workspace_task_id'] for _, arguments, _ in client.calls] == ['t-1', 't-1']


@pytest.mark.anyio
async def test_an_unchanged_ledger_makes_no_call_on_a_tick():
    clock = Clock()
    ticks = Ticks(clock)
    client = RecordingClient()
    ledger = UsageLedger()
    reporter = _reporter(client, ledger, clock=clock, sleep=ticks.sleep)
    await reporter.start()
    try:
        await ticks.tick()  # an empty ledger has nothing to report
        assert client.calls == [] and reporter.last_report_at is None
        ledger.add(Usage(10, 2, MODEL))
        await ticks.tick()
        assert len(client.calls) == 1
        reported_at = reporter.last_report_at
        await ticks.tick()  # unchanged since the last successful report
        await ticks.tick()
        assert len(client.calls) == 1
        assert reporter.last_report_at == reported_at
    finally:
        await reporter.stop()
    assert len(client.calls) == 1  # nothing new to flush either


@pytest.mark.anyio
async def test_a_later_tick_reports_new_cumulative_totals_not_deltas():
    clock = Clock()
    ticks = Ticks(clock)
    client = RecordingClient()
    ledger = _ledger(Usage(100, 20, MODEL))
    reporter = _reporter(client, ledger, clock=clock, sleep=ticks.sleep)
    await reporter.start()
    try:
        await ticks.tick()
        ledger.add(Usage(50, 5, MODEL))
        ledger.add(Usage(8, 0, SMALL))
        await ticks.tick()
    finally:
        await reporter.stop()
    assert [(u['source'], u['prompt_tokens'], u['completion_tokens']) for u in client.usages()] == [
        ('mvibe-proxy mistral-large-latest', 100, 20),
        ('mvibe-proxy mistral-large-latest', 150, 25),
        ('mvibe-proxy mistral-small-latest', 8, 0),
    ]
    assert len({arguments['workspace_task_id'] for _, arguments, _ in client.calls}) == 1


@pytest.mark.anyio
async def test_unknown_model_and_zero_totals():
    client = RecordingClient()
    ledger = _ledger(Usage(0, 0, 'mistral-embed'), Usage(4, 1, None))
    reporter = _reporter(client, ledger)
    assert await reporter.report() is True
    # A model whose cumulative totals are both zero has nothing to report.
    assert [u['source'] for u in client.usages()] == ['mvibe-proxy unknown']


@pytest.mark.anyio
async def test_report_with_nothing_to_send_makes_no_call_and_sets_no_report_time():
    client = RecordingClient()
    reporter = _reporter(client, _ledger(Usage(0, 0, MODEL)))
    assert await reporter.report() is True
    assert client.calls == [] and reporter.last_report_at is None


@pytest.mark.anyio
async def test_a_failure_counts_warns_once_and_skips_the_remaining_sources():
    clock = Clock()
    ticks = Ticks(clock)
    counters = ProxyCounters(clock=clock.time)
    warnings = []
    client = RecordingClient(failures={0: McpError(
        'timeout: no session_resume_or_handoff response within 10000 ms')})
    ledger = _ledger(Usage(5, 7, MODEL), Usage(1, 1, SMALL))
    reporter = _reporter(client, ledger, clock=clock, sleep=ticks.sleep, counters=counters,
                         warnings=warnings)
    await reporter.start()
    try:
        await ticks.tick()
        assert len(client.calls) == 1  # the second source was skipped
        assert reporter.failures == 1
        assert warnings == [FAILURE_WARNING]
        assert counters.report_failures == 1
        assert reporter.last_report_at is None and counters.last_report_at is None
        # The unchanged ledger is retried on the next tick, and success resets the streak.
        await ticks.tick()
        assert [u['source'] for u in client.usages()] == [
            'mvibe-proxy mistral-large-latest', 'mvibe-proxy mistral-large-latest',
            'mvibe-proxy mistral-small-latest']
        assert reporter.failures == 0
        assert reporter.last_report_at == clock.time() == 1_700_000_120
        assert counters.last_report_at == reporter.last_report_at
        assert counters.report_failures == 1
        assert warnings == [FAILURE_WARNING]
    finally:
        await reporter.stop()


@pytest.mark.anyio
async def test_one_warning_per_failure_streak():
    clock = Clock()
    ticks = Ticks(clock)
    counters = ProxyCounters(clock=clock.time)
    warnings = []
    error = McpError('terminated: the mmhmem stdio child is not running')
    client = RecordingClient(failures={0: error, 1: error, 3: error})
    ledger = _ledger(Usage(5, 7, MODEL))
    reporter = _reporter(client, ledger, clock=clock, sleep=ticks.sleep, counters=counters,
                         warnings=warnings)
    await reporter.start()
    try:
        await ticks.tick()
        await ticks.tick()
        assert reporter.failures == 2 and counters.report_failures == 2
        assert warnings == [FAILURE_WARNING]
        await ticks.tick()  # call 2 succeeds
        assert reporter.failures == 0 and reporter.last_report_at is not None
        ledger.add(Usage(1, 1, MODEL))
        await ticks.tick()  # call 3 fails: a new streak warns again
        assert reporter.failures == 1 and counters.report_failures == 3
        assert warnings == [FAILURE_WARNING, FAILURE_WARNING]
    finally:
        await reporter.stop()


@pytest.mark.anyio
@pytest.mark.parametrize(('error', 'name'), [
    (McpError('tool: SECRET-TOOL-TEXT'), 'McpError'),
    (asyncio.TimeoutError(), 'TimeoutError'),
    (RuntimeError('SECRET-RUNTIME-TEXT'), 'RuntimeError'),
    (BrokenPipeError(32, 'SECRET-PIPE-TEXT'), 'BrokenPipeError'),
])
async def test_any_exception_is_a_counted_failure_named_by_class_only(error, name):
    warnings = []
    counters = ProxyCounters(clock=Clock().time)
    reporter = _reporter(RecordingClient(failures={0: error}), _ledger(Usage(5, 7, MODEL)),
                         counters=counters, warnings=warnings)
    assert await reporter.report() is False
    assert reporter.failures == 1 and counters.report_failures == 1
    assert warnings == [f'Stats report to mmhmem failed ({name}); will retry next interval']
    assert 'SECRET' not in warnings[0]


@pytest.mark.anyio
async def test_report_returns_true_only_when_every_source_succeeded():
    client = RecordingClient(failures={1: McpError('timeout: x')})
    reporter = _reporter(client, _ledger(Usage(5, 7, MODEL), Usage(1, 1, SMALL)))
    assert await reporter.report() is False
    assert reporter.last_report_at is None
    assert await reporter.report() is True
    assert reporter.last_report_at == 1_700_000_000


@pytest.mark.anyio
async def test_usage_added_during_a_report_is_reported_on_the_next_tick():
    clock = Clock()
    ticks = Ticks(clock)
    ledger = _ledger(Usage(5, 7, MODEL))
    client = RecordingClient(during=lambda index: index == 0 and ledger.add(Usage(1, 1, MODEL)))
    reporter = _reporter(client, ledger, clock=clock, sleep=ticks.sleep)
    await reporter.start()
    try:
        await ticks.tick()
        assert [u['prompt_tokens'] for u in client.usages()] == [5]
        await ticks.tick()  # the revision moved during the first report
        assert [u['prompt_tokens'] for u in client.usages()] == [5, 6]
        await ticks.tick()
        assert len(client.calls) == 2
    finally:
        await reporter.stop()


@pytest.mark.anyio
async def test_a_raising_warn_never_escapes_a_report():
    def warn(message):
        raise RuntimeError('warn sink failed')
    reporter = _reporter(RecordingClient(failures={0: McpError('timeout: x')}),
                         _ledger(Usage(5, 7, MODEL)), warn=warn)
    assert await reporter.report() is False
    assert reporter.failures == 1


@pytest.mark.anyio
async def test_the_default_warn_logs_one_warning_record(caplog):
    caplog.set_level(logging.WARNING)
    reporter = _reporter(RecordingClient(failures={0: McpError('timeout: x'), 1: McpError('x')}),
                         _ledger(Usage(5, 7, MODEL)))
    await reporter.report()
    await reporter.report()
    messages = [record.getMessage() for record in caplog.records
                if record.levelno == logging.WARNING]
    assert messages == [FAILURE_WARNING]


@pytest.mark.anyio
async def test_the_call_result_is_never_logged(capsys, caplog):
    caplog.set_level(logging.DEBUG)
    clock = Clock()
    ticks = Ticks(clock)
    warnings = []
    ledger = _ledger(Usage(5, 7, MODEL))
    client = RecordingClient(failures={1: McpError('timeout: x')})
    reporter = _reporter(client, ledger, clock=clock, sleep=ticks.sleep, warnings=warnings)
    await reporter.start()
    try:
        await ticks.tick()
        assert warnings == []  # warn is only for failures
        ledger.add(Usage(1, 1, MODEL))
        await ticks.tick()
        assert warnings == [FAILURE_WARNING]
    finally:
        await reporter.stop()
    captured = capsys.readouterr()
    rendered = captured.out + captured.err + caplog.text + ''.join(warnings)
    assert RESULT_SENTINEL not in rendered


@pytest.mark.anyio
async def test_start_is_idempotent():
    clock = Clock()
    ticks = Ticks(clock)
    client = RecordingClient()
    reporter = _reporter(client, _ledger(Usage(5, 7, MODEL)), clock=clock, sleep=ticks.sleep)
    await reporter.start()
    await reporter.start()
    try:
        await _until(lambda: ticks.parked)
        for _ in range(5):
            await asyncio.sleep(0)
        assert ticks.intervals == [60]  # one loop only
        await ticks.tick()
        assert len(client.calls) == 1
    finally:
        await reporter.stop()


@pytest.mark.anyio
async def test_stop_cancels_the_loop_and_flushes_once():
    clock = Clock()
    ticks = Ticks(clock)
    counters = ProxyCounters(clock=clock.time)
    client = RecordingClient()
    ledger = _ledger(Usage(5, 7, MODEL))
    reporter = _reporter(client, ledger, clock=clock, sleep=ticks.sleep, counters=counters)
    await reporter.start()
    await _until(lambda: ticks.parked)
    await asyncio.wait_for(reporter.stop(), 5)
    assert ticks.cancelled == 1 and not ticks.parked
    assert client.calls == [_expected_call(reporter.task_id, MODEL, 5, 7)]
    assert reporter.last_report_at == counters.last_report_at == 1_700_000_000
    # Idempotent, and a stopped reporter never restarts or reports again.
    ledger.add(Usage(1, 1, MODEL))
    await reporter.stop()
    await reporter.start()
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(client.calls) == 1 and ticks.intervals == [60]


@pytest.mark.anyio
async def test_stop_without_start_flushes_and_stop_without_changes_does_not():
    client = RecordingClient()
    reporter = _reporter(client, _ledger(Usage(5, 7, MODEL)))
    await reporter.stop()
    assert len(client.calls) == 1
    idle_client = RecordingClient()
    idle = _reporter(idle_client, UsageLedger())
    await idle.stop()
    assert idle_client.calls == []


@pytest.mark.anyio
async def test_stop_after_a_successful_tick_has_nothing_to_flush():
    clock = Clock()
    ticks = Ticks(clock)
    client = RecordingClient()
    reporter = _reporter(client, _ledger(Usage(5, 7, MODEL)), clock=clock, sleep=ticks.sleep)
    await reporter.start()
    await ticks.tick()
    await reporter.stop()
    assert len(client.calls) == 1


@pytest.mark.anyio
async def test_stop_retries_a_failed_tick_in_the_final_flush():
    clock = Clock()
    ticks = Ticks(clock)
    client = RecordingClient(failures={0: McpError('timeout: x')})
    reporter = _reporter(client, _ledger(Usage(5, 7, MODEL)), clock=clock, sleep=ticks.sleep,
                         warnings=[])
    await reporter.start()
    await ticks.tick()
    assert reporter.failures == 1
    await reporter.stop()
    assert len(client.calls) == 2 and reporter.failures == 0


def test_the_final_report_bound_is_two_seconds():
    assert stats.UsageReporter.FINAL_REPORT_TIMEOUT_S == 2.0


@pytest.mark.anyio
async def test_a_hanging_final_report_is_bounded_and_counted(monkeypatch):
    monkeypatch.setattr(stats.UsageReporter, 'FINAL_REPORT_TIMEOUT_S', 0.05)
    counters = ProxyCounters(clock=Clock().time)
    warnings = []
    client = RecordingClient(hang=True)
    reporter = _reporter(client, _ledger(Usage(5, 7, MODEL)), counters=counters,
                         warnings=warnings)
    started = time.monotonic()
    await asyncio.wait_for(reporter.stop(), 5)
    assert time.monotonic() - started < 1.0
    assert client.cancelled == 1
    assert reporter.failures == 1 and counters.report_failures == 1
    assert reporter.last_report_at is None
    assert warnings == [FINAL_WARNING.format('TimeoutError')]


@pytest.mark.anyio
async def test_a_failing_final_report_never_raises_from_stop():
    warnings = []
    reporter = _reporter(RecordingClient(failures={0: RuntimeError('boom')}),
                         _ledger(Usage(5, 7, MODEL)), warnings=warnings)
    await reporter.stop()
    assert reporter.failures == 1
    assert warnings == [FINAL_WARNING.format('RuntimeError')]


@pytest.mark.parametrize('interval', (0, -1, float('nan'), float('inf')))
def test_the_interval_must_be_a_positive_finite_number(interval):
    with pytest.raises(ValueError, match='interval_s'):
        _reporter(RecordingClient(), UsageLedger(), interval_s=interval)


def test_the_stdio_client_satisfies_the_tool_caller_protocol():
    expected = inspect.signature(stats.ToolCaller.call_tool)
    actual = inspect.signature(StdioMcpClient.call_tool)
    assert [(p.name, p.kind) for p in actual.parameters.values()] == [
        (p.name, p.kind) for p in expected.parameters.values()]
    assert inspect.iscoroutinefunction(StdioMcpClient.call_tool)


def test_stats_does_not_import_econ_at_runtime():
    # Design boundary: stats may name the stdio client's type only; it types
    # the client as the ToolCaller protocol, so econ is never imported.
    tree = ast.parse(Path(stats.__file__).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != 'mvibe_proxy.econ'
            assert not (node.module == 'mvibe_proxy' and any(
                alias.name == 'econ' for alias in node.names))
        elif isinstance(node, ast.Import):
            assert all(alias.name != 'mvibe_proxy.econ' for alias in node.names)


# --- mmhmem_stats (task 2.4, Requirements 3.2, 3.3) -----------------------------

FAKE_MMHMEM = Path(__file__).with_name('fixtures') / 'fake_mmhmem_stats.py'
SENTINEL_BIN = str(Path('/sentinel/mmhmem-never-run'))
STDERR_SENTINEL = 'STDERR-SENTINEL'
ENV_SECRET = 'SECRET-ENV-VALUE-7f3a'
FIXTURE_DAYS = [{
    'day': '2026-11-10', 'project': 'mvibe', 'harness': 'mvibe-proxy',
    'avoided_input_tokens_est': 1200,
    'frontier_prompt_tokens_client_reported': 5000,
    'frontier_completion_tokens_client_reported': 700,
    'client_reported_sources': ['mvibe-proxy mistral-large-latest'],
    'worker_prompt_tokens': 0, 'worker_completion_tokens': 0, 'reads': 3,
}]
FIXTURE_TOTALS = {
    'avoided_input_tokens_est': 1200,
    'frontier_prompt_tokens_client_reported': 5000,
    'frontier_completion_tokens_client_reported': 700,
    'worker_prompt_tokens': 0, 'worker_completion_tokens': 0, 'reads': 3,
}


class Spawner:
    """Runs the fixture script in place of the configured binary.

    Records the argv and keywords mmhmem_stats asked for, and every process
    started, so tests can prove the fixed `stats --json` call and that a
    timed-out child is gone. The real mmhmem binary is never run.
    """

    def __init__(self):
        self.argv = []
        self.kwargs = []
        self.processes = []

    async def __call__(self, program, *args, **kwargs):
        self.argv.append((program, *args))
        self.kwargs.append(kwargs)
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-I', str(FAKE_MMHMEM), *args, **kwargs)
        self.processes.append(process)
        return process


def _fake_env(mode, **extra):
    return {**os.environ, 'FAKE_MMHMEM_MODE': mode, 'MISTRAL_API_KEY': ENV_SECRET, **extra}


async def _run_stats(mode, *, spawner=None, timeout_s=20.0, **extra):
    spawner = spawner or Spawner()
    result = await asyncio.wait_for(
        stats.mmhmem_stats(SENTINEL_BIN, env=_fake_env(mode, **extra), timeout_s=timeout_s,
                           spawn=spawner), 30)
    rendered = json.dumps(result)
    assert STDERR_SENTINEL not in rendered and ENV_SECRET not in rendered
    assert all(process.returncode is not None for process in spawner.processes)
    return result, spawner


@pytest.mark.anyio
async def test_unconfigured_binary_is_not_configured_without_spawning():
    spawner = Spawner()
    result = await stats.mmhmem_stats(None, env=dict(os.environ), timeout_s=1, spawn=spawner)
    assert result == {'available': False, 'reason': 'not_configured'}
    assert spawner.argv == []


@pytest.mark.anyio
async def test_unconfigured_binary_never_reaches_the_default_spawn(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('mmhmem_stats must not spawn without a binary')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', deny)
    result = await stats.mmhmem_stats(None, env={}, timeout_s=1)
    assert result == {'available': False, 'reason': 'not_configured'}


@pytest.mark.anyio
async def test_stats_json_is_parsed_into_days_and_totals():
    result, spawner = await _run_stats('json')
    assert result == {'available': True, 'days': FIXTURE_DAYS, 'totals': FIXTURE_TOTALS}
    assert spawner.argv == [(SENTINEL_BIN, 'stats', '--json')]
    kwargs = spawner.kwargs[0]
    assert kwargs['stdout'] == asyncio.subprocess.PIPE
    assert kwargs['stderr'] == asyncio.subprocess.DEVNULL
    assert kwargs['stdin'] == asyncio.subprocess.DEVNULL
    assert kwargs['env']['FAKE_MMHMEM_MODE'] == 'json'


@pytest.mark.anyio
async def test_the_callers_environment_reaches_the_child(tmp_path):
    data_dir = str(tmp_path / 'mmhmem-data')
    result, _ = await _run_stats('echo_data_dir', AGENT_MEM_DATA_DIR=data_dir)
    assert result == {'available': True, 'days': [], 'totals': {'data_dir': data_dir}}


@pytest.mark.anyio
async def test_the_empty_store_notice_is_no_stats_yet():
    result, _ = await _run_stats('notice')
    assert result == {'available': False, 'reason': 'no_stats_yet'}


@pytest.mark.anyio
async def test_a_non_zero_exit_is_reported_by_its_code():
    result, _ = await _run_stats('exit3')
    assert result == {'available': False, 'reason': 'exit_3'}


@pytest.mark.anyio
async def test_a_child_running_past_the_timeout_is_killed():
    started = time.monotonic()
    result, spawner = await _run_stats('sleep', timeout_s=0.5)
    assert result == {'available': False, 'reason': 'timeout'}
    assert len(spawner.processes) == 1
    assert spawner.processes[0].returncode is not None  # the process is gone
    assert time.monotonic() - started < 10


@pytest.mark.anyio
@pytest.mark.parametrize('output', (
    '',
    'garbage',
    '{"days": [',
    '[]',
    '"days"',
    '{"days": [], "totals": []}',
    '{"days": {}, "totals": {}}',
    '{"days": []}',
    '{"totals": {}}',
    'NaN-ish {"days": [], "totals": {}}',
    'no token-savings statistics recorded yet',
))
async def test_invalid_output_is_invalid_json(output):
    result, _ = await _run_stats('raw', FAKE_MMHMEM_OUTPUT=output)
    assert result == {'available': False, 'reason': 'invalid_json'}


@pytest.mark.anyio
async def test_stdout_is_capped_and_an_overflowing_child_is_killed():
    assert stats.MMHMEM_STATS_STDOUT_LIMIT == 8 * 1024 * 1024
    result, spawner = await _run_stats('flood')
    assert result == {'available': False, 'reason': 'invalid_json'}
    assert spawner.processes[0].returncode is not None


@pytest.mark.anyio
async def test_stdout_at_the_cap_is_parsed_and_one_byte_over_is_rejected(monkeypatch):
    monkeypatch.setattr(stats, 'MMHMEM_STATS_STDOUT_LIMIT', 4096)
    at_cap, _ = await _run_stats('sized', FAKE_MMHMEM_SIZE='4096')
    assert at_cap == {'available': True, 'days': [], 'totals': {'reads': 1}}
    over, _ = await _run_stats('sized', FAKE_MMHMEM_SIZE='4097')
    assert over == {'available': False, 'reason': 'invalid_json'}


@pytest.mark.anyio
async def test_a_spawn_failure_is_sanitized():
    async def missing(program, *args, **kwargs):
        raise FileNotFoundError(2, 'No such file or directory', program)
    result = await stats.mmhmem_stats(SENTINEL_BIN, env={}, timeout_s=1, spawn=missing)
    assert result == {'available': False, 'reason': 'spawn_failed'}


@pytest.mark.anyio
async def test_the_default_spawn_runs_the_configured_path(tmp_path):
    # A path that does not exist: the real spawn entry point fails, nothing runs.
    missing = str(tmp_path / 'no-such-mmhmem')
    result = await stats.mmhmem_stats(missing, env=dict(os.environ), timeout_s=5)
    assert result == {'available': False, 'reason': 'spawn_failed'}
    assert missing not in json.dumps(result)


@pytest.mark.anyio
async def test_cancelling_the_call_kills_the_child():
    spawner = Spawner()
    task = asyncio.create_task(stats.mmhmem_stats(
        SENTINEL_BIN, env=_fake_env('sleep'), timeout_s=30, spawn=spawner))
    await _until(lambda: spawner.processes, timeout_s=10)
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert spawner.processes[0].returncode is not None
