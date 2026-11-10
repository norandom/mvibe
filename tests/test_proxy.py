"""Offline public-factory route contract; every external boundary is injected."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import httpx2
import pytest

import proxy
from mvibe_auth.api import AuthConfig, AuthError, AuthFailure, AuthSnapshot, LoginCompleted
from mvibe_proxy.ratelimit import (ENV_BACKOFF_CAP_S, ENV_MAX_QUEUE, ENV_MAX_WAIT_S,
                                   ENV_MODEL_RPS, ENV_RPS, RateLimitConfig, UpstreamGate)


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


def harness(session=None, *, response_status=200, fail_send=False, force=False, content=b'{"result":"synthetic"}',
            **app_options):
    session = session or Session()
    requests = []
    options = []

    def client_factory(**kwargs):
        options.append(kwargs)
        async def upstream(request):
            requests.append(request)
            if fail_send:
                raise RuntimeError('secret upstream credential')
            return httpx2.Response(response_status, content=content)
        return httpx2.AsyncClient(transport=httpx2.MockTransport(upstream), follow_redirects=False)

    app = proxy.create_app(lambda: session, client_factory, force_login=force, **app_options)
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
@pytest.mark.parametrize('stream', [False, True])
async def test_chat_completions_forwards_reasoning_effort_and_extra_fields(stream):
    app, session, sent, _ = harness()
    body = json.dumps({'model': 'mistral-large-4', 'reasoning_effort': 'high',
                       'messages': [{'role': 'user', 'content': 'hi'}],
                       'stream': stream, 'custom_field': {'kept': True}}).encode()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.post('/v1/chat/completions', content=body)
    assert response.status_code == 200
    forwarded = json.loads(sent[0].content)
    assert forwarded['reasoning_effort'] == 'high'
    assert forwarded['custom_field'] == {'kept': True}
    assert forwarded['model'] == 'mistral-large-4' and forwarded['stream'] is stream
    # Response path stays byte-exact passthrough.
    assert response.content == b'{"result":"synthetic"}'


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


_RATE_LIMIT_ENV = (ENV_RPS, ENV_MODEL_RPS, ENV_MAX_WAIT_S, ENV_MAX_QUEUE, ENV_BACKOFF_CAP_S)


def clear_rate_limit_env(monkeypatch):
    for name in _RATE_LIMIT_ENV:
        monkeypatch.delenv(name, raising=False)


def recording_gate_factory():
    built = []
    def gate_factory(config):
        gate = UpstreamGate(config)
        built.append((config, gate))
        return gate
    return gate_factory, built


@pytest.mark.anyio
async def test_lifespan_builds_the_injected_gate_with_reactive_only_defaults(monkeypatch):
    # create_app never reads the environment; main() owns MVIBE_RATE_LIMIT_*.
    monkeypatch.setenv(ENV_RPS, '0.5')
    monkeypatch.setenv(ENV_MAX_WAIT_S, '1')
    gate_factory, built = recording_gate_factory()
    app, _, _, _ = harness(gate_factory=gate_factory)
    assert app.state.rate_gate is None and not built
    async with app.router.lifespan_context(app):
        (config, gate), = built
        assert config.default_rps is None and not config.model_rps
        assert (config.max_wait_s, config.max_queue, config.backoff_cap_s) == (120, 64, 30)
        assert app.state.rate_gate is gate
        assert app.state.rate_gate.held == 0
        assert app.state.rate_limit_config is config


@pytest.mark.anyio
async def test_lifespan_hands_the_given_rate_limit_config_to_the_gate_factory():
    given = RateLimitConfig(default_rps=0.5, model_rps={}, max_wait_s=5, max_queue=2, backoff_cap_s=30)
    gate_factory, built = recording_gate_factory()
    app, _, _, _ = harness(rate_limit_config=given, gate_factory=gate_factory)
    async with app.router.lifespan_context(app):
        (config, gate), = built
        assert config is given
        assert app.state.rate_gate is gate and app.state.rate_limit_config is given


@pytest.mark.anyio
async def test_default_gate_factory_builds_a_real_reactive_only_gate():
    app, _, _, _ = harness()
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.rate_gate, UpstreamGate)
        assert app.state.rate_gate.held == 0
        assert app.state.rate_limit_config.default_rps is None
        assert not app.state.rate_limit_config.model_rps


def test_default_gate_warns_once_per_hold_naming_the_model_and_wait(monkeypatch, caplog):
    constructed = []
    monkeypatch.setattr(proxy, 'UpstreamGate',
                        lambda config, **options: constructed.append((config, options)) or 'gate')
    config = RateLimitConfig()
    assert proxy._default_gate(config) == 'gate'
    (seen, options), = constructed
    # Only the warning is supplied; clocks, sleep and jitter keep their defaults.
    assert seen is config and set(options) == {'warn'}
    with caplog.at_level(logging.WARNING, logger='mvibe.proxy'):
        options['warn']('mistral-large-4', 2.0)
    assert [(record.name, record.levelno, record.getMessage()) for record in caplog.records] == [
        ('mvibe.proxy', logging.WARNING, 'proxy_rate_limit_hold model=mistral-large-4 wait_s=2.0')]


@pytest.mark.parametrize(('name', 'value'), [
    (ENV_RPS, 'abc'), (ENV_RPS, '0'), (ENV_MODEL_RPS, 'mistral-large-4'),
    (ENV_MAX_WAIT_S, '-1'), (ENV_MAX_QUEUE, '1.5'), (ENV_BACKOFF_CAP_S, 'nan')])
def test_cli_invalid_rate_limit_setting_refuses_to_start_naming_it(monkeypatch, capsys, name, value):
    clear_rate_limit_env(monkeypatch)
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(proxy, 'create_app', lambda **kwargs: pytest.fail('app built'))
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *args, **kwargs: pytest.fail('served'))
    with pytest.raises(SystemExit) as error:
        proxy.main([])
    assert error.value.code == 2
    assert f'error: {name} ' in capsys.readouterr().err


@pytest.mark.parametrize('source', [None, 'oidc'])
def test_cli_passes_the_validated_rate_limit_config_to_the_selected_app(monkeypatch, source):
    clear_rate_limit_env(monkeypatch)
    monkeypatch.setenv(ENV_RPS, '0.5')
    monkeypatch.setenv(ENV_MODEL_RPS, 'codestral-latest=2')
    monkeypatch.setenv(ENV_MAX_WAIT_S, '30')
    monkeypatch.setenv(ENV_MAX_QUEUE, '8')
    selected = []
    served = []
    monkeypatch.setattr(proxy, 'create_app', lambda **kwargs: selected.append(kwargs) or 'app')
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *args, **kwargs: served.append(args))
    proxy.main(['--auth-source', source] if source else [])
    (options,) = selected
    assert (options.get('auth_factory') is proxy._oidc_auth_factory) is (source == 'oidc')
    config = options['rate_limit_config']
    assert config.default_rps == 0.5 and dict(config.model_rps) == {'codestral-latest': 2.0}
    assert (config.max_wait_s, config.max_queue, config.backoff_cap_s) == (30, 8, 30)
    assert served == [('app',)]


def test_cli_without_rate_limit_settings_serves_reactive_only(monkeypatch):
    clear_rate_limit_env(monkeypatch)
    real_create_app = proxy.create_app
    selected = []
    served = []
    monkeypatch.setattr(proxy, 'create_app',
                        lambda **kwargs: selected.append(kwargs) or real_create_app(**kwargs))
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda app, **kwargs: served.append(app))
    proxy.main([])
    config = selected[0]['rate_limit_config']
    assert config.default_rps is None and not config.model_rps
    assert (config.max_wait_s, config.max_queue, config.backoff_cap_s) == (120, 64, 30)
    # The gate is built by the lifespan when uvicorn starts serving the app.
    assert len(served) == 1 and served[0].state.rate_gate is None


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


async def raw_asgi(app, method, path, *, body=b'', headers=(), events, receive=None, scopes=None):
    """One raw ASGI request; `events` records when the app takes the request body.

    `receive` replaces the default single-chunk body delivery; `scopes` collects the scope."""
    messages = []
    delivered = False
    async def deliver():
        nonlocal delivered
        if not delivered:
            delivered = True
            events.append('body')
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await asyncio.Event().wait()
    async def send(message):
        messages.append(message)
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
             'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
             'query_string': b'', 'root_path': '', 'headers': list(headers),
             'client': ('local', 1), 'server': ('local', 80)}
    if scopes is not None:
        scopes.append(scope)
    await app(scope, receive or deliver, send)
    return messages


MODELS_LIST = {'object': 'list', 'data': [{'id': 'mistral-large-4', 'object': 'model'}]}


@pytest.mark.anyio
@pytest.mark.parametrize(('method', 'path', 'headers', 'body'), [
    ('POST', '/v1/chat/completions', [], b' {"tools": [ ]}\n\x00'),
    ('GET', '/v1/models', [(b'anthropic-version', b'2023-06-01')], b''),
    ('GET', '/api/tags', [], b''),
])
async def test_routes_read_the_body_before_acquiring(method, path, headers, body):
    chat = path == '/v1/chat/completions'
    upstream_body = b'{"result":"synthetic"}' if chat else json.dumps(MODELS_LIST).encode()
    app, session, sent, _ = harness(content=upstream_body)
    async with app.router.lifespan_context(app):
        messages = await asyncio.wait_for(
            raw_asgi(app, method, path, body=body, headers=headers, events=session.events), 1)
    resolves = [index for index, event in enumerate(session.events)
                if isinstance(event, tuple) and event[0] == 'resolve']
    # The route consumed the body before acquisition (and its disconnect watcher) began.
    assert len(resolves) == 1 and session.events.index('body') < resolves[0]
    assert messages[0]['status'] == 200 and len(sent) == 1
    assert sent[0].content == body
    received = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
    if chat:
        assert received == upstream_body
    elif path == '/v1/models':
        translated = json.loads(received)
        assert [m['id'] for m in translated['data']] == ['mistral-large-4'] and translated['has_more'] is False
    else:
        assert [m['name'] for m in json.loads(received)['models']] == ['mistral-large-4']


def disconnecting(chunks, events):
    """receive() handing over `chunks` (more_body=True), then reporting the client gone."""
    pending = list(chunks)
    async def receive():
        if pending:
            events.append('body')
            return {'type': 'http.request', 'body': pending.pop(0), 'more_body': True}
        events.append('disconnect')
        return {'type': 'http.disconnect'}
    return receive


@pytest.mark.anyio
@pytest.mark.parametrize(('method', 'path', 'headers'), [
    ('POST', '/v1/chat/completions', []),
    ('POST', '/api/chat', []),
    ('GET', '/v1/models', [(b'anthropic-version', b'2023-06-01')]),
    ('GET', '/api/tags', []),
])
@pytest.mark.parametrize('chunks', [(), (b'{"model": "mistral-',)], ids=['before-body', 'mid-body'])
async def test_disconnect_during_body_read_is_cancelled_and_never_sent(method, path, headers, chunks):
    app, session, sent, _ = harness()
    scopes = []
    async with app.router.lifespan_context(app):
        # Returns normally: an ordinary client disconnect never escapes the app.
        messages = await asyncio.wait_for(raw_asgi(
            app, method, path, headers=headers, events=session.events,
            receive=disconnecting(chunks, session.events), scopes=scopes), 1)
    assert 'disconnect' in session.events
    assert not sent
    assert not [event for event in session.events if isinstance(event, tuple) and event[0] == 'resolve']
    assert scopes[0]['mvibe.diagnostic'].outcome == 'cancelled'
    # The client is gone: at most an empty 204, whose status only lands in the diagnostic.
    starts = [m for m in messages if m['type'] == 'http.response.start']
    assert not starts or [m['status'] for m in starts] == [204]
    assert b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body') == b''
