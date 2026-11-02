"""Offline public-factory route contract; every external boundary is injected."""
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import httpx2
import pytest

import proxy
from mvibe_auth.api import AuthConfig, AuthError, AuthFailure, AuthSnapshot, LoginCompleted


class Session:
    def __init__(self, *, signed_in=True, config_match=True):
        self.config = AuthConfig(enabled=True, audience='https://inference.example')
        self.state = dict(signed_in=signed_in, config_match=config_match)
        self.events = []
        self.failure = None

    async def status(self):
        self.events.append('status')
        return self.state

    async def start_login(self, force=False):
        self.events.append(('login', force))
        return SimpleNamespace(attempt_id='attempt', authorization_url='https://issuer.example/authorize')

    async def wait_login(self, attempt_id):
        self.events.append(('wait', attempt_id))
        return self.failure or LoginCompleted()

    async def resolve(self, url):
        self.events.append(('resolve', url))
        if self.failure:
            raise AuthError(self.failure)
        return AuthSnapshot({'Authorization': 'Bearer synthetic-id', 'X-Organization-Id': 'org'},
                            datetime.now(timezone.utc), 'revision', url)

    async def close(self):
        self.events.append('close')


def harness(session=None, *, response_status=200, fail_send=False, force=False):
    session = session or Session()
    requests = []
    options = []

    def client_factory(**kwargs):
        options.append(kwargs)
        async def upstream(request):
            requests.append(request)
            if fail_send:
                raise RuntimeError('secret upstream credential')
            return httpx2.Response(response_status, content=b'{"result":"synthetic"}')
        return httpx2.AsyncClient(transport=httpx2.MockTransport(upstream), follow_redirects=False)

    app = proxy.create_app(lambda: session, client_factory, force_login=force)
    return app, session, requests, options


@pytest.mark.anyio
@pytest.mark.parametrize(('method', 'path'), [('GET', 'models'), ('POST', 'chat/completions'), ('POST', 'embeddings')])
async def test_allowed_routes_use_only_current_public_snapshot(method, path):
    app, session, sent, options = harness()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            for _ in range(2):
                response = await client.request(method, '/v1/' + path, content=b'{"tools":[]}',
                    headers={'Authorization': 'Bearer caller-secret', 'X-Api-Key': 'caller-key', 'X-Organization-Id': 'caller-org'})
                assert response.status_code == 200
    assert len([event for event in session.events if isinstance(event, tuple) and event[0] == 'resolve']) == 2
    assert len(sent) == 2
    for request in sent:
        assert str(request.url) == 'https://inference.example/v1/' + path
        assert request.headers['authorization'] == 'Bearer synthetic-id'
        assert request.headers['x-organization-id'] == 'org'
        assert 'x-api-key' not in request.headers
        assert request.headers['content-type'] == 'application/json'
    assert options[0]['auth_config'] is session.config
    assert options[0]['follow_redirects'] is False
    assert session.events[-1] == 'close'


@pytest.mark.anyio
async def test_health_and_rejected_operations_do_not_resolve_or_send():
    app, session, sent, _ = harness()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            health = await client.get('/health')
            assert health.status_code == 200 and health.json() == {'status': 'ok'}
            for method, path, status in [('GET', 'embeddings', 404), ('POST', 'models', 404),
                    ('GET', 'responses', 404), ('POST', 'responses', 404), ('DELETE', 'models', 405),
                    ('PUT', 'chat/completions', 405), ('OPTIONS', 'models', 405), ('HEAD', 'models', 405)]:
                assert (await client.request(method, '/v1/' + path)).status_code == status
    assert not sent and session.events == ['status', 'close']


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['auth', 'config', 'connection'])
async def test_safe_pre_response_errors(failure):
    app, session, sent, _ = harness(fail_send=failure == 'connection')
    if failure == 'auth':
        session.failure = AuthFailure('rejected')
    async with app.router.lifespan_context(app):
        if failure == 'config':
            class InvalidConfig:
                @property
                def audience(self):
                    raise AuthError(AuthFailure('invalid'))
            session.config = InvalidConfig()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.get('/v1/models')
    assert response.status_code == (502 if failure == 'connection' else 401)
    assert 'secret' not in response.text
    if failure != 'connection':
        assert '--login' in response.text and not sent
    else:
        assert response.json()['error']['message'] == 'Upstream connection failed'


@pytest.mark.anyio
@pytest.mark.parametrize('status', [401, 403, 429, 500, 302])
async def test_upstream_application_status_is_preserved_without_replay(status):
    app, _, sent, _ = harness(response_status=status)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.get('/v1/models')
    assert response.status_code == status
    assert response.content == b'{"result":"synthetic"}'
    assert len(sent) == 1


