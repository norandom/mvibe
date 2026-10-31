"""Tenant selection and revision races use only synthetic storage and HTTP."""
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx2
import pytest
from keyring.errors import KeyringError
from pydantic import SecretStr, ValidationError

from mvibe_auth import _oidc_runtime as runtime
from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import AuthError
from mvibe_auth._coordination import MutationCoordinator
from mvibe_auth._oidc_config import OidcError
from mvibe_auth._oidc_credentials import OidcStorageError, OidcTenant
from mvibe_auth._oidc_service import OidcService
from tests.test_credentials import tokens, slot


@pytest.fixture
def tenant_setup(slot, monkeypatch, tmp_path):
    monkeypatch.setattr('mvibe_auth._oidc_service.time.time', lambda: 1700000000)
    config = AuthConfig(enabled=True, issuer='https://synthetic.invalid',
                        client_id='synthetic-client', audience='https://inference.invalid')
    service = OidcService.from_config(config)
    service.store.coordinator = MutationCoordinator(tmp_path / 'slot.lock')
    slot.raw = tokens().model_dump_json()
    return SimpleNamespace(slot=slot, config=config, service=service)


def owner(setup, **selection):
    from dataclasses import replace
    config = replace(setup.config, **selection)
    setup.service.config = config
    return runtime.OidcRuntime(config, service=setup.service)


@pytest.mark.anyio
@pytest.mark.parametrize('workspace', ['', 'explicit-workspace'])
async def test_explicit_organization_bypasses_discovery(tenant_setup, monkeypatch, workspace):
    discover = AsyncMock(side_effect=AssertionError('must bypass discovery'))
    monkeypatch.setattr(runtime, '_discover_workspace', discover)
    result = await owner(tenant_setup, organization_id='explicit-org', workspace_id=workspace).headers('https://inference.invalid/v1/models')
    assert result['x-external-organization-id'] == 'explicit-org'
    assert result.get('x-external-workspace-id', '') == workspace
    discover.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize('mode', ['override', 'cached', 'discovery'])
async def test_changed_revision_restarts_whole_resolution(tenant_setup, monkeypatch, mode):
    setup = tenant_setup
    cached = OidcTenant(selection='', organization_id='old-org', workspace_id='old-workspace')
    original = tokens(tenant=cached if mode == 'cached' else None)
    setup.slot.raw = original.model_dump_json()
    replacement = tokens(subject='new-person', id_token=SecretStr('new-id'), tenant=None)
    calls = []
    real_access = setup.service.access_tokens
    async def access(**kwargs):
        calls.append(kwargs.get('budget'))
        result = await real_access(**kwargs)
        if mode != 'discovery' and len(calls) == 1:
            setup.slot.raw = replacement.model_dump_json()
        return result
    setup.service.access_tokens = access
    async def discover(config, token, selection):
        if token == 'synthetic-id':
            setup.slot.raw = replacement.model_dump_json()
            return runtime._Workspace(id='old-workspace', organization_id='old-org')
        return runtime._Workspace(id='new-workspace', organization_id='new-org')
    monkeypatch.setattr(runtime, '_discover_workspace', discover)
    result = await owner(setup, organization_id='explicit-org' if mode == 'override' else '').headers('https://inference.invalid/v1/models')
    assert result['Authorization'] == 'Bearer new-id'
    assert result['x-external-organization-id'] == ('explicit-org' if mode == 'override' else 'new-org')
    assert len(calls) == 2 and calls[0] is calls[1] and calls[0] is not None


@pytest.mark.parametrize('field', ['id', 'organization_id'])
@pytest.mark.parametrize('bad', ['', ' ', '\t\n'])
def test_workspace_identifiers_reject_empty(field, bad):
    with pytest.raises(ValidationError):
        runtime._Workspace.model_validate({'id': 'workspace', 'organization_id': 'org'} | {field: bad})


