import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mvibe_auth import LoginCompleted, LoginReusedCurrent, LoginHandle, AuthFailure
from mvibe_auth import login_cli


class Session:
    config = SimpleNamespace(audience='https://inference.example.test')

    def __init__(self, outcome=None, url='https://issuer.example.test/SECRET'):
        self.outcome = outcome or LoginCompleted()
        self.url = url
        self.events = []
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def start_login(self):
        self.events.append('start')
        return LoginHandle('attempt', self.url, datetime.now(timezone.utc))

    async def wait_login(self, attempt):
        self.events.append('wait')
        self.waiting.set()
        if self.block:
            await self.release.wait()
        return self.outcome

    async def resolve(self, target):
        self.events.append(('resolve', target))

    async def cancel_login(self, attempt):
        self.events.append(('cancel', attempt))

    async def close(self):
        self.events.append('close')


@pytest.mark.anyio
async def test_completed_login_opens_browser_then_closes():
    session = Session()
    await login_cli.login(session_factory=lambda: session, open_browser=lambda url: session.events.append(('browser', url)) or True)
    assert session.events == ['start', ('browser', session.url), 'wait', 'close']


@pytest.mark.anyio
async def test_reuse_proves_usability_without_browser():
    session = Session(LoginReusedCurrent(), '')
    await login_cli.login(session_factory=lambda: session, open_browser=lambda _: pytest.fail('browser'))
    assert session.events == ['start', 'wait', ('resolve', session.config.audience), 'close']


@pytest.mark.anyio
@pytest.mark.parametrize('failure', [AuthFailure('rejected'), AuthFailure('conflict')])
async def test_failure_closes_and_raises(failure):
    session = Session(failure)
    with pytest.raises(Exception):
        await login_cli.login(session_factory=lambda: session, open_browser=lambda _: True)
    assert session.events[-1] == 'close'


@pytest.mark.anyio
async def test_cancel_cancels_owned_attempt_and_closes():
    session = Session()
    session.block = True
    task = asyncio.create_task(login_cli.login(session_factory=lambda: session, open_browser=lambda _: True))
    await session.waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.events[-2:] == [('cancel', 'attempt'), 'close']


@pytest.mark.anyio
async def test_concurrent_commands_cancel_only_their_own_session():
    first, second = Session(), Session()
    first.block = second.block = True
    tasks = [asyncio.create_task(login_cli.login(session_factory=lambda: first, open_browser=lambda _: True)),
             asyncio.create_task(login_cli.login(session_factory=lambda: second, open_browser=lambda _: True))]
    await asyncio.gather(first.waiting.wait(), second.waiting.wait())
    tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[0]
    assert 'close' not in second.events
    second.release.set()
    await tasks[1]
    assert ('cancel', 'attempt') not in second.events
    assert second.events[-1] == 'close'


@pytest.mark.anyio
async def test_browser_unavailable_cancels_and_closes():
    session = Session()
    with pytest.raises(Exception):
        await login_cli.login(session_factory=lambda: session, open_browser=lambda _: False)
    assert session.events[-2:] == [('cancel', 'attempt'), 'close']


@pytest.mark.anyio
async def test_unusable_current_never_completes():
    session = Session(LoginReusedCurrent(), '')
    async def unavailable(_):
        raise RuntimeError('SECRET')
    session.resolve = unavailable
    with pytest.raises(RuntimeError):
        await login_cli.login(session_factory=lambda: session)
    assert session.events[-1] == 'close'


def test_cli_marker_only_after_success_and_cleanup(monkeypatch, capsys):
    async def complete():
        print('SECRET noisy dependency')
    monkeypatch.setattr(login_cli, 'login', complete)
    assert login_cli.main() == 0
    assert capsys.readouterr() == (login_cli.CONNECTION_MARKER, '')


def test_cli_sanitizes_failure(monkeypatch, capsys):
    async def fail():
        print('SECRET')
        raise RuntimeError('SECRET https://authorization/code')
    monkeypatch.setattr(login_cli, 'login', fail)
    assert login_cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert 'SECRET' not in captured.err and 'https://' not in captured.err


def test_cli_close_failure_never_emits_marker(monkeypatch, capsys):
    session = Session()
    async def fail_close():
        raise RuntimeError('SECRET')
    session.close = fail_close
    async def run():
        await login_cli.login(session_factory=lambda: session, open_browser=lambda _: True)
    monkeypatch.setattr(login_cli, '_run', run)
    assert login_cli.main() == 1
    assert capsys.readouterr().out == ''

