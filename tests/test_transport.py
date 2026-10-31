"""Offline credential transport contracts; every HTTP destination is controlled."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import ssl

import httpx2
from pydantic import SecretStr
import pytest

from mvibe_auth import http as transport
from mvibe_auth import _oidc_runtime as runtime
from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import AuthError, AuthSnapshot
from mvibe_auth._credentials_port import PlatformCredentialTarget
from mvibe_auth._http import MistralPlatformHTTPClient, UntrustedPlatformDestination
from mvibe_auth._oidc_config import OidcError
from mvibe_auth._oidc_platform_credentials import OidcPlatformCredentialProvider
from mvibe_auth.oidc_marker import OIDC_API_KEY

MARKER = {'Authorization': f'Bearer {OIDC_API_KEY}'}


def runtime_credentials(monkeypatch, owner):
    from tests.test_credentials import tokens
    current = tokens(issuer=owner.config.issuer, client_id=owner.config.client_id,
                     audience=owner.config.audience, id_token=SecretStr('synthetic-id-token'),
                     expires_at=4_000_000_000, id_token_expires_at=4_000_000_000)
    resolve = AsyncMock(return_value=current)
    monkeypatch.setattr(owner.service, 'access_tokens', resolve)
    monkeypatch.setattr(owner.service.store, 'reload', AsyncMock(return_value=current))
    return resolve


@pytest.mark.anyio
@pytest.mark.parametrize('url', [
    'https://other.example/v1/models', 'http://api.mistral.ai/v1/models',
    'https://api.mistral.ai:444/v1/models', 'https://api.mistral.ai.other.example/',
])
async def test_inference_destination_denied_before_credentials(monkeypatch, controlled_http, url):
    config = AuthConfig(enabled=True, organization_id='synthetic-org')
    owner = runtime.OidcRuntime(config)
    resolve = Mock(side_effect=AssertionError('credential use before destination validation'))
    monkeypatch.setattr(runtime, '_resolve_id_token', resolve)
    async with transport.VibeAsyncHTTPClient(auth_config=config, oidc_resolver=owner.headers,
                                             transport=controlled_http.transport) as client:
        with pytest.raises(OidcError, match='destination'):
            await client.get(url, headers=MARKER)
    resolve.assert_not_called()
    assert controlled_http.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize('url', ['https://api.mistral.ai/v1/models',
                                 'https://api.mistral.ai:443/v1/models',
                                 'https://chat.mistral.ai/api/test'])
async def test_marker_uses_inference_id_snapshot(monkeypatch, controlled_http, url):
    config = AuthConfig(enabled=True, organization_id='synthetic-org', workspace_id='synthetic-ws')
    owner = runtime.OidcRuntime(config)
    runtime_credentials(monkeypatch, owner)
    controlled_http.queue('GET', str(httpx2.URL(url)))
    async def snapshot_headers(target):
        snapshot = AuthSnapshot(headers=await owner.headers(target), target=target,
                                expires_at=datetime(2100, 1, 1, tzinfo=UTC), revision='synthetic-revision')
        assert snapshot.kind == 'id_token'
        return dict(snapshot.headers)
    async with transport.VibeAsyncHTTPClient(auth_config=config, oidc_resolver=snapshot_headers,
                                             transport=controlled_http.transport) as client:
        await client.get(url, headers=MARKER)
    headers = controlled_http.requests[0].headers
    assert headers['Authorization'] == 'Bearer synthetic-id-token'
    assert headers['x-external-organization-id'] == 'synthetic-org'
    assert headers['x-external-workspace-id'] == 'synthetic-ws'


@pytest.mark.anyio
@pytest.mark.parametrize('enabled,has_resolver', [(True, False), (False, True)])
async def test_marker_fails_closed_without_enabled_owner(controlled_http, enabled, has_resolver):
    resolve = AsyncMock(return_value={'Authorization': 'Bearer synthetic-id'})
    async with transport.VibeAsyncHTTPClient(auth_config=AuthConfig(enabled=enabled),
            oidc_resolver=resolve if has_resolver else None, transport=controlled_http.transport) as client:
        with pytest.raises(AuthError):
            await client.get('https://api.mistral.ai/v1/models', headers=MARKER)
    resolve.assert_not_awaited()
    assert not controlled_http.requests


@pytest.mark.anyio
@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
@pytest.mark.parametrize('location', ['https://other.example/steal', '/v1/redirected'])
async def test_inference_marker_never_follows_redirect(controlled_http, status, location):
    url = 'https://api.mistral.ai/v1/models'
    controlled_http.queue('GET', url, status=status, headers={'location': location})
    resolve = AsyncMock(return_value={'Authorization': 'Bearer synthetic-id',
                                      'x-external-organization-id': 'synthetic-org'})
    async with transport.VibeAsyncHTTPClient(auth_config=AuthConfig(enabled=True), oidc_resolver=resolve,
            transport=controlled_http.transport, follow_redirects=True) as client:
        response = await client.get(url, headers=MARKER, follow_redirects=True)
    assert response.status_code == status
    assert not response.history
    assert len(controlled_http.requests) == 1
    resolve.assert_awaited_once_with(url)


def userinfo_provider():
    config = AuthConfig(enabled=True)
    target = PlatformCredentialTarget(base_url='https://auth.example/userinfo', audience=config.audience)
    tokens = SimpleNamespace(issuer=config.issuer, client_id=config.client_id, audience=config.audience,
        subject='synthetic-subject', access_token=SecretStr('synthetic-access-token'),
        id_token=SecretStr('synthetic-id-token'), expires_at=4_000_000_000)
    resolve = AsyncMock(return_value=tokens)
    return target, OidcPlatformCredentialProvider(target, resolve, config=config.to_oidc_config(),
                                                  auth_config=config), resolve


@pytest.mark.anyio
async def test_userinfo_sends_access_token_and_never_follows_redirect(controlled_http):
    target, provider, resolve = userinfo_provider()
    controlled_http.queue('GET', target.base_url, status=307,
                          headers={'location': 'https://other.example/steal'})
    async with MistralPlatformHTTPClient(base_url=target.base_url, audience=target.audience,
            credentials=provider, transport=controlled_http.transport, follow_redirects=True) as client:
        response = await client.get(target.base_url)
        with pytest.raises(ValueError, match='redirects'):
            await client.get(target.base_url, follow_redirects=True)
    assert response.status_code == 307
    assert len(controlled_http.requests) == 1
    assert controlled_http.requests[0].headers['Authorization'] == 'Bearer synthetic-access-token'
    assert 'x-external-organization-id' not in controlled_http.requests[0].headers
    resolve.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize('url', ['https://other.example/userinfo', 'http://auth.example/userinfo',
    'https://auth.example:444/userinfo', 'https://auth.example/userinfo-evil',
    'https://auth.example/userinfo/%252e%252e/steal', 'https://auth.example/userinfo/a%2fb'])
async def test_userinfo_scope_denied_before_token_resolution(controlled_http, url):
    target, provider, resolve = userinfo_provider()
    async with MistralPlatformHTTPClient(base_url=target.base_url, audience=target.audience,
            credentials=provider, transport=controlled_http.transport) as client:
        with pytest.raises(UntrustedPlatformDestination):
            await client.get(url)
    resolve.assert_not_awaited()
    assert not controlled_http.requests


@pytest.mark.anyio
async def test_userinfo_provider_mismatched_target_never_resolves():
    target, provider, resolve = userinfo_provider()
    result = await provider.resolve(PlatformCredentialTarget(base_url='https://other.example/',
                                                             audience=target.audience))
    assert result.reason == 'invalid'
    resolve.assert_not_awaited()


class RecordingTransport(httpx2.AsyncBaseTransport):
    def __init__(self, **kwargs):
        self.options = kwargs
        self.requests = []
        self.closed = 0
    async def handle_async_request(self, request):
        self.requests.append(request)
        return httpx2.Response(200)
    async def aclose(self):
        self.closed += 1


@pytest.mark.anyio
@pytest.mark.parametrize('url,expected', [
    ('https://api.example/', 'http://secure-proxy.example:3128'),
    ('http://api.example/', 'http://plain-proxy.example:3128'),
    ('https://10.2.3.4/', None), ('https://[fd00::2]/', None),
    ('https://a.internal.example/', None), ('https://internal.example/', 'http://secure-proxy.example:3128'),
    ('https://exact.example/', None), ('https://exact.example:444/', 'http://secure-proxy.example:3128'),
    ('https://notinternal.example/', 'http://secure-proxy.example:3128'),
    ('https://[::1]/', None),
    ('https://scheme.example/', None),
    ('http://scheme.example/', 'http://plain-proxy.example:3128'),
])
async def test_environment_proxy_routes_and_closes(monkeypatch, url, expected):
    made = []
    def make(**kwargs):
        item = RecordingTransport(**kwargs)
        made.append(item)
        return item
    monkeypatch.setattr(httpx2, 'AsyncHTTPTransport', make)
    monkeypatch.setattr(transport, 'getproxies', lambda: {
        'https': 'secure-proxy.example:3128', 'http': 'http://plain-proxy.example:3128',
        'no': '10.0.0.0/8,fd00::/8,.internal.example,exact.example:443,[::1]:443,https://scheme.example,broken.example:bad'})
    trust = object()
    async with transport.VibeAsyncHTTPClient(verify=trust) as client:
        await client.get(url)
    used = [item for item in made if item.requests]
    assert len(used) == 1
    assert used[0].options.get('proxy') == expected
    assert all(item.options['verify'] is trust and item.options['trust_env'] is False for item in made)
    assert all(item.closed == 1 for item in made)


@pytest.mark.anyio
@pytest.mark.parametrize('proxy_info', [{}, {'all': 'fallback.example:3128'},
                                       {'https': ' ', 'no': '*'},
                                       {'https': 'proxy.example:3128', 'no': '*'}])
async def test_direct_and_all_proxy_paths(monkeypatch, proxy_info):
    made = []
    def make(**kwargs):
        item = RecordingTransport(**kwargs)
        made.append(item)
        return item
    monkeypatch.setattr(httpx2, 'AsyncHTTPTransport', make)
    monkeypatch.setattr(httpx2._client, 'AsyncHTTPTransport', make)
    monkeypatch.setattr(transport, 'getproxies', lambda: proxy_info)
    async with transport.VibeAsyncHTTPClient() as client:
        await client.get('https://api.example/')
    used = [item for item in made if item.requests]
    assert len(used) == 1
    assert used[0].options.get('proxy') == ('http://fallback.example:3128' if 'all' in proxy_info else None)
    assert all(item.closed == 1 for item in made)


@pytest.mark.anyio
@pytest.mark.parametrize('via_proxy', [False, True])
async def test_owned_transport_marker_destination_and_redirect_policy(monkeypatch, via_proxy):
    made = []
    class RedirectTransport(RecordingTransport):
        async def handle_async_request(self, request):
            self.requests.append(request)
            return httpx2.Response(308, headers={'location': 'https://other.example/steal'})
    def make(**kwargs):
        item = RedirectTransport(**kwargs)
        made.append(item)
        return item
    monkeypatch.setattr(httpx2, 'AsyncHTTPTransport', make)
    monkeypatch.setattr(httpx2._client, 'AsyncHTTPTransport', make)
    monkeypatch.setattr(transport, 'getproxies', lambda: {'https': 'proxy.example:3128'} if via_proxy else {})
    config = AuthConfig(enabled=True, organization_id='synthetic-org')
    owner = runtime.OidcRuntime(config)
    resolve = runtime_credentials(monkeypatch, owner)
    async with transport.VibeAsyncHTTPClient(auth_config=config, oidc_resolver=owner.headers,
                                             follow_redirects=True) as client:
        with pytest.raises(OidcError, match='destination'):
            await client.get('https://other.example/', headers=MARKER)
        resolve.assert_not_called()
        assert all(not item.requests for item in made)
        response = await client.get('https://api.mistral.ai/v1/models', headers=MARKER,
                                    follow_redirects=True)
    assert response.status_code == 308
    requests = [request for item in made for request in item.requests]
    assert len(requests) == 1
    assert requests[0].headers['Authorization'] == 'Bearer synthetic-id-token'
    assert resolve.call_count == 1
    assert all(item.closed == 1 for item in made)


@pytest.mark.anyio
async def test_injected_transport_ignores_machine_proxy(monkeypatch, controlled_http):
    def deny():
        raise AssertionError('injected transport must not discover machine proxy')
    monkeypatch.setattr(transport, 'getproxies', deny)
    controlled_http.queue('GET', 'https://api.example/')
    async with transport.VibeAsyncHTTPClient(transport=controlled_http.transport) as client:
        await client.get('https://api.example/')
    assert len(controlled_http.requests) == 1


@pytest.mark.parametrize('system', [False, True])
def test_configured_trust_additive_certificates_and_cache(monkeypatch, system):
    context = SimpleNamespace(verify_flags=0, load_verify_locations=Mock())
    certifi_context = Mock(return_value=context)
    system_context = Mock(return_value=context)
    monkeypatch.setattr(transport.ssl, 'create_default_context', certifi_context)
    monkeypatch.setattr(transport.truststore, 'SSLContext', system_context)
    monkeypatch.setattr(transport.certifi, 'where', lambda: 'synthetic-public-ca.pem')
    monkeypatch.setenv('SSL_CERT_FILE', 'synthetic-private-ca.pem')
    monkeypatch.setenv('SSL_CERT_DIR', 'synthetic-ca-dir')
    transport.configure_ssl_context(enable_system_trust_store=system)
    assert transport.build_ssl_context() is context
    assert transport.build_ssl_context() is context
    context.load_verify_locations.assert_called_once_with(cafile='synthetic-private-ca.pem', capath='synthetic-ca-dir')
    if system:
        system_context.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        certifi_context.assert_not_called()
    else:
        certifi_context.assert_called_once_with(cafile='synthetic-public-ca.pem')
        system_context.assert_not_called()
        assert context.verify_flags & ssl.VERIFY_X509_STRICT
        assert context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_pinned_trust_survives_later_configuration(monkeypatch):
    first, second = object(), object()
    normal = Mock(return_value=first)
    system = Mock(return_value=second)
    monkeypatch.setattr(transport.ssl, 'create_default_context', normal)
    monkeypatch.setattr(transport.truststore, 'SSLContext', system)
    transport.pin_ssl_context(enable_system_trust_store=True)
    assert transport.build_ssl_context() is second
    transport.configure_ssl_context(enable_system_trust_store=False)
    assert transport.system_trust_store_enabled()
    assert transport.build_ssl_context() is second
    normal.assert_not_called()
    system.assert_called_once()