@pytest.mark.anyio
@pytest.mark.parametrize('selection', ['', 'target', 'missing'])
async def test_discovery_order_exact_selection_and_pagination(tenant_setup, monkeypatch, selection):
    config = tenant_setup.config
    requests = []
    pages = [
        {'workspaces': [{'id': 'first', 'organization_id': 'first-org'},
                        {'id': 'second', 'organization_id': 'second-org'}]},
        {'workspaces': [{'id': 'target', 'organization_id': 'target-org'}]},
    ]
    async def handle(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Bearer synthetic-id'
        return httpx2.Response(200, json=pages.pop(0))
    real_client = runtime.VibeAsyncHTTPClient
    monkeypatch.setattr(runtime, '_WORKSPACE_PAGE_SIZE', 2)
    monkeypatch.setattr(runtime, 'VibeAsyncHTTPClient', lambda **kwargs: real_client(
        **kwargs, transport=httpx2.MockTransport(handle)))
    if selection == 'missing':
        with pytest.raises(OidcError, match='No accessible'):
            await runtime._discover_workspace(config, 'synthetic-id', selection)
    else:
        result = await runtime._discover_workspace(config, 'synthetic-id', selection)
        assert result.id == (selection or 'first')
        assert result.organization_id == ('target-org' if selection else 'first-org')
    assert [int(item.url.params['offset']) for item in requests] == ([0] if not selection else [0, 2])


@pytest.mark.anyio
@pytest.mark.parametrize('selection', ['', 'target'])
async def test_no_accessible_workspace_fails(tenant_setup, monkeypatch, selection):
    real_client = runtime.VibeAsyncHTTPClient
    monkeypatch.setattr(runtime, 'VibeAsyncHTTPClient', lambda **kwargs: real_client(
        **kwargs, transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json={'workspaces': []}))))
    with pytest.raises(OidcError, match='No accessible'):
        await owner(tenant_setup, workspace_id=selection).headers('https://inference.invalid/v1/models')


@pytest.mark.anyio
@pytest.mark.parametrize('selection', ['', 'different'])
async def test_cache_selection_matching(tenant_setup, monkeypatch, selection):
    cached = OidcTenant(selection='', organization_id='cache-org', workspace_id='cache-workspace')
    tenant_setup.slot.raw = tokens(tenant=cached).model_dump_json()
    discover = AsyncMock(return_value=runtime._Workspace(id='different', organization_id='different-org'))
    monkeypatch.setattr(runtime, '_discover_workspace', discover)
    result = await owner(tenant_setup, workspace_id=selection).headers('https://inference.invalid/v1/models')
    assert result['x-external-organization-id'] == ('different-org' if selection else 'cache-org')
    assert discover.await_count == bool(selection)


@pytest.mark.anyio
@pytest.mark.parametrize('unreadable', [False, True])
async def test_persistence_failure_requires_readable_matching_revision(tenant_setup, monkeypatch, unreadable):
    setup = tenant_setup
    def save(*args):
        if unreadable:
            setup.slot.read_error = KeyringError('synthetic storage error')
        raise OidcStorageError('synthetic save failure')
    monkeypatch.setattr(setup.service.store, 'save_tenant', save)
    monkeypatch.setattr(runtime, '_discover_workspace', AsyncMock(return_value=runtime._Workspace(id='workspace', organization_id='org')))
    if unreadable:
        with pytest.raises(OidcError, match='storage unavailable'):
            await owner(setup).headers('https://inference.invalid/v1/models')
    else:
        result = await owner(setup).headers('https://inference.invalid/v1/models')
        assert result['x-external-organization-id'] == 'org'


@pytest.mark.anyio
async def test_repeated_tenant_conflicts_exhaust_shared_budget(tenant_setup, monkeypatch):
    calls = []
    async def discover(config, token, selection):
        calls.append(token)
        tenant_setup.slot.raw = tokens(id_token=SecretStr('changed-' + str(len(calls)))).model_dump_json()
        return runtime._Workspace(id='workspace', organization_id='org')
    monkeypatch.setattr(runtime, '_discover_workspace', discover)
    with pytest.raises(AuthError) as error:
        await owner(tenant_setup).headers('https://inference.invalid/v1/models')
    assert error.value.failure.code == 'conflict'
    assert len(calls) == 2


