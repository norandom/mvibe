"""Consumer contracts through the public facade; all external boundaries synthetic."""
import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone

from keyring.errors import KeyringError
from pydantic import SecretStr
import pytest

from mvibe_auth import api
from tests.test_credentials import slot, tokens, tenant
from tests.test_login_commit import login


@pytest.fixture
def session(login, monkeypatch):
    config = replace(login.service.config, organization_id='synthetic-org')
    login.service.config = config
    monkeypatch.setattr('mvibe_auth._oidc_service.OidcService.from_config',
                        classmethod(lambda cls, supplied: login.service))
    return api.AuthSession(config)


@pytest.mark.anyio
async def test_public_snapshot_is_immutable_target_bound_and_reloads(session, login, capsys):
    login.slot.external(tokens())
    url = 'https://inference.invalid/v1/models'
    first = await session.resolve(url)
    assert first.target == url and first.kind == 'id_token'
    assert first.headers == {'Authorization': 'Bearer synthetic-id',
                             'x-external-organization-id': 'synthetic-org',
                             'User-Agent': 'mistral-client-python/Mistral-Vibe/3.0.4'}
    assert first.expires_at == datetime.fromtimestamp(1700000900, timezone.utc)
    assert 'synthetic-id' not in repr(first) and 'synthetic-refresh' not in repr(first)
    assert not hasattr(first, 'refresh_token')
    with pytest.raises(TypeError): first.headers['Authorization'] = 'changed'
    with pytest.raises(FrozenInstanceError): first.target = 'changed'
    login.slot.external(tokens(id_token=SecretStr('rotated-id')))
    second = await session.resolve(url)
    assert second.headers['Authorization'] == 'Bearer rotated-id'
    assert second.revision != first.revision
    assert len(second.revision) >= 32
    assert login.callbacks == []
    assert capsys.readouterr().out == ''
    await session.close()


@pytest.mark.anyio
async def test_client_metadata_is_exact_native_transport_identity(session, login):
    from mvibe_auth import http

    login.slot.external(tokens())
    snapshot = await session.resolve('https://inference.invalid/v1/models')
    assert snapshot.headers['User-Agent'] == 'mistral-client-python/Mistral-Vibe/3.0.4'
    assert http.get_user_agent('mistral') == 'mistral-client-python/Mistral-Vibe/3.0.4'
    assert http.get_user_agent(None) == 'Mistral-Vibe/3.0.4'
    await session.close()


@pytest.mark.anyio
async def test_proxy_sends_shared_client_metadata_with_authoritative_identity(session, login):
    import httpx
    import httpx2
    import proxy

    login.slot.external(tokens())
    sent = []

    async def upstream(request):
        sent.append(request)
        return httpx2.Response(401, content=b'{"error":"synthetic"}')

    app = proxy.create_app(lambda: session, lambda **kwargs: httpx2.AsyncClient(
        transport=httpx2.MockTransport(upstream), follow_redirects=False))
    body = b'{ "messages": [], "stream": false }'
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.post('/v1/chat/completions', content=body, headers={
                'Authorization': 'Bearer caller', 'User-Agent': 'caller-agent',
                'x-external-organization-id': 'caller-org', 'x-external-workspace-id': 'caller-space'})
    assert response.status_code == 401
    assert len(sent) == 1
    request = sent[0]
    assert request.headers['user-agent'] == 'mistral-client-python/Mistral-Vibe/3.0.4'
    assert request.headers['authorization'] == 'Bearer synthetic-id'
    assert request.headers['x-external-organization-id'] == 'synthetic-org'
    assert 'x-external-workspace-id' not in request.headers
    import json
    assert json.loads(request.content)['messages'] == []
    assert json.loads(request.content)['metadata']['call_source'] == 'vibe_code'


@pytest.mark.anyio
@pytest.mark.parametrize('target', ['https://wrong.invalid/v1/models', 'garbage',
    'https://user:pass@inference.invalid/v1/models', 'https://inference.invalid/v1/models#fragment'])
async def test_invalid_target_precedes_storage(session, login, target):
    login.slot.read_error = AssertionError('store touched before target validation')
    with pytest.raises(api.AuthError) as exc:
        await session.resolve(target)
    assert exc.value.failure.code == 'invalid'
    await session.close()


@pytest.mark.anyio
async def test_status_contains_only_state_and_never_launches_browser(session, login):
    assert await session.status() == {'signed_in': False, 'config_match': False}
    login.slot.external(tokens())
    assert await session.status() == {'signed_in': True, 'config_match': True}
    login.slot.external(tokens(client_id='other-client'))
    assert await session.status() == {'signed_in': True, 'config_match': False}
    assert login.callbacks == []
    await session.close()


@pytest.mark.anyio
async def test_reuse_has_stable_typed_outcome_without_browser(session, login):
    login.slot.external(tokens())
    handle = await session.start_login()
    assert isinstance(handle, api.LoginHandle) and handle.authorization_url == ''
    result = await session.wait_login(handle.attempt_id)
    assert isinstance(result, api.LoginReusedCurrent)
    assert await session.wait_login(handle.attempt_id) is result
    assert login.callbacks == []
    await session.close()


