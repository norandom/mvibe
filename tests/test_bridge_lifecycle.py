"""Real helper processes, synthetic shared keyring and real temporary OS locks."""
import json
import os
from queue import Empty, Queue
import signal
import subprocess
import sys
from threading import Thread
import time

import pytest
from pydantic import SecretStr

from mvibe_auth import api
from mvibe_auth._bridge_protocol import parse_response
from mvibe_auth._coordination import MutationCoordinator
from tests.test_credentials import tokens

TARGET = 'https://inference.invalid/v1/models'


class Child:
    def __init__(self, root, mode):
        self.root = root
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'tests.bridge_lifecycle_worker', str(root), mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.output = Queue()
        self.frames = []
        def collect():
            for line in self.process.stdout:
                self.output.put(line)
        self.reader = Thread(target=collect, daemon=True)
        self.reader.start()

    def send(self, method, id, **params):
        self.process.stdin.write(json.dumps(dict(version=1, id=id, method=method, params=params)).encode() + b'\n')
        self.process.stdin.flush()

    def receive(self, method, id):
        raw = self.output.get(timeout=10)
        return self.decode(raw, method, id)

    def decode(self, raw, method, id):
        self.frames.append(raw)
        result = parse_response(raw, method)
        assert result.id == id
        return result

    def finish(self):
        assert self.process.wait(timeout=10) == 0, self.process.stderr.read()
        self.reader.join(timeout=2)
        assert not self.reader.is_alive()
        stderr = self.process.stderr.read()
        assert stderr == b''
        # Resolve responses intentionally contain the inference ID token. They
        # never contain refresh/access tokens, raw responses or tracebacks.
        assert not any(secret in b''.join(self.frames) + stderr for secret in
            (b'synthetic-refresh', b'synthetic-access', b'Traceback', b'synthetic-private'))

    def cleanup(self):
        if self.process.poll() is None:
            self.kill()
        self.process.wait(timeout=10)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
        self.reader.join(timeout=2)

    def kill(self):
        # Windows venv executables may be redirector parents. Terminate the
        # recorded interpreter itself, which owns the worker and native lock.
        pid = self.root / 'runtime.pid'
        if pid.exists():
            os.kill(int(pid.read_text()), getattr(signal, 'SIGKILL', signal.SIGTERM))
        else:
            self.process.kill()


@pytest.fixture
def children():
    owned = []
    def spawn(root, mode):
        child = Child(root, mode)
        owned.append(child)
        return child
    yield spawn
    for child in reversed(owned):
        child.cleanup()


def barrier(child, root, name):
    deadline = time.monotonic() + 10
    while not (root / name).exists():
        assert child.process.poll() is None, child.process.stderr.read()
        assert time.monotonic() < deadline, name
        time.sleep(.01)


def native_lock(root, available):
    coordinator = MutationCoordinator(root / 'coord.lock', timeout=0)
    if available:
        with coordinator.hold():
            pass
    else:
        with pytest.raises(api.AuthError) as error:
            with coordinator.hold():
                pytest.fail('persistence lock released early')
        assert error.value.failure.code == 'transient'


def begin(child, root, operation):
    if operation == 'login':
        child.send('login.start', 'start', force=True)
        handle = child.receive('login.start', 'start')
        assert handle.ok
        child.send('login.wait', 'pending', attempt_id=handle.result.attempt_id)
    else:
        child.send('resolve', 'pending', target_url=TARGET)