@pytest.mark.anyio
async def test_service_and_tenant_share_one_conflict_budget(tenant_setup, monkeypatch):
    real_access = tenant_setup.service.access_tokens
    calls = []
    async def access(**kwargs):
        calls.append(kwargs['budget'])
        kwargs['budget'].conflict()  # Simulate a service refresh write conflict.
        return await real_access(**kwargs)
    tenant_setup.service.access_tokens = access
    async def discover(*args):
        tenant_setup.slot.raw = tokens(id_token=SecretStr('changed')).model_dump_json()
        return runtime._Workspace(id='workspace', organization_id='org')
    monkeypatch.setattr(runtime, '_discover_workspace', discover)
    with pytest.raises(AuthError) as error:
        await owner(tenant_setup).headers('https://inference.invalid/v1/models')
    assert error.value.failure.code == 'conflict'
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('bad_tenant', [
    OidcTenant(selection='', organization_id=' ', workspace_id='workspace'),
    OidcTenant(selection='', organization_id='org', workspace_id=''),
    OidcTenant(selection='target', organization_id='org', workspace_id='wrong'),
])
async def test_invalid_cache_tenant_is_not_reused(tenant_setup, monkeypatch, bad_tenant):
    tenant_setup.slot.raw = tokens(tenant=bad_tenant).model_dump_json()
    discover = AsyncMock(return_value=runtime._Workspace(id='target', organization_id='valid-org'))
    monkeypatch.setattr(runtime, '_discover_workspace', discover)
    result = await owner(tenant_setup, workspace_id=bad_tenant.selection).headers('https://inference.invalid/v1/models')
    assert result['x-external-organization-id'] == 'valid-org'
    discover.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize('outcome', ['signout', 'unreadable'])
async def test_final_override_recheck_fails_closed(tenant_setup, outcome):
    setup = tenant_setup
    real_access = setup.service.access_tokens
    async def access(**kwargs):
        result = await real_access(**kwargs)
        if outcome == 'signout':
            setup.slot.raw = None
        else:
            setup.slot.read_error = KeyringError('synthetic unreadable slot')
        return result
    setup.service.access_tokens = access
    with pytest.raises(OidcError):
        await owner(setup, organization_id='org').headers('https://inference.invalid/v1/models')


@pytest.mark.anyio
async def test_real_refresh_conflict_then_tenant_conflict_exhausts_budget(tenant_setup, monkeypatch):
    setup = tenant_setup
    setup.slot.raw = tokens(expires_at=1700000000).model_dump_json()
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def refresh_token(self, *args, **kwargs): return {'synthetic': True}
    monkeypatch.setattr(setup.service.client, 'session', Session)
    monkeypatch.setattr(setup.service.client, 'discover', AsyncMock(return_value=SimpleNamespace(token_endpoint='https://synthetic.invalid/token')))
    monkeypatch.setattr(setup.service.client, 'validated_tokens', AsyncMock(return_value=tokens()))
    commits = []
    async def changed_refresh(*args, **kwargs):
        commits.append(True)
        setup.slot.raw = tokens(id_token=SecretStr('fresh-competitor')).model_dump_json()
        return False
    monkeypatch.setattr(setup.service.store, 'save_if_current', changed_refresh)
    discoveries = []
    async def discover(config, token, selection):
        discoveries.append(token)
        setup.slot.raw = tokens(id_token=SecretStr('changed-after-discovery')).model_dump_json()
        return runtime._Workspace(id='workspace', organization_id='org')
    monkeypatch.setattr(runtime, '_discover_workspace', discover)
    with pytest.raises(AuthError) as error:
        await owner(setup).headers('https://inference.invalid/v1/models')
    assert error.value.failure.code == 'conflict'
    assert commits == [True]
    assert discoveries == ['fresh-competitor']


@pytest.mark.anyio
async def test_slow_discovery_rechecks_token_freshness(tenant_setup, monkeypatch):
    setup = tenant_setup
    now = [1700000000]
    monkeypatch.setattr('mvibe_auth._oidc_service.time.time', lambda: now[0])
    accesses = []
    async def access(**kwargs):
        accesses.append(kwargs['budget'])
        current = tokens() if len(accesses) == 1 else tokens(
            id_token=SecretStr('renewed'), expires_at=1700002000,
            id_token_expires_at=1700002000)
        setup.slot.raw = current.model_dump_json()
        return current
    monkeypatch.setattr(setup.service, 'access_tokens', access)
    async def discover(*args):
        now[0] = 1700001000
        return runtime._Workspace(id='workspace', organization_id='org')
    monkeypatch.setattr(runtime, '_discover_workspace', discover)
    result = await owner(setup).headers('https://inference.invalid/v1/models')
    assert result['Authorization'] == 'Bearer renewed'
    assert len(accesses) == 2 and accesses[0] is accesses[1]
