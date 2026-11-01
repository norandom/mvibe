"""Synthetic dispatcher checks; no test dependencies selected by wire input."""
import asyncio
from datetime import datetime, timezone
import json
import os
import subprocess
import sys

import pytest

from mvibe_auth import bridge
from mvibe_auth.api import (
    AuthConfig, AuthError, AuthFailure, AuthSnapshot, LoginCompleted,
    LoginHandle, LoginReusedCurrent,
)
from mvibe_auth._bridge_protocol import MAX_FRAME_BYTES, parse_response


def request(method, id='r', **params):
    return json.dumps(dict(version=1, id=id, method=method, params=params)).encode() + b'\n'


class Session:
    def __init__(self):
        self.config = AuthConfig(enabled=True)
        self.closed = False
        self.calls = []
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.outcome = LoginCompleted()
        self.error = None

    async def status(self):
        return {'signed_in': True, 'config_match': True}

    async def start_login(self, force=False):
        self.calls.append(('start', force))
        return LoginHandle('attempt', 'https://issuer.invalid/authorize?code=synthetic', datetime.fromtimestamp(2000000000, timezone.utc))

    async def wait_login(self, attempt_id):
        self.calls.append(('wait', attempt_id))
        self.waiting.set()
        await self.release.wait()
        return self.outcome

    async def cancel_login(self, attempt_id):
        self.calls.append(('cancel', attempt_id))
        self.outcome = AuthFailure('cancelled')
        self.release.set()

    async def resolve(self, target_url):
        self.calls.append(('resolve', target_url))
        if self.error:
            raise self.error
        return AuthSnapshot({'Authorization': 'Bearer synthetic-id', 'x-external-organization-id': 'org'}, datetime.fromtimestamp(2000000000, timezone.utc), str(len(self.calls)), target_url)

    async def close(self):
        self.closed = True
        self.release.set()


class Pipes:
    def __init__(self, session=None):
        self.input = asyncio.Queue()
        self.output = asyncio.Queue()
        self.session = session or Session()
        self.task = None
        self.frames = []

    async def read(self, limit):
        assert limit == MAX_FRAME_BYTES + 1
        return await self.input.get()

    async def write(self, frame):
        assert len(frame) <= MAX_FRAME_BYTES
        self.frames.append(frame)
        self.output.put_nowait(json.loads(frame))

    async def __aenter__(self):
        self.task = asyncio.create_task(bridge.serve(self.read, self.write, session_factory=lambda: self.session))
        return self

    async def send(self, method, id='r', **params):
        await self.input.put(request(method, id, **params))

    async def receive(self):
        return await asyncio.wait_for(self.output.get(), 2)

    async def __aexit__(self, *args):
        self.input.put_nowait(b'')
        try:
            await asyncio.wait_for(self.task, 2)
        finally:
            if not self.task.done():
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)


@pytest.mark.anyio
async def test_methods_and_request_scoped_resolution():
    async with Pipes() as p:
        for method, params in [('hello', {}), ('status', {}), ('login.start', {'force': True}), ('resolve', {'target_url': 'https://api.mistral.ai/v1/models'}), ('resolve', {'target_url': 'https://api.mistral.ai/v1/models'})]:
            await p.send(method, **params)
            value = await p.receive()
            assert value['ok'] is True
            parse_response(p.frames[-1], method)
        assert p.session.calls.count(('resolve', 'https://api.mistral.ai/v1/models')) == 2
        assert not any(b'refresh' in frame for frame in p.frames)
        await p.send('shutdown')
        assert (await p.receive())['result'] == {'closed': True}
        assert p.session.closed


@pytest.mark.anyio
async def test_native_metadata_is_request_scoped_and_optional():
    async with Pipes() as p:
        url = 'https://api.mistral.ai/v1/chat/completions'
        await p.send('resolve', 'legacy', target_url=url)
        assert 'metadata' not in (await p.receive())['result']
        for session_id, purpose, iteration, call_type in [('one', 'agent', 0, 'main_call'),
                ('two', 'title', 0, 'title_generation'), ('one', 'agent', 1, 'secondary_call')]:
            await p.send('resolve', session_id + purpose + str(iteration), target_url=url,
                request_context={'session_id': session_id, 'purpose': purpose, 'iteration': iteration})
            result = (await p.receive())['result']
            assert result['metadata']['session_id'] == session_id
            assert result['metadata']['call_type'] == call_type
            assert 'user_plan' not in result['metadata']
            assert 'message_id' not in result['metadata']
        assert p.session.calls.count(('resolve', url)) == 4


