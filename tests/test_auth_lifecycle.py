"""Public consumer lifecycle/parity; only issuer, keyring and HTTP are synthetic."""
import asyncio
from dataclasses import replace
from pathlib import Path
import re
from threading import Event
from unittest.mock import AsyncMock

import httpx2
from pydantic import SecretStr
import pytest

from mvibe_auth import api
from mvibe_auth._coordination import MutationCoordinator
from mvibe_auth._oidc_service import OidcService
from tests.test_credentials import slot, tokens
from tests.test_login_commit import login

TARGET = 'https://inference.invalid/v1/models'


@pytest.fixture
def session(login, monkeypatch):
    config = replace(login.service.config, organization_id='synthetic-org')
    login.service.config = config
    monkeypatch.setattr(OidcService, 'from_config', classmethod(lambda cls, config: login.service))
    return api.AuthSession(config)


async def turns():
    for _ in range(5):
        await asyncio.sleep(0)


def no_secrets(text):
    for secret in ('synthetic-access', 'synthetic-refresh', 'synthetic-id',
                   'rotated-id', 'rotated-refresh', 'private-response-payload'):
        assert secret not in text


@pytest.mark.anyio
async def test_close_cancels_all_callback_waiters_and_repeated_close(session, login):
    handle = await session.start_login(force=True)
    waiters = [asyncio.create_task(session.wait_login(handle.attempt_id)) for _ in range(3)]
    await turns()
    await asyncio.gather(session.close(), session.close())
    results = await asyncio.gather(*waiters, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert login.callbacks[0].closed and login.callbacks[0].result.done()
    assert login.session.closed and login.notifications == ['cancelled']
    await session.close()
    for operation in (session.status(), session.resolve(TARGET), session.start_login(),
                      session.wait_login(handle.attempt_id), session.cancel_login(handle.attempt_id)):
        with pytest.raises(api.AuthError) as error:
            await operation
        assert error.value.failure.code == 'cancelled'


@pytest.mark.anyio
async def test_close_cancels_refresh_and_queued_resolution(session, login):
    login.slot.external(tokens(expires_at=1700000000))
    entered, exited = asyncio.Event(), asyncio.Event()
    class Refresh:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): exited.set()
        async def refresh_token(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
    login.service.client.session = Refresh
    first = asyncio.create_task(session.resolve(TARGET))
    await asyncio.wait_for(entered.wait(), 2)
    queued = asyncio.create_task(session.resolve(TARGET))
    await turns()
    await session.close()
    assert all(isinstance(result, asyncio.CancelledError) for result in
               await asyncio.gather(first, queued, return_exceptions=True))
    assert exited.is_set() and login.slot.writes == 0
    # Cancellation has left the native coordinator available to another owner.
    async with MutationCoordinator(login.service.store.coordinator.path, timeout=0).hold_async():
        pass
    await session.close()


@pytest.mark.anyio
@pytest.mark.parametrize('operation', ['login', 'refresh'])
async def test_repeated_close_cancellation_drains_persistence_before_native_release(
        session, login, monkeypatch, operation, caplog, capsys):
    caplog.set_level('DEBUG')
    entered, release = Event(), Event()
    original_write = login.slot.write
    def blocked_write(*args):
        entered.set()
        assert release.wait(10)
        original_write(*args)
    monkeypatch.setattr('keyring.set_password', blocked_write)
    if operation == 'login':
        handle = await session.start_login(force=True)
        login.callbacks[0].result.set_result('https://synthetic.invalid/callback?code=synthetic')
        request = asyncio.create_task(session.wait_login(handle.attempt_id))
    else:
        login.slot.external(tokens(expires_at=1700000000))
        class Refresh:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def refresh_token(self, *args, **kwargs): return {'synthetic': 'raw'}
        login.service.client.session = Refresh
        login.service.client.validated_tokens = AsyncMock(return_value=tokens())
        request = asyncio.create_task(session.resolve(TARGET))
    assert await asyncio.to_thread(entered.wait, 5)
    closing = asyncio.create_task(session.close())
    contender_entered = asyncio.Event()
    async def contender():
        async with MutationCoordinator(login.service.store.coordinator.path).hold_async():
            contender_entered.set()
    contender_task = asyncio.create_task(contender())
    try:
        await turns()
        for _ in range(3):
            closing.cancel()
            request.cancel()
            await turns()
        assert not closing.done() and not contender_entered.is_set()
        assert login.slot.writes == 0
        # A completed, immediate native acquisition attempt proves exclusion;
        # an unscheduled contender alone would not prove lock retention.
        probe = MutationCoordinator(login.service.store.coordinator.path, timeout=0)
        with pytest.raises(api.AuthError) as error:
            await asyncio.to_thread(probe._acquire)
        assert error.value.failure.code == 'transient'
    finally:
        release.set()
        results = await asyncio.gather(closing, request, return_exceptions=True)
        await asyncio.wait_for(contender_task, 5)
        await session.close()
    assert isinstance(results[0], asyncio.CancelledError)
    assert contender_entered.is_set() and login.slot.writes == 1
    if operation == 'login':
        assert login.callbacks[0].closed and login.session.closed
        assert login.notifications == ['success']
    captured = capsys.readouterr()
    no_secrets(caplog.text + captured.out + captured.err)


@pytest.mark.anyio
@pytest.mark.parametrize('selection', ['', 'selected-workspace'])
async def test_two_public_consumers_share_rotation_tenant_and_revisions(login, monkeypatch, selection):
    config = replace(login.service.config, workspace_id=selection)
    first_service = login.service
    first_service.config = config
    second_service = OidcService.from_config(config)
    second_service.store.coordinator = MutationCoordinator(first_service.store.coordinator.path)
    second_service.client.discover = first_service.client.discover
    services = iter((first_service, second_service))
    monkeypatch.setattr(OidcService, 'from_config', classmethod(lambda cls, config: next(services)))
    sessions = [api.AuthSession(config), api.AuthSession(config)]
    refresh_entered, release_refresh = asyncio.Event(), asyncio.Event()
    refresh_calls = []
    class Refresh:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def refresh_token(self, *args, **kwargs):
            refresh_calls.append(kwargs)
            refresh_entered.set()
            await release_refresh.wait()
            return {'synthetic': 'raw'}
    for service in (first_service, second_service):
        service.client.session = Refresh
        service.client.validated_tokens = AsyncMock(return_value=tokens(
            id_token=SecretStr('rotated-id'), refresh_token=SecretStr('rotated-refresh')))
    tenant_calls = []
    async def http(request):
        tenant_calls.append(request)
        identity = request.headers['Authorization']
        workspace = selection or ('new-workspace' if identity == 'Bearer new-person-id' else 'first-workspace')
        org = 'new-org' if identity == 'Bearer new-person-id' else 'first-org'
        return httpx2.Response(200, json={'workspaces': [{'id': workspace, 'organization_id': org}]})
    from mvibe_auth import _oidc_runtime as runtime
    real_client = runtime.VibeAsyncHTTPClient
    monkeypatch.setattr(runtime, 'VibeAsyncHTTPClient', lambda **kwargs: real_client(
        **kwargs, transport=httpx2.MockTransport(http)))
    login.slot.external(tokens(expires_at=1700000000))
    pending = [asyncio.create_task(sessions[0].resolve(TARGET))]
    try:
        await asyncio.wait_for(refresh_entered.wait(), 2)
        pending.append(asyncio.create_task(sessions[1].resolve(TARGET)))
        await turns()
        release_refresh.set()
        first, second = await asyncio.gather(*pending)
        assert len(refresh_calls) == 1
        assert first.headers == second.headers and first.revision == second.revision
        assert first.headers['Authorization'] == 'Bearer rotated-id'
        assert first.headers['x-external-organization-id'] == 'first-org'
        assert first.headers['x-external-workspace-id'] == (selection or 'first-workspace')
        assert len(tenant_calls) == 1
        assert tenant_calls[0].headers['Authorization'] == 'Bearer rotated-id'
        # An external identity replacement invalidates both consumers' tenant cache.
        login.slot.external(tokens(subject='new-person', id_token=SecretStr('new-person-id')))
        for consumer in sessions:
            current = await consumer.resolve(TARGET)
            assert current.headers['Authorization'] == 'Bearer new-person-id'
            assert current.headers['x-external-organization-id'] == 'new-org'
            assert current.headers['x-external-workspace-id'] == (selection or 'new-workspace')
            assert current.revision != first.revision
        assert len(tenant_calls) == 2 and login.callbacks == []
    finally:
        release_refresh.set()
        await asyncio.gather(*(consumer.close() for consumer in sessions))
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.anyio
async def test_public_failure_and_representations_exclude_diagnostics(session, login, caplog, capsys):
    caplog.set_level('DEBUG')
    login.slot.external(tokens(expires_at=1700000000))
    login.service.client.discover.side_effect = RuntimeError(
        'private-response-payload synthetic-access synthetic-refresh synthetic-id')
    with pytest.raises(api.AuthError) as error:
        await session.resolve(TARGET)
    assert error.value.failure.code == 'transient'
    no_secrets(str(error.value) + repr(error.value) + repr(error.value.failure))
    await session.close()
    captured = capsys.readouterr()
    no_secrets(caplog.text + captured.out + captured.err)


@pytest.mark.anyio
@pytest.mark.parametrize('repeated', [False, True])
async def test_public_external_writer_race_uses_single_shared_budget(login, monkeypatch, repeated):
    monkeypatch.setattr(OidcService, 'from_config', classmethod(lambda cls, config: login.service))
    session = api.AuthSession(login.service.config)
    from mvibe_auth import _oidc_runtime as runtime
    calls = []
    async def http(request):
        calls.append(request.headers['Authorization'])
        if len(calls) == 1 or repeated:
            login.slot.external(tokens(subject=f'person-{len(calls)}',
                                       id_token=SecretStr(f'new-id-{len(calls)}')))
        return httpx2.Response(200, json={'workspaces': [
            {'id': 'workspace', 'organization_id': 'organization'}]})
    real_client = runtime.VibeAsyncHTTPClient
    monkeypatch.setattr(runtime, 'VibeAsyncHTTPClient', lambda **kwargs: real_client(
        **kwargs, transport=httpx2.MockTransport(http)))
    login.slot.external(tokens())
    try:
        if repeated:
            with pytest.raises(api.AuthError) as error:
                await session.resolve(TARGET)
            assert error.value.failure == api.AuthFailure('conflict', retryable=True)
            assert login.slot.writes == 0
        else:
            result = await session.resolve(TARGET)
            assert result.headers['Authorization'] == 'Bearer new-id-1'
            assert result.headers['x-external-organization-id'] == 'organization'
            assert login.slot.writes == 1
        assert calls == ['Bearer synthetic-id', 'Bearer new-id-1']
        assert login.callbacks == []
    finally:
        await session.close()


def guide_examples():
    """Execute the actual guide blocks; importing them performs no login."""
    import ast
    guide = Path(__file__).parents[1] / 'docs' / 'auth-library.md'
    blocks = re.findall(r'```python\n(.*?)```', guide.read_text(encoding='utf-8'), re.S)
    assert len(blocks) == 2
    namespace = {}
    for block in blocks:
        tree = ast.parse(block)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module == 'mvibe_auth'
            if isinstance(node, ast.Import):
                assert False, 'examples import public library names only'
            if isinstance(node, ast.Attribute):
                assert not node.attr.startswith('_')
        exec(compile(tree, str(guide), 'exec'), namespace)
    return namespace


@pytest.mark.anyio
@pytest.mark.parametrize('force', [False, True])
async def test_guide_sign_in_example_runs_against_synthetic_boundaries(login, monkeypatch, force):
    monkeypatch.setattr(OidcService, 'from_config', classmethod(lambda cls, config: login.service))
    login.slot.external(tokens())
    examples = guide_examples()
    presented = []
    def present(url):
        presented.append(url)
        login.callbacks[0].result.set_result('https://synthetic.invalid/callback?code=synthetic')
    outcome = await examples['sign_in'](login.service.config, present, force=force)
    assert outcome.outcome == ('completed' if force else 'reused_current')
    assert bool(presented) is force
    if force:
        assert login.callbacks[0].closed and login.session.closed


@pytest.mark.anyio
async def test_guide_sign_in_closes_on_presentation_failure(login, monkeypatch):
    monkeypatch.setattr(OidcService, 'from_config', classmethod(lambda cls, config: login.service))
    def fail(url): raise RuntimeError('presentation unavailable')
    with pytest.raises(RuntimeError, match='presentation unavailable'):
        await guide_examples()['sign_in'](login.service.config, fail)
    assert login.callbacks[0].closed and login.session.closed


@pytest.mark.anyio
async def test_guide_request_example_resolves_each_request(session, login):
    example = guide_examples()['prepare_request']
    login.slot.external(tokens())
    first = await example(session, TARGET)
    login.slot.external(tokens(id_token=SecretStr('rotated-id')))
    second = await example(session, TARGET)
    assert first['Authorization'] == 'Bearer synthetic-id'
    assert second['Authorization'] == 'Bearer rotated-id'
    await session.close()