@pytest.mark.parametrize('closure', ['shutdown', 'eof'])
@pytest.mark.parametrize('operation', ['login', 'refresh'])
def test_process_close_cancels_waiting_activity_before_persistence(tmp_path, children, closure, operation):
    if operation == 'refresh':
        (tmp_path / 'slot.json').write_text(tokens().model_dump_json())
    child = children(tmp_path, 'refresh-wait' if operation == 'refresh' else 'login-wait')
    begin(child, tmp_path, operation)
    barrier(child, tmp_path, 'refresh-entered' if operation == 'refresh' else 'callback-ready')
    if closure == 'shutdown':
        child.send('shutdown', 'stop')
        # Admission ends at shutdown even with another complete buffered frame.
        child.send('status', 'after-stop')
    else:
        child.process.stdin.close()
    pending = child.receive('resolve' if operation == 'refresh' else 'login.wait', 'pending')
    assert not pending.ok and pending.error.code == 'cancelled'
    if closure == 'shutdown':
        assert child.receive('shutdown', 'stop').result.closed
    child.finish()
    assert child.output.empty()
    assert not (tmp_path / 'write-entered').exists()
    assert (tmp_path / 'issuer-closed').exists()
    if operation == 'login':
        assert (tmp_path / 'callback-closed').exists()
    native_lock(tmp_path, True)


@pytest.mark.parametrize('closure', ['shutdown', 'eof'])
@pytest.mark.parametrize('operation', ['login', 'refresh'])
def test_process_graceful_close_drains_blocked_persistence(tmp_path, children, closure, operation):
    if operation == 'refresh':
        (tmp_path / 'slot.json').write_text(tokens().model_dump_json())
    child = children(tmp_path, operation + '-before')
    begin(child, tmp_path, operation)
    (tmp_path / 'release-callback').touch()
    barrier(child, tmp_path, 'write-entered')
    native_lock(tmp_path, False)
    if closure == 'shutdown':
        child.send('shutdown', 'stop')
    else:
        child.process.stdin.close()
    # A real completed native acquisition probe proves the worker retains its
    # lease; no acknowledgement or process exit can race ahead of the write.
    native_lock(tmp_path, False)
    method = 'resolve' if operation == 'refresh' else 'login.wait'
    pending = None
    try:
        raw = child.output.get(timeout=.15)
    except Empty:
        pass
    else:
        # login.wait cancellation can be reported while the attempt's commit
        # drains; only the terminal shutdown acknowledgement must wait.
        pending = child.decode(raw, method, 'pending')
        assert not pending.ok and pending.error.code == 'cancelled'
        with pytest.raises(Empty):
            child.output.get(timeout=.15)
    assert child.process.poll() is None
    (tmp_path / 'release-write').touch()
    if pending is None:
        pending = child.receive(method, 'pending')
    assert not pending.ok and pending.error.code == 'cancelled'
    if closure == 'shutdown':
        assert child.receive('shutdown', 'stop').result.closed
    child.finish()
    assert (tmp_path / 'write-committed').exists()
    if operation == 'login':
        assert (tmp_path / 'callback-closed').exists()
    native_lock(tmp_path, True)


@pytest.mark.parametrize('point', ['before', 'after'])
def test_forced_death_unknown_commit_restarts_from_authoritative_store(tmp_path, children, point):
    original = tokens(id_token=SecretStr('prior-id'), expires_at=4102444800, id_token_expires_at=4102444800)
    (tmp_path / 'slot.json').write_text(original.model_dump_json())
    child = children(tmp_path, 'login-' + point)
    begin(child, tmp_path, 'login')
    (tmp_path / 'release-callback').touch()
    barrier(child, tmp_path, 'write-entered' if point == 'before' else 'write-committed')
    native_lock(tmp_path, False)
    child.kill()
    child.process.wait(timeout=10)
    # The OS must release a lock even when Python finally blocks cannot run.
    native_lock(tmp_path, True)
    assert not (tmp_path / 'callback-closed').exists()
    assert child.output.empty()  # no successful completion was reported
    authoritative = json.loads((tmp_path / 'slot.json').read_text())
    expected = 'prior-id' if point == 'before' else 'synthetic-id'
    assert authoritative['id_token'] == expected
    restarted = children(tmp_path, 'restart')
    restarted.send('resolve', 'current', target_url=TARGET)
    result = restarted.receive('resolve', 'current')
    assert result.ok and result.result.headers['Authorization'] == 'Bearer ' + expected
    assert result.result.target == TARGET
    restarted.send('shutdown', 'stop')
    assert restarted.receive('shutdown', 'stop').result.closed
    restarted.finish()
    assert not (tmp_path / 'refresh-entered').exists()