@pytest.mark.anyio
async def test_wait_does_not_block_cancel():
    async with Pipes() as p:
        await p.send('login.wait', 'wait', attempt_id='attempt')
        await asyncio.wait_for(p.session.waiting.wait(), 2)
        await p.send('login.cancel', 'cancel', attempt_id='attempt')
        values = [await p.receive(), await p.receive()]
        assert {v['id'] for v in values} == {'wait', 'cancel'}
        assert next(v for v in values if v['id'] == 'wait')['error']['code'] == 'cancelled'
        assert next(v for v in values if v['id'] == 'cancel')['result'] == {'cancelled': True}


@pytest.mark.anyio
@pytest.mark.parametrize('outcome', [LoginCompleted(), LoginReusedCurrent(), AuthFailure('conflict', retryable=True)])
async def test_wait_typed_terminal_outcomes(outcome):
    async with Pipes() as p:
        p.session.outcome = outcome
        p.session.release.set()
        await p.send('login.wait', attempt_id='attempt')
        value = await p.receive()
        if isinstance(outcome, AuthFailure):
            assert value['error'] == {'code': 'conflict', 'message': outcome.message, 'retryable': True}
        else:
            assert value['result'] == {'outcome': outcome.outcome}
        parse_response(p.frames[-1], 'login.wait')


@pytest.mark.anyio
@pytest.mark.parametrize('error,code', [(AuthError(AuthFailure('missing')), 'missing'), (AuthError(AuthFailure('invalid')), 'invalid'), (RuntimeError('synthetic-refresh-secret headers'), 'transient')])
async def test_resolution_errors_are_typed_and_sanitized(error, code, capsys):
    async with Pipes() as p:
        p.session.error = error
        await p.send('resolve', target_url='https://evil.invalid')
        value = await p.receive()
        assert value['error']['code'] == code
        assert 'synthetic-refresh-secret' not in str(value)
    assert capsys.readouterr() == ('', '')


@pytest.mark.anyio
@pytest.mark.parametrize('changes,code', [({'version': 2}, 'unsupported_version'), ({'method': 'unknown'}, 'unknown_method'), ({'refresh_token': 'synthetic-secret'}, 'invalid_frame'), ({'params': {'extra': 'synthetic-secret'}}, 'invalid_frame')])
async def test_rejected_envelopes_preserve_id(changes, code):
    async with Pipes() as p:
        value = json.loads(request('hello', 'correlated'))
        value.update(changes)
        await p.input.put(json.dumps(value).encode() + b'\n')
        response = await p.receive()
        assert response['id'] == 'correlated'
        assert response['error']['code'] == code
        assert 'synthetic-secret' not in str(response)
        await p.send('hello', 'next')
        assert (await p.receive())['ok']


@pytest.mark.anyio
@pytest.mark.parametrize('frame,code', [(b'{invalid\n', 'invalid_frame'), (b'\xff\n', 'invalid_frame'), (b'{}', 'invalid_frame'), (b'x' * (MAX_FRAME_BYTES + 1), 'frame_too_large'), (b'{"version":1,"id":"","method":"hello","params":{}}\n', 'invalid_frame')])
async def test_uncorrelatable_frames_retire_safely(frame, code):
    async with Pipes() as p:
        await p.input.put(frame)
        value = await p.receive()
        assert value['error']['code'] == code
        await asyncio.wait_for(p.task, 2)


@pytest.mark.anyio
async def test_duplicate_active_id_retires_without_ambiguous_replies():
    async with Pipes() as p:
        await p.send('login.wait', 'same', attempt_id='attempt')
        await asyncio.wait_for(p.session.waiting.wait(), 2)
        await p.send('hello', 'same')
        assert (await p.receive())['error']['code'] == 'invalid_frame'
        await asyncio.wait_for(p.task, 2)
        assert len(p.frames) == 1
        assert p.session.closed


@pytest.mark.anyio
async def test_active_request_bound_and_shutdown_remain_processable():
    async with Pipes() as p:
        for index in range(31):
            await p.send('login.wait', str(index), attempt_id='attempt')
        await p.send('status', 'overflow')
        overflow = await p.receive()
        assert overflow['id'] == 'overflow'
        assert overflow['error']['code'] == 'transient'
        assert len(p.session.calls) == 31
        await p.send('shutdown', 'stop')
        replies = [await p.receive() for _ in range(32)]
        assert replies[-1]['id'] == 'stop'
        assert replies[-1]['result'] == {'closed': True}
        assert all(v['error']['code'] == 'cancelled' for v in replies[:-1])
        assert p.session.closed


