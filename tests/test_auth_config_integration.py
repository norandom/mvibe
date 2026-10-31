"""Captured configuration integration; all credentials and HTTP are synthetic."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pydantic import SecretStr

import httpx2
import pytest

from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import AuthError
from mvibe_auth._oidc_client import OidcClient
from mvibe_auth._oidc_service import OidcService
from mvibe_auth._oidc_config import OidcConfig, OidcError
from mvibe_auth._oidc_platform_credentials import OidcPlatformCredentialProvider
from mvibe_auth._credentials_port import PlatformCredentialTarget
from mvibe_auth import _oidc_runtime as runtime
from mvibe_auth import http as http_module
from mvibe_auth.http import VibeAsyncHTTPClient
from mvibe_auth.oidc_marker import OIDC_API_KEY, oidc_enabled


@pytest.mark.anyio
async def test_service_retains_enabled_and_registration(monkeypatch):
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'true')
    service = OidcService.from_env()
    tokens = SimpleNamespace(issuer=service.client.config.issuer,
                             client_id=service.client.config.client_id,
                             audience=service.client.config.audience,
                             expires_at=4102444800)
    service.store = SimpleNamespace(coordinator=service.store.coordinator,
                                    reload=AsyncMock(return_value=tokens))
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'false')
    monkeypatch.setenv('VIBE_CLI_OIDC_CLIENT_ID', 'new-client')
    assert await service.access_tokens() is tokens
    new_service = OidcService.from_env()
    assert new_service.config.client_id == 'new-client'
    with pytest.raises(AuthError):
        await new_service.access_tokens()
    assert oidc_enabled(service.config)
    assert not oidc_enabled(new_service.config)


@pytest.mark.anyio
async def test_transport_captures_config_and_injects_resolver(monkeypatch):
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'true')
    seen = []
    async def resolve(url):
        seen.append(url)
        return {'Authorization': 'Bearer synthetic-id'}
    def handle(request):
        assert request.headers['Authorization'] == 'Bearer synthetic-id'
        return httpx2.Response(302, headers={'location': 'https://other.example/'})
    async with VibeAsyncHTTPClient(oidc_resolver=resolve,
                                  transport=httpx2.MockTransport(handle),
                                  follow_redirects=True) as client:
        monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'false')
        response = await client.get('https://api.mistral.ai/v1/models',
                                    headers={'Authorization': f'Bearer {OIDC_API_KEY}'})
        assert response.status_code == 302
        assert client.auth_config.enabled
    assert seen == ['https://api.mistral.ai/v1/models']


@pytest.mark.anyio
async def test_marker_fails_closed_without_resolver_or_when_disabled():
    requests = []
    transport = httpx2.MockTransport(lambda request: requests.append(request) or httpx2.Response(200))
    for config in (AuthConfig(enabled=True), AuthConfig(enabled=False)):
        async with VibeAsyncHTTPClient(auth_config=config, transport=transport) as client:
            with pytest.raises(AuthError):
                await client.get('https://api.mistral.ai/v1/models',
                                 headers={'Authorization': f'Bearer {OIDC_API_KEY}'})
    assert requests == []


def test_transport_dependencies_do_not_import_runtime_or_facade():
    for name in ('http.py', '_http.py', 'oidc_marker.py', '_oidc_config.py'):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'mvibe_auth' / name).read_text())
        modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert 'mvibe_auth._oidc_runtime' not in modules
        assert 'mvibe_auth.api' not in modules
    for name in ('_oidc_service.py', '_oidc_runtime.py', '_oidc_platform_credentials.py',
                 'oidc_marker.py', '_oidc_config.py'):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'mvibe_auth' / name).read_text())
        assert not any(isinstance(node, ast.Attribute) and node.attr in ('environ', 'getenv')
                       for node in ast.walk(tree))


@pytest.mark.anyio
async def test_runtime_retains_tenant_destination_and_service(monkeypatch):
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'true')
    monkeypatch.setenv('VIBE_CLI_OIDC_ORGANIZATION_ID', 'old-org')
    monkeypatch.setenv('VIBE_CLI_OIDC_WORKSPACE_ID', 'old-workspace')
    owner = runtime.OidcRuntime.from_env()
    services = []
    from tests.test_credentials import tokens
    current = tokens(issuer=owner.config.issuer, client_id=owner.config.client_id,
                     audience=owner.config.audience, expires_at=4_000_000_000,
                     id_token_expires_at=4_000_000_000)
    async def resolve(**kwargs):
        services.append(owner.service)
        return current
    monkeypatch.setattr(owner.service, 'access_tokens', resolve)
    monkeypatch.setattr(owner.service.store, 'reload', AsyncMock(return_value=current))
    monkeypatch.setenv('VIBE_CLI_OIDC_ORGANIZATION_ID', 'new-org')
    monkeypatch.setenv('VIBE_CLI_OIDC_WORKSPACE_ID', 'new-workspace')
    monkeypatch.setenv('VIBE_CLI_OIDC_AUDIENCE', 'https://new.example')
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'false')
    headers = await owner.headers('https://api.mistral.ai/v1/models')
    assert headers == {'Authorization': 'Bearer synthetic-id',
                       'x-external-organization-id': 'old-org',
                       'x-external-workspace-id': 'old-workspace'}
    assert services == [owner.service]
    await owner.headers('https://chat.mistral.ai/api/test')
    await owner.headers('wss://api.mistral.ai/realtime')
    with pytest.raises(OidcError, match='destination'):
        await owner.headers('https://new.example/v1/models')
    assert services == [owner.service] * 3
    newer = runtime.OidcRuntime.from_env()
    assert newer.config.organization_id == 'new-org'
    assert newer.config.audience == 'https://new.example'
    with pytest.raises(AuthError):
        await newer.headers('https://new.example/v1/models')


def test_stored_cache_key_uses_captured_selection(monkeypatch):
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'true')
    monkeypatch.setenv('VIBE_CLI_OIDC_ORGANIZATION_ID', 'old-org')
    owner = runtime.OidcRuntime.from_env()
    config = owner.config
    tokens = SimpleNamespace(issuer=config.issuer, client_id=config.client_id,
                             audience=config.audience, subject='synthetic-user')
    monkeypatch.setattr(runtime.OidcCredentialStore, '_load', lambda self: tokens)
    old_key = owner.stored_cache_key()
    monkeypatch.setenv('VIBE_CLI_OIDC_ORGANIZATION_ID', 'new-org')
    assert owner.stored_cache_key() == old_key
    assert runtime.OidcRuntime.from_env().stored_cache_key() != old_key


@pytest.mark.anyio
async def test_workspace_discovery_receives_owner_selection(monkeypatch):
    config = AuthConfig(enabled=True, workspace_id='captured-workspace')
    owner = runtime.OidcRuntime(config)
    monkeypatch.setenv('VIBE_CLI_OIDC_WORKSPACE_ID', 'later-workspace')
    from tests.test_credentials import tokens
    current = tokens(issuer=config.issuer, client_id=config.client_id, audience=config.audience,
                     expires_at=4_000_000_000, id_token_expires_at=4_000_000_000)
    monkeypatch.setattr(owner.service, 'access_tokens', AsyncMock(return_value=current))
    monkeypatch.setattr(owner.service.store, 'reload', AsyncMock(return_value=current))
    monkeypatch.setattr(owner.service.store, '_reload', lambda: current)
    monkeypatch.setattr(owner.service.store, 'save_tenant', lambda *args: True)
    discover = AsyncMock(return_value=runtime._Workspace(id='captured-workspace', organization_id='discovered-org'))
    monkeypatch.setattr(runtime, '_discover_workspace', discover)
    headers = await owner.headers('https://api.mistral.ai/v1/models')
    assert headers['x-external-workspace-id'] == 'captured-workspace'
    discover.assert_awaited_once_with(config, 'synthetic-id', 'captured-workspace')


@pytest.mark.anyio
async def test_userinfo_provider_captures_enablement_and_keeps_access_token(monkeypatch):
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'true')
    config = AuthConfig.from_env().to_oidc_config()
    target = PlatformCredentialTarget(base_url='https://auth.mistral.ai/userinfo', audience=config.audience)
    tokens = SimpleNamespace(issuer=config.issuer, client_id=config.client_id,
                             audience=config.audience, subject='synthetic-user',
                             access_token=SecretStr('synthetic-access'),
                             id_token=SecretStr('synthetic-id'), expires_at=4_000_000_000)
    resolve = AsyncMock(return_value=tokens)
    provider = OidcPlatformCredentialProvider(target, resolve, config=config)
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'false')
    result = await provider.resolve(target)
    assert result.headers['Authorization'] == 'Bearer synthetic-access'
    assert result.kind == 'oauth_access_token'
    newer = OidcPlatformCredentialProvider(target, resolve, config=config)
    with pytest.raises(AuthError):
        await newer.resolve(target)
    assert resolve.await_count == 1


@pytest.mark.anyio
async def test_transport_preserves_captured_proxy_and_tls(monkeypatch):
    created = []
    class Transport(httpx2.AsyncBaseTransport):
        def __init__(self, **kwargs):
            created.append(kwargs)
        async def handle_async_request(self, request):
            return httpx2.Response(200)
    monkeypatch.setattr(httpx2, 'AsyncHTTPTransport', Transport)
    monkeypatch.setattr(http_module, 'getproxies', lambda: {'https': 'proxy.example:3128', 'no': '10.0.0.0/8,.internal.example'})
    trust = object()
    async with VibeAsyncHTTPClient(auth_config=AuthConfig(), verify=trust) as client:
        monkeypatch.setattr(http_module, 'getproxies', lambda: {'https': 'later.example:3128'})
        assert client._transport._no_proxy == ('10.0.0.0/8', '.internal.example')
        await client.get('https://api.mistral.ai/v1/models')
    assert created == [{'trust_env': False, 'verify': trust},
                       {'proxy': 'http://proxy.example:3128', 'trust_env': False, 'verify': trust}]


def test_explicit_config_never_reads_environment_and_rejects_mismatch(monkeypatch):
    config = AuthConfig(enabled=True, client_id='captured-client')
    def deny(cls):
        raise AssertionError('environment read after capture')
    monkeypatch.setattr(AuthConfig, 'from_env', classmethod(deny))
    service = OidcService.from_config(config)
    owner = runtime.OidcRuntime(config, service=service)
    assert owner.config is config
    assert oidc_enabled(config)
    target = PlatformCredentialTarget(base_url='https://auth.mistral.ai/userinfo', audience=config.audience)
    OidcPlatformCredentialProvider(target, AsyncMock(), config=config.to_oidc_config(), auth_config=config)
    with pytest.raises(OidcError, match='does not match'):
        OidcService(OidcClient(OidcConfig('https://other.example', 'other', config.audience,
                                       config.redirect_uri)), SimpleNamespace(), config=config)


@pytest.mark.parametrize('name,value', [('AUDIENCE', ''), ('ISSUER', 'http://remote.example'),
                                      ('REDIRECT_URI', 'https://remote.example/callback'),
                                      ('SCOPE', 'profile')])
def test_invalid_environment_is_rejected_at_consumer_creation(monkeypatch, name, value):
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'true')
    monkeypatch.setenv('VIBE_CLI_OIDC_' + name, value)
    for factory in (OidcService.from_env, runtime.OidcRuntime.from_env, VibeAsyncHTTPClient):
        with pytest.raises(AuthError) as error:
            factory()
        assert error.value.failure.code == 'invalid'