@pytest.mark.parametrize('source', [None, 'existing-vibe', 'oidc'])
def test_cli_selects_native_by_default_and_oidc_only_explicitly(monkeypatch, source):
    selected = []
    served = []
    monkeypatch.setattr(proxy, 'create_app', lambda **kwargs: selected.append(kwargs) or 'app')
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *args, **kwargs: served.append((args, kwargs)))
    args = ['--login'] + (['--auth-source', source] if source else [])
    proxy.main(args)
    factory = selected[0].get('auth_factory', proxy._default_auth_factory)
    from mvibe_auth.existing_vibe import ExistingVibeSession
    if source == 'oidc':
        assert factory is proxy._oidc_auth_factory
    else:
        assert isinstance(factory(), ExistingVibeSession)
    assert selected[0]['force_login'] is True
    assert served[0][1]['host'] == '127.0.0.1'


@pytest.mark.parametrize(('host', 'expected', 'warns'), [
    (None, '127.0.0.1', False),
    ('127.0.0.1', '127.0.0.1', False),
    ('localhost', 'localhost', False),
    ('::1', '::1', False),
    ('0.0.0.0', '0.0.0.0', True),
])
def test_cli_host_binds_requested_address_and_warns_off_loopback(monkeypatch, capsys, host, expected, warns):
    served = []
    monkeypatch.setattr(proxy, 'create_app', lambda **kwargs: 'app')
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *args, **kwargs: served.append((args, kwargs)))
    proxy.main([] if host is None else ['--host', host])
    assert served[0][1]['host'] == expected
    assert ('WARNING' in capsys.readouterr().err) is warns


def test_cli_rejects_invalid_host(monkeypatch):
    monkeypatch.setattr(proxy, 'create_app', lambda **kwargs: 'app')
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *args, **kwargs: None)
    with pytest.raises(SystemExit):
        proxy.main(['--host', 'not a host'])


@pytest.mark.anyio
async def test_swagger_and_openapi_document_the_supported_operations():
    app, session, sent, _ = harness()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            docs = await client.get('/docs')
            schema = await client.get('/openapi.json')
    assert docs.status_code == 200 and 'swagger' in docs.text.lower()
    assert schema.status_code == 200
    assert {'/health', '/v1/models', '/v1/chat/completions', '/v1/embeddings'} <= set(schema.json()['paths'])
    assert not sent and session.events == ['status', 'close']


@pytest.mark.parametrize('setting,enabled', [(None, True), ('false', False), ('true', True)])
def test_explicit_oidc_factory_preserves_enablement_override(monkeypatch, setting, enabled):
    if setting is None:
        monkeypatch.delenv('VIBE_CLI_OIDC_ENABLED', raising=False)
    else:
        monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', setting)
    monkeypatch.setattr(proxy, 'AuthSession', lambda config: config)
    assert proxy._oidc_auth_factory().enabled is enabled


@pytest.mark.anyio
@pytest.mark.parametrize('app_kind', ['factory', 'module'])
async def test_default_native_missing_never_falls_back_or_opens_browser(monkeypatch, app_kind):
    from mvibe_auth import existing_vibe
    monkeypatch.setattr(existing_vibe, '_read_native_credential', lambda: None)
    monkeypatch.setattr(proxy, 'AuthSession', lambda *_: pytest.fail('experimental OIDC selected'))
    monkeypatch.setattr(proxy.webbrowser, 'open', lambda *_: pytest.fail('browser opened'))
    app = proxy.create_app() if app_kind == 'factory' else proxy.app
    with pytest.raises(AuthError) as error:
        async with app.router.lifespan_context(app):
            pytest.fail('missing native credential must stop startup')
    assert error.value.failure.code == 'missing'


@pytest.mark.anyio
@pytest.mark.parametrize('force', [False, True])
async def test_default_native_startup_and_requests_share_fresh_session(monkeypatch, force):
    from mvibe_auth import existing_vibe
    values = iter(['startup', 'validated', 'request-one', 'request-two'] if force
                  else ['startup', 'request-one', 'request-two'])
    reads = []
    def read():
        value = next(values)
        reads.append(value)
        return value
    monkeypatch.setattr(existing_vibe, '_read_native_credential', read)
    monkeypatch.setattr(proxy, 'AuthSession', lambda *_: pytest.fail('experimental OIDC selected'))
    monkeypatch.setattr(proxy.webbrowser, 'open', lambda *_: pytest.fail('browser opened'))
    sent = []
    def client_factory(**kwargs):
        def upstream(request):
            sent.append(request)
            return httpx2.Response(200, json={'data': []})
        return httpx2.AsyncClient(transport=httpx2.MockTransport(upstream))
    app = proxy.create_app(client_factory=client_factory, force_login=force)
    async with app.router.lifespan_context(app):
        session = app.state.auth
        assert isinstance(session, existing_vibe.ExistingVibeSession)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            for _ in range(2):
                assert (await client.get('/v1/models')).status_code == 200
                assert app.state.auth is session
    assert reads == (['startup', 'validated', 'request-one', 'request-two'] if force
                     else ['startup', 'request-one', 'request-two'])
    assert [request.headers['authorization'] for request in sent] == [
        'Bearer request-one', 'Bearer request-two']
    assert session._closed