@pytest.mark.anyio
async def test_reserved_control_slot_cancels_saturated_login_waits():
    async with Pipes() as p:
        for index in range(31):
            await p.send('login.wait', str(index), attempt_id='attempt')
        await p.send('login.cancel', 'cancel', attempt_id='attempt')
        values = [await p.receive() for _ in range(32)]
        assert next(v for v in values if v['id'] == 'cancel')['result'] == {'cancelled': True}
        assert all(v['error']['code'] == 'cancelled' for v in values if v['id'] != 'cancel')


@pytest.mark.anyio
@pytest.mark.parametrize('closure', ['shutdown', 'eof'])
@pytest.mark.parametrize('visible_before_suspend', [False, True])
async def test_closure_drains_started_writer_before_acknowledgment(closure, visible_before_suspend):
    p = Pipes()
    writing = asyncio.Event()
    release_write = asyncio.Event()
    close_started = asyncio.Event()
    writer_cancelled = False
    writer_finished = False
    original_close = p.session.close

    async def close():
        await original_close()
        close_started.set()

    p.session.close = close

    async def write(frame):
        nonlocal writer_cancelled, writer_finished
        value = json.loads(frame)
        if value['id'] == 'current' and value['ok']:
            if visible_before_suspend:
                await p.write(frame)
            writing.set()
            try:
                await release_write.wait()
            except asyncio.CancelledError:
                writer_cancelled = True
                raise
            if not visible_before_suspend:
                await p.write(frame)
            writer_finished = True
        else:
            # Cancellation replies and shutdown acknowledgment must share the
            # original writer's output lock until its flush has completed.
            assert writer_finished
            await p.write(frame)

    task = asyncio.create_task(bridge.serve(p.read, write, session_factory=lambda: p.session))
    try:
        await p.send('resolve', 'current', target_url='https://api.mistral.ai/v1/models')
        await asyncio.wait_for(writing.wait(), 2)
        await p.send('login.wait', 'pending', attempt_id='attempt')
        await asyncio.wait_for(p.session.waiting.wait(), 2)
        p.input.put_nowait(request('shutdown', 'stop') if closure == 'shutdown' else b'')
        await asyncio.wait_for(close_started.wait(), 2)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not writer_cancelled, 'closure cancelled an already-started terminal write'
        assert not task.done(), 'closure completed before the terminal writer drained'
        assert len(p.frames) == int(visible_before_suspend)
        release_write.set()
        await asyncio.wait_for(task, 2)
        values = [json.loads(frame) for frame in p.frames]
        assert [value['id'] for value in values] == (
            ['current', 'pending', 'stop'] if closure == 'shutdown' else ['current', 'pending'])
        assert values[0]['ok'] is True
        assert values[1]['error']['code'] == 'cancelled'
        if closure == 'shutdown':
            assert values[-1]['result'] == {'closed': True}
        assert writer_finished
        assert p.session.closed
    finally:
        release_write.set()
        p.input.put_nowait(b'')
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)


@pytest.mark.anyio
async def test_duplicate_during_pipe_write_cannot_emit_two_replies():
    p = Pipes()
    started = asyncio.Event()
    unblock = asyncio.Event()
    duplicate_read = asyncio.Event()
    reads = 0

    async def read(limit):
        nonlocal reads
        frame = await p.read(limit)
        reads += 1
        if reads == 2:
            duplicate_read.set()
        return frame

    async def slow_write(frame):
        await p.write(frame)
        started.set()
        await unblock.wait()
    task = asyncio.create_task(bridge.serve(read, slow_write, session_factory=lambda: p.session))
    try:
        await p.send('hello', 'same')
        await asyncio.wait_for(started.wait(), 2)
        await p.send('hello', 'same')
        await asyncio.wait_for(duplicate_read.wait(), 2)
        await asyncio.sleep(0)
        assert not task.done()
        unblock.set()
        await asyncio.wait_for(task, 2)
        assert len(p.frames) == 1
    finally:
        unblock.set()
        p.input.put_nowait(b'')
        await asyncio.wait_for(task, 2)


@pytest.mark.anyio
@pytest.mark.parametrize('changes', [{'version': 2}, {'method': 'unknown'}, {'refresh_token': 'synthetic-secret'}])
async def test_malformed_duplicate_during_write_retires_with_one_reply(changes):
    p = Pipes()
    writing = asyncio.Event()
    release_write = asyncio.Event()
    duplicate_read = asyncio.Event()
    reads = 0

    async def read(limit):
        nonlocal reads
        frame = await p.read(limit)
        reads += 1
        if reads == 2:
            duplicate_read.set()
        return frame

    async def write(frame):
        await p.write(frame)
        if len(p.frames) == 1:
            writing.set()
            await release_write.wait()

    async def release():
        await duplicate_read.wait()
        await asyncio.sleep(0)
        release_write.set()

    task = asyncio.create_task(bridge.serve(read, write, session_factory=lambda: p.session))
    releaser = asyncio.create_task(release())
    try:
        await p.send('hello', 'same')
        await asyncio.wait_for(writing.wait(), 2)
        malformed = json.loads(request('hello', 'same'))
        malformed.update(changes)
        p.input.put_nowait(json.dumps(malformed).encode() + b'\n')
        await p.send('status', 'after')
        p.input.put_nowait(b'')
        await asyncio.wait_for(task, 2)
        assert len(p.frames) == 1, [json.loads(frame) for frame in p.frames]
        assert p.session.closed
    finally:
        release_write.set()
        p.input.put_nowait(b'')
        await asyncio.gather(task, releaser, return_exceptions=True)