@pytest.mark.anyio
async def test_start_returns_listening_url_and_completed_result(session, login, capsys):
    login.slot.external(tokens())
    handle = await session.start_login(force=True)
    assert handle.authorization_url == 'https://synthetic.invalid/authorize'
    assert login.ready.is_set() and len(login.callbacks) == 1
    assert handle.authorization_url not in repr(handle)
    login.callbacks[0].result.set_result('https://synthetic.invalid/callback?code=synthetic')
    result = await session.wait_login(handle.attempt_id)
    assert isinstance(result, api.LoginCompleted)
    assert await session.wait_login(handle.attempt_id) is result
    assert login.callbacks[0].closed and login.session.closed
    assert capsys.readouterr().out == ''
    await session.close()


@pytest.mark.anyio
async def test_cancel_and_close_return_safe_terminal_result(session, login):
    handle = await session.start_login(force=True)
    await session.cancel_login(handle.attempt_id)
    result = await session.wait_login(handle.attempt_id)
    assert isinstance(result, api.AuthFailure) and result.code == 'cancelled'
    assert await session.wait_login(handle.attempt_id) is result
    assert login.callbacks[0].closed and login.session.closed
    await session.close()
    await session.close()
    with pytest.raises(api.AuthError) as exc:
        await session.resolve('https://inference.invalid/v1/models')
    assert exc.value.failure.code == 'cancelled'


@pytest.mark.anyio
@pytest.mark.parametrize('state,code', [('absent', 'missing'), ('mismatch', 'invalid'),
                                       ('unreadable', 'transient')])
async def test_failures_are_typed_sanitized_without_key_fallback(session, login, monkeypatch, state, code):
    monkeypatch.setenv('MISTRAL_API_KEY', 'private-api-key')
    if state == 'mismatch': login.slot.external(tokens(client_id='different'))
    if state == 'unreadable': login.slot.read_error = KeyringError('private-store-detail')
    with pytest.raises(api.AuthError) as exc:
        await session.resolve('https://inference.invalid/v1/models')
    assert exc.value.failure.code == code
    assert 'private' not in str(exc.value)
    assert login.callbacks == []
    await session.close()


@pytest.mark.anyio
async def test_login_preparation_failure_is_sanitized(session, login):
    login.service.client.discover.side_effect = RuntimeError('private-discovery-data')
    with pytest.raises(api.AuthError) as exc:
        await session.start_login(force=True)
    assert exc.value.failure.code == 'transient'
    assert 'private' not in str(exc.value)
    assert login.session.closed
    await session.close()


def test_package_exports_only_inference_public_api():
    import mvibe_auth
    assert mvibe_auth.AuthSession is api.AuthSession
    assert 'MistralPlatformHTTPClient' not in mvibe_auth.__all__
    assert 'OidcTokens' not in api.__all__
    assert 'AuthSession' in api.__all__


@pytest.mark.anyio
async def test_config_is_captured_and_readonly(session, login, monkeypatch):
    login.slot.external(tokens())
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'false')
    monkeypatch.setenv('VIBE_CLI_OIDC_AUDIENCE', 'https://changed.invalid')
    monkeypatch.setenv('VIBE_CLI_OIDC_ORGANIZATION_ID', 'changed-org')
    with pytest.raises(AttributeError):
        session.config = api.AuthConfig(enabled=False)
    snapshot = await session.resolve('https://inference.invalid/v1/models')
    assert snapshot.headers['x-external-organization-id'] == 'synthetic-org'
    assert session.config.enabled
    await session.close()


@pytest.mark.anyio
async def test_public_resolution_reuses_revision_bound_stored_tenant(login, monkeypatch):
    monkeypatch.setattr('mvibe_auth._oidc_service.OidcService.from_config',
                        classmethod(lambda cls, supplied: login.service))
    session = api.AuthSession(login.service.config)
    login.slot.external(tokens(tenant=tenant()))
    snapshot = await session.resolve('https://inference.invalid/v1/chat/completions')
    assert snapshot.headers['x-external-organization-id'] == 'synthetic-org'
    assert snapshot.headers['x-external-workspace-id'] == 'synthetic-workspace'
    assert login.slot.writes == 0 and login.callbacks == []
    await session.close()


@pytest.mark.anyio
@pytest.mark.parametrize('conflict', [False, True])
async def test_terminal_login_outcomes_are_stable_and_sanitized(session, login, conflict):
    from mvibe_auth._oidc_config import OidcError
    handle = await session.start_login(force=True)
    if conflict:
        login.slot.external(tokens(subject='newer-person'))
        login.callbacks[0].result.set_result('https://synthetic.invalid/callback?code=synthetic')
    else:
        login.callbacks[0].result.set_exception(OidcError('private-code-and-token'))
    result = await session.wait_login(handle.attempt_id)
    assert await session.wait_login(handle.attempt_id) is result
    if conflict:
        assert isinstance(result, api.LoginReusedCurrent)
        assert login.slot.writes == 0
    else:
        assert isinstance(result, api.AuthFailure) and result.code == 'rejected'
    assert 'private' not in repr(result)
    await session.close()


@pytest.mark.anyio
async def test_close_drains_waiting_login(session, login):
    await session.start_login(force=True)
    await session.close()
    assert login.callbacks[0].closed and login.session.closed
    assert login.notifications == ['cancelled']
