"""Offline native request contract; no sibling checkout required."""
from mvibe_auth.http import get_adapter_user_agent
from mvibe_auth.request_metadata import RequestContext, build_request_metadata
import json
from pathlib import Path
import pytest
from mvibe_auth import request_metadata as native


def test_exact_native_user_agent():
    assert get_adapter_user_agent() == 'mistral-client-python/Mistral-Vibe/3.0.4'


def test_native_metadata_contract():
    metadata = build_request_metadata(RequestContext(session_id='session', message_id='message'))
    assert metadata['session_id'] == 'session'
    assert metadata['message_id'] == 'message'
    assert metadata['call_type'] == 'main_call'
    assert metadata['call_source'] == 'vibe_code'
    assert metadata['host_kind'] == 'local'
    assert metadata['version'] == '3.0.4'
    assert 'user_plan' not in metadata
    assert all(isinstance(value, str) for value in metadata.values())


@pytest.mark.parametrize(('purpose', 'iteration', 'call_type'), [
    ('agent', 0, 'main_call'), ('agent', 1, 'secondary_call'),
    ('compaction', 0, 'secondary_call'), ('generate', 0, 'secondary_call'),
    ('classify', 0, 'smart_approve'), ('title', 0, 'title_generation'),
])
def test_exact_native_golden(monkeypatch, purpose, iteration, call_type):
    monkeypatch.setattr(native, 'get_platform_id', lambda: 'windows')
    monkeypatch.setattr(native, 'get_platform_version', lambda: 'synthetic-os-version')
    monkeypatch.setattr(native.platform, 'machine', lambda: 'AMD64')
    fixture = json.loads((Path(__file__).parent / 'fixtures/native-request-metadata.json').read_text())
    expected = next(case['metadata'] for case in fixture['cases'] if case['call_type'] == call_type)
    actual = build_request_metadata(RequestContext(agent_entrypoint='cli', agent_version='3.0.4',
        client_name='vibe_cli', client_version='3.0.4', session_id='synthetic-session',
        message_id='synthetic-message', purpose=purpose, iteration=iteration))
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_metadata_attachment_preserves_application_fields():
    from mvibe_auth.request_metadata import attach_request_metadata
    payload = {'messages': [{'role': 'assistant', 'tool_calls': [{'function': {'arguments': '{  "x":1 }'}}]}],
               'tools': [{'function': {'parameters': {'type': 'object'}}}], 'stream': True,
               'metadata': {'custom': {'kept': True}, 'call_source': 'caller'}}
    result = json.loads(attach_request_metadata(json.dumps(payload).encode()))
    assert result['messages'] == payload['messages']
    assert result['tools'] == payload['tools']
    assert result['metadata']['custom'] == {'kept': True}
    assert result['metadata']['call_source'] == 'vibe_code'
    assert result['stream'] is True


def test_null_metadata_is_absent_and_gets_native_attribution():
    result = json.loads(native.attach_request_metadata(b'{"messages":[],"metadata":null}'))
    assert result['metadata']['call_source'] == 'vibe_code'
    assert result['metadata']['host_kind'] == 'local'


@pytest.mark.parametrize('body', [b'not json', b'[1,2]', b'null', b'{"metadata":[]}', b'{"metadata":1}', b'\xff', b'{"value": NaN}'])
def test_non_chat_objects_remain_raw(body):
    from mvibe_auth.request_metadata import attach_request_metadata
    assert attach_request_metadata(body) == body