@pytest.mark.anyio
async def test_malformed_duplicate_retires_even_if_original_finishes_during_error_write():
    p = Pipes()
    async def write(frame):
        await p.write(frame)
        p.session.release.set()
        # Finish the original wait while the duplicate error is being written.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    task = asyncio.create_task(bridge.serve(p.read, write, session_factory=lambda: p.session))
    try:
        await p.send('login.wait', 'same', attempt_id='attempt')
        await asyncio.wait_for(p.session.waiting.wait(), 2)
        malformed = json.loads(request('hello', 'same'))
        malformed['version'] = 2
        p.input.put_nowait(json.dumps(malformed).encode() + b'\n')
        await p.send('status', 'after')
        p.input.put_nowait(b'')
        await asyncio.wait_for(task, 2)
        assert [json.loads(frame)['id'] for frame in p.frames] == ['same']
        assert p.session.closed
    finally:
        p.input.put_nowait(b'')
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_eof_closes_session_with_wait_pending():
    async with Pipes() as p:
        await p.send('login.wait', attempt_id='attempt')
        await asyncio.wait_for(p.session.waiting.wait(), 2)
        await p.input.put(b'')
        await asyncio.wait_for(p.task, 2)
        assert p.session.closed


@pytest.mark.anyio
async def test_setup_error_actionable_and_correlated():
    def broken_factory():
        raise RuntimeError('synthetic-private-config')
    p = Pipes()
    task = asyncio.create_task(bridge.serve(p.read, p.write, session_factory=broken_factory))
    try:
        await p.send('hello', 'setup')
        value = await p.receive()
        assert value['id'] == 'setup'
        assert not value['ok']
        assert 'installed mvibe-auth' in value['error']['message']
        assert 'synthetic-private-config' not in str(value)
    finally:
        p.input.put_nowait(b'')
        await asyncio.wait_for(task, 2)


def test_adapter_default_optin_honors_explicit_disable(monkeypatch):
    captures = []
    monkeypatch.setattr(bridge, 'AuthSession', lambda config: captures.append(config))
    monkeypatch.delenv('VIBE_CLI_OIDC_ENABLED', raising=False)
    before = dict(os.environ)
    bridge.create_session()
    assert captures[-1].enabled
    assert dict(os.environ) == before
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'false')
    bridge.create_session()
    assert captures[-1].enabled is False


def test_installed_module_stdout_protocol_only():
    process = subprocess.run([sys.executable, '-m', 'mvibe_auth', 'bridge'],
        input=request('hello', 'hello') + request('shutdown', 'stop'), capture_output=True, timeout=10)
    assert process.returncode == 0, process.stderr
    lines = process.stdout.splitlines(keepends=True)
    assert len(lines) == 2
    assert parse_response(lines[0], 'hello').id == 'hello'
    assert parse_response(lines[1], 'shutdown').id == 'stop'
    assert process.stderr == b''


def test_stdio_discards_incidental_dependency_output():
    code = '''
import asyncio
from types import SimpleNamespace
from mvibe_auth import bridge
def factory():
    import sys
    print('synthetic-private-stdout')
    print('synthetic-private-stderr', file=sys.stderr)
    return SimpleNamespace(config=SimpleNamespace(audience='https://inference.invalid'), close=close)
async def close():
    pass
bridge.create_session = factory
asyncio.run(bridge.stdio())
'''
    process = subprocess.run([sys.executable, '-c', code],
        input=request('hello', 'hello') + request('shutdown', 'stop'), capture_output=True, timeout=10)
    assert process.returncode == 0
    assert len(process.stdout.splitlines()) == 2
    assert b'synthetic-private' not in process.stdout + process.stderr


def test_module_usage_does_not_run_auth():
    process = subprocess.run([sys.executable, '-m', 'mvibe_auth', 'unknown'], capture_output=True, timeout=10)
    assert process.returncode == 2
    assert process.stdout == b''
    assert b'Usage:' in process.stderr
