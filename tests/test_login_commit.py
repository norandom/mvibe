"""Login commit races with synthetic issuer, callback and keyring boundaries."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from keyring.errors import KeyringError
import pytest

from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import AuthFailure
from mvibe_auth._coordination import MutationCoordinator
from mvibe_auth._oidc_config import OidcError
from mvibe_auth._oidc_service import OidcService
from tests.test_credentials import tokens, slot


@pytest.fixture
def login(slot, monkeypatch, tmp_path):
    monkeypatch.setattr('mvibe_auth._oidc_service.time.time', lambda: 1700000000)
    config = AuthConfig(issuer='https://synthetic.invalid', client_id='synthetic-client',
                        audience='https://inference.invalid', enabled=True)
    service = OidcService.from_config(config)
    service.store.coordinator = MutationCoordinator(tmp_path / 'slot.lock')
    ready = asyncio.Event()
    callbacks = []
    class Callback:
        def __init__(self, uri, state):
            self.state = state
            self.result = asyncio.get_running_loop().create_future()
            self.closed = False
            callbacks.append(self)
        async def __aenter__(self): return self
        async def __aexit__(self):
            self.closed = True
            if not self.result.done(): self.result.cancel()
    class Session:
        closed = False
        def create_authorization_url(self, endpoint, **kwargs):
            ready.set()
            return 'https://synthetic.invalid/authorize', kwargs['state']
        async def fetch_token(self, endpoint, **kwargs): return {'synthetic': 'raw'}
        async def aclose(self): self.closed = True
    session = Session()
    service.client.session = lambda: session
    service.client.discover = AsyncMock(return_value=SimpleNamespace(
        authorization_endpoint='https://synthetic.invalid/authorize',
        token_endpoint='https://synthetic.invalid/token'))
    service.client.validated_tokens = AsyncMock(return_value=tokens(subject='browser-person'))
    monkeypatch.setattr('mvibe_auth._oidc_service.OidcCallback', Callback)
    notifications = []
    service.on_attempt_done = lambda attempt, status: notifications.append(status)
    return SimpleNamespace(service=service, slot=slot, ready=ready, callbacks=callbacks,
                           session=session, notifications=notifications)


async def begin(login):
    attempt = await login.service.start()
    await login.ready.wait()
    return attempt


async def finish(login, attempt):
    login.callbacks[0].result.set_result('https://synthetic.invalid/callback?code=synthetic')
    await attempt.task


@pytest.mark.anyio
@pytest.mark.parametrize('present', [False, True])
async def test_browser_wait_preserves_newer_identity(login, present):
    if present: login.slot.external(tokens())
    attempt = await begin(login)
    newer = tokens(subject='newer-person')
    login.slot.external(newer)
    await finish(login, attempt)
    assert await login.service.store.reload() == newer
    assert attempt.outcome.outcome == 'reused_current'
    assert login.slot.writes == 0


@pytest.mark.anyio
@pytest.mark.parametrize('present', [False, True])
async def test_validated_login_commits_and_repeated_wait_is_stable(login, present):
    if present: login.slot.external(tokens())
    attempt = await begin(login)
    assert login.slot.writes == 0
    await finish(login, attempt)
    first = await login.service.wait(attempt.attempt_id)
    assert first.outcome == 'completed'
    assert await login.service.wait(attempt.attempt_id) is first
    assert (await login.service.store.reload()).subject == 'browser-person'
    assert login.notifications == ['success']
    assert login.callbacks[0].closed and login.session.closed


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['denied', 'validation', 'persistence', 'cancelled'])
async def test_failed_login_has_one_safe_terminal_outcome(login, failure):
    original = tokens()
    login.slot.external(original)
    attempt = await begin(login)
    if failure == 'cancelled':
        await login.service.cancel(attempt.attempt_id)
    elif failure == 'denied':
        login.callbacks[0].result.set_exception(OidcError('OIDC authorization was declined'))
        await attempt.task
    else:
        if failure == 'validation':
            login.service.client.validated_tokens.side_effect = OidcError('private-token-value')
        else:
            login.slot.write_error = KeyringError('private-token-value')
        await finish(login, attempt)
    outcome = await login.service.wait(attempt.attempt_id)
    assert isinstance(outcome, AuthFailure)
    assert await login.service.wait(attempt.attempt_id) is outcome
    assert 'private-token-value' not in repr(outcome) + str(attempt.detail)
    assert await login.service.store.reload() == original
    assert len(login.notifications) == 1
    assert login.callbacks[0].closed and login.session.closed


@pytest.mark.anyio
async def test_unreadable_snapshot_fails_before_authorization(login):
    login.slot.read_error = KeyringError('private-token-value')
    attempt = await login.service.start()
    await attempt.task
    assert not login.ready.is_set()
    assert isinstance(attempt.outcome, AuthFailure)
    assert login.slot.writes == 0


@pytest.mark.anyio
@pytest.mark.parametrize('changes', [dict(issuer='https://other.invalid'),
    dict(id_token_expires_at=1700000000, refresh_token=None)])
async def test_conflict_unusable_current_does_not_claim_success(login, changes):
    attempt = await begin(login)
    newer = tokens(**changes)
    login.slot.external(newer)
    await finish(login, attempt)
    assert isinstance(attempt.outcome, AuthFailure)
    assert attempt.status == 'failed'
    assert await login.service.store.reload() == newer
    assert login.notifications == ['error']


@pytest.mark.anyio
async def test_no_native_coordinator_held_during_browser_wait(login):
    attempt = await begin(login)
    async def contender():
        async with login.service.store.coordinator.hold_async():
            login.slot.external(tokens(subject='cooperating-person'))
    await asyncio.wait_for(asyncio.create_task(contender()), 2)
    await finish(login, attempt)
    assert attempt.outcome.outcome == 'reused_current'


@pytest.mark.anyio
async def test_login_conflict_and_refresh_conflict_share_one_budget(login):
    attempt = await begin(login)
    login.slot.external(tokens(expires_at=1700000000))
    refresh_calls = []
    class RefreshSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def refresh_token(self, endpoint, **kwargs):
            refresh_calls.append(kwargs)
            login.slot.external(tokens(subject='second-newer-person'))
            return {'synthetic': 'raw'}
    login.service.client.session = RefreshSession
    login.service.client.validated_tokens.side_effect = [tokens(subject='browser-person'), tokens()]
    await finish(login, attempt)
    assert attempt.outcome == AuthFailure('conflict', retryable=True)
    assert len(refresh_calls) == 1
    assert (await login.service.store.reload()).subject == 'second-newer-person'
    assert login.slot.writes == 0
    assert len(login.callbacks) == 1


@pytest.mark.anyio
async def test_deadline_exposes_stable_failure_after_cleanup(login, monkeypatch):
    real_timeout = asyncio.timeout
    contexts = []
    def controlled_timeout(seconds):
        assert seconds == 300
        context = real_timeout(None)
        contexts.append(context)
        return context
    monkeypatch.setattr(asyncio, 'timeout', controlled_timeout)
    attempt = await begin(login)
    contexts[0].reschedule(asyncio.get_running_loop().time())
    outcome = await login.service.wait(attempt.attempt_id)
    assert isinstance(outcome, AuthFailure)
    assert outcome is await login.service.wait(attempt.attempt_id)
    assert login.callbacks[0].closed and login.session.closed
    assert login.notifications == ['error']
    assert login.slot.writes == 0


@pytest.mark.anyio
async def test_cancel_before_worker_start_has_one_terminal_result(login):
    attempt = await login.service.start()
    await login.service.cancel(attempt.attempt_id)
    assert await login.service.wait(attempt.attempt_id) == AuthFailure('cancelled')
    await login.service.cancel(attempt.attempt_id)
    assert login.notifications == ['cancelled']
    assert not login.ready.is_set()


@pytest.mark.anyio
async def test_cancel_during_commit_drains_write_and_reports_actual_result(login, monkeypatch):
    from threading import Event
    started, release = Event(), Event()
    original_write = login.slot.write
    def blocked_write(*args):
        started.set()
        assert release.wait(5)
        original_write(*args)
    monkeypatch.setattr('keyring.set_password', blocked_write)
    attempt = await begin(login)
    login.callbacks[0].result.set_result('https://synthetic.invalid/callback?code=synthetic')
    assert await asyncio.to_thread(started.wait, 5)
    cancellation = asyncio.create_task(login.service.cancel(attempt.attempt_id))
    try:
        await asyncio.sleep(0)
        attempt.task.cancel()  # Repeated cancellation must not release the worker's lease.
        await asyncio.sleep(0)
        assert not cancellation.done()
        assert not login.session.closed
    finally:
        release.set()
        await cancellation
    assert (await login.service.wait(attempt.attempt_id)).outcome == 'completed'
    assert (await login.service.store.reload()).subject == 'browser-person'
    assert login.notifications == ['success']
    assert login.callbacks[0].closed and login.session.closed
