"""Offline public-factory route contract; every external boundary is injected."""
import asyncio
import json
import logging
import shlex
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import httpx2
import pytest

import proxy
from fakes import Clock
from mvibe_auth.api import AuthConfig, AuthError, AuthFailure, AuthSnapshot, LoginCompleted
from mvibe_proxy import diagnostics
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
            upstream=None, **app_options):
    """An app on fakes; `upstream(request)`, when given, answers in place of response_status/content."""
    session = session or Session()
    requests = []
    options = []

    def client_factory(**kwargs):
        options.append(kwargs)
        async def respond(request):
            requests.append(request)
            if fail_send:
                raise RuntimeError('secret upstream credential')
            if upstream is not None:
                return upstream(request)
            return httpx2.Response(response_status, content=content)
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), follow_redirects=False)

    app = proxy.create_app(lambda: session, client_factory, force_login=force, **app_options)
    return app, session, requests, options


def clocked_gate(clock, *, sleep=None, warn=None):
    """A gate_factory for the real gate on the fake `clock`.

    Its waits advance the clock (or run `sleep`), and its jitter is fixed at 1.0,
    so a hold without Retry-After lasts exactly the backoff (1 s for the first)."""
    def gate_factory(config):
        return UpstreamGate(config, clock=clock.time, wall_clock=clock.time,
                            sleep=sleep or clock.sleep, random=lambda: 0.5, warn=warn)
    return gate_factory


def answers(clock, *script, latency=0.0, sent_at=None):
    """An upstream answering the `(status, headers, content)` entries of `script` in
    order, the last one repeating. Each answer takes `latency` seconds of `clock`;
    `sent_at` collects the clock reading as each request reaches the upstream."""
    pending = list(script)
    def upstream(request):
        if sent_at is not None:
            sent_at.append(clock.time())
        clock.advance(latency)
        status, headers, content = pending.pop(0) if len(pending) > 1 else pending[0]
        return httpx2.Response(status, headers=headers, content=content)
    return upstream


# Half a second of maximum wait: the first hold an upstream 429 asks for (at
# least 1 s) ends past the deadline even when the answer takes no clock time,
# so the gate answers the upstream 429 itself after exactly one send.
SHORT_WAIT = RateLimitConfig(max_wait_s=0.5)


def resolves(session):
    return [event for event in session.events if isinstance(event, tuple) and event[0] == 'resolve']


def body_of(messages):
    return b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')


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
    # Through a gate that cannot wait: an upstream 429 is not re-sent but answered
    # by the gate with the upstream body and a Retry-After; every other status
    # passes the gate unchanged.
    clock = Clock()
    app, _, sent, _ = harness(
        upstream=answers(clock, (status, {}, b'{"result":"synthetic"}')),
        rate_limit_config=SHORT_WAIT, gate_factory=clocked_gate(clock))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await asyncio.wait_for(client.get('/v1/models'), 5)
    assert response.status_code == status
    assert response.content == b'{"result":"synthetic"}'
    assert len(sent) == 1
    assert response.headers.get('retry-after') == ('1' if status == 429 else None)


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
        options['warn']('my model=forged wait_s=0.0', 1.0)
    # The key is quoted, so a space or '=' in a model name cannot add a token.
    messages = [record.getMessage() for record in caplog.records]
    assert [(record.name, record.levelno) for record in caplog.records] == [
        ('mvibe.proxy', logging.WARNING)] * 2
    assert messages == ["proxy_rate_limit_hold model='mistral-large-4' wait_s=2.0",
                        "proxy_rate_limit_hold model='my model=forged wait_s=0.0' wait_s=1.0"]
    assert [shlex.split(message) for message in messages] == [
        ['proxy_rate_limit_hold', 'model=mistral-large-4', 'wait_s=2.0'],
        ['proxy_rate_limit_hold', 'model=my model=forged wait_s=0.0', 'wait_s=1.0']]


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


async def raw_asgi(app, method, path, *, body=b'', headers=(), events, receive=None, scopes=None,
                   messages=None):
    """One raw ASGI request; `events` records when the app takes the request body.

    `receive` replaces the default single-chunk body delivery; `scopes` collects the scope;
    `messages`, when given, receives each sent message as it is sent."""
    messages = [] if messages is None else messages
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


# --- the rate-limit gate on every upstream request (upstream-rate-limit-queue 2.4) ---
# Requirements 2.1, 2.2, 2.5, 2.8, 3.1, 3.5, 6.2: make_acquire parses the body
# once for the gate key, and the gate's attempt resolves, prepares (first
# attempt only) and sends.

RATE_LIMIT_SETTINGS = ('MVIBE_RATE_LIMIT_MAX_WAIT_S', 'MVIBE_RATE_LIMIT_MAX_QUEUE', 'MVIBE_RATE_LIMIT_RPS')
TOOL_CHAT = {'model': 'mistral-large-4', 'messages': [
    {'role': 'user', 'content': 'read it'},
    {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'call-1', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]},
    {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'synthetic file content'}]}


class CountingSession(Session):
    """Each resolve hands out a new credential, so every attempt can be told apart."""

    async def resolve(self, url):
        snapshot = await super().resolve(url)
        return AuthSnapshot({**snapshot.headers, 'Authorization': f'Bearer synthetic-{len(resolves(self))}'},
                            snapshot.expires_at, snapshot.revision, snapshot.target)


class RecordingPlan:
    """Stands in for AccountPlanResolver and records the snapshots it was asked about."""

    def __init__(self):
        self.snapshots = []

    async def resolve(self, snapshot):
        self.snapshots.append(snapshot)
        return 'Pro'

    async def close(self):
        pass


class CountingShunt:
    """Stands in for EconShunt: rewrites tool results in place and counts its calls."""

    def __init__(self):
        self.applied = 0

    def apply(self, payload, diagnostic=None):
        self.applied += 1
        for message in payload['messages']:
            if message.get('role') == 'tool':
                message['content'] = 'summarized'

    async def shutdown(self):
        pass


@pytest.mark.anyio
async def test_a_429_is_held_then_resent_with_a_fresh_snapshot_and_the_first_prepared_body():
    clock = Clock()
    sent_at = []
    plan, shunt = RecordingPlan(), CountingShunt()
    app, session, sent, _ = harness(
        CountingSession(),
        upstream=answers(clock, (429, {'x-ratelimit-remaining-req-minute': '0'}, b'{"message":"rate limited"}'),
                         (200, {'content-type': 'application/json'}, b'{"result":"synthetic"}'),
                         sent_at=sent_at),
        account_plan_factory=lambda config: plan, gate_factory=clocked_gate(clock))
    scopes = []
    t0 = clock.time()
    async with app.router.lifespan_context(app):
        app.state.econ = shunt
        messages = await asyncio.wait_for(raw_asgi(
            app, 'POST', '/v1/chat/completions', body=json.dumps(TOOL_CHAT).encode(),
            events=session.events, scopes=scopes), 5)
        assert app.state.rate_gate.held == 0
    # The client sees only the accepted answer.
    assert messages[0]['type'] == 'http.response.start' and messages[0]['status'] == 200
    assert body_of(messages) == b'{"result":"synthetic"}'
    # Held for the 1 s backoff, then the identical bytes re-sent under a new credential.
    assert sent_at == [t0, t0 + 1] and len(sent) == 2
    assert sent[0].content == sent[1].content
    assert [request.headers['authorization'] for request in sent] == [
        'Bearer synthetic-1', 'Bearer synthetic-2']
    assert len(resolves(session)) == 2
    # Attribution and economics ran once, with the first attempt's snapshot.
    assert [snapshot.headers['Authorization'] for snapshot in plan.snapshots] == ['Bearer synthetic-1']
    assert shunt.applied == 1
    forwarded = json.loads(sent[1].content)
    assert forwarded['metadata']['user_plan'] == 'Pro'
    assert forwarded['messages'][-1]['content'] == 'summarized'
    diagnostic = scopes[0]['mvibe.diagnostic']
    assert (diagnostic.rate_limit.rejections, diagnostic.rate_limit.queued_ms) == (1, 1000.0)
    assert not diagnostic.rate_limit.exhausted
    assert (diagnostic.outcome, diagnostic.model) == ('completed', 'mistral-large-4')


@pytest.mark.anyio
async def test_a_429_beyond_the_maximum_wait_reaches_the_client_with_its_body_and_guidance(capsys):
    clock = Clock()
    rejection = b'{"message":"Requests rate limit exceeded","detail":"caf\xc3\xa9"}'
    app, _, sent, _ = harness(
        upstream=answers(clock, (429, {'content-type': 'application/json', 'retry-after': '5',
                                       'x-ratelimit-limit-req-minute': '5'}, rejection)),
        rate_limit_config=RateLimitConfig(max_wait_s=1.0), gate_factory=clocked_gate(clock))
    diagnostics.configure_stdout()
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                response = await asyncio.wait_for(client.post(
                    '/v1/chat/completions', content=b'{"model":"mistral-large-4"}'), 5)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    finally:
        diagnostics.disable_stdout()
    # Retry-After 5 s cannot be honoured within 1 s: one send, then the upstream 429 itself.
    assert len(sent) == 1
    assert response.status_code == 429 and response.content == rejection
    assert response.headers['retry-after'] == '5'
    assert response.headers['x-ratelimit-limit-req-minute'] == '5'
    assert response.headers['content-type'] == 'application/json'
    row = rows[-1]
    assert (row['outcome'], row['phase'], row['status']) == ('rate_limited', 'upstream', 429)
    assert all(name in row['hint'] for name in RATE_LIMIT_SETTINGS)
    assert (row['rate_limit_rejections'], row['rate_limit_headers']) == (
        1, {'x-ratelimit-limit-req-minute': '5'})
    assert row['request_id'] == response.headers['x-request-id']


@pytest.mark.anyio
async def test_parallel_chat_requests_are_sent_at_the_configured_rate():
    clock = Clock()
    sent_at = []
    app, _, sent, _ = harness(
        upstream=answers(clock, (200, {}, b'{"result":"synthetic"}'), sent_at=sent_at),
        rate_limit_config=RateLimitConfig(default_rps=0.5), gate_factory=clocked_gate(clock))
    t0 = clock.time()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            responses = await asyncio.wait_for(asyncio.gather(*(
                client.post('/v1/chat/completions', content=b'{"model":"mistral-large-4"}')
                for _ in range(3))), 5)
    assert [response.status_code for response in responses] == [200, 200, 200]
    assert all(response.content == b'{"result":"synthetic"}' for response in responses)
    assert sent_at == [t0, t0 + 2, t0 + 4] and len(sent) == 3


@pytest.mark.anyio
@pytest.mark.parametrize(('path', 'headers'), [
    ('/v1/models', {}), ('/v1/models', {'anthropic-version': '2023-06-01'}), ('/api/tags', {})])
async def test_model_list_requests_are_never_paced(path, headers):
    clock = Clock()
    sent_at = []
    app, _, sent, _ = harness(
        upstream=answers(clock, (200, {}, json.dumps(MODELS_LIST).encode()), sent_at=sent_at),
        rate_limit_config=RateLimitConfig(default_rps=0.5), gate_factory=clocked_gate(clock))
    t0 = clock.time()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            for _ in range(2):
                response = await asyncio.wait_for(client.get(path, headers=headers), 5)
                assert response.status_code == 200
    assert sent_at == [t0, t0] and len(sent) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(('method', 'path', 'body'), [
    ('POST', '/v1/chat/completions', b' {"tools": [ ]}\n\x00'),
    ('POST', '/v1/embeddings', b'{"model": "mistral-embed", "input": ["x"]}'),
    ('GET', '/v1/models', b''),
])
async def test_first_attempt_success_is_forwarded_byte_exact_without_queueing(method, path, body):
    clock = Clock()
    payload = b'{"result":"synthetic","bytes":"caf\xc3\xa9"}\n'
    app, session, sent, _ = harness(
        upstream=answers(clock, (200, {'content-type': 'application/json', 'retry-after': '1',
                                       'x-ratelimit-remaining-req-minute': '4'}, payload)),
        gate_factory=clocked_gate(clock))
    scopes = []
    async with app.router.lifespan_context(app):
        messages = await asyncio.wait_for(raw_asgi(
            app, method, path, body=body, events=session.events, scopes=scopes), 5)
    assert messages[0]['status'] == 200
    # Today's header set: no rate-limit guidance on a success.
    assert [header for header in messages[0]['headers'] if header[0] != b'x-request-id'] == [
        (b'content-type', b'application/json'), (b'cache-control', b'no-cache')]
    assert body_of(messages) == payload
    assert len(sent) == 1 and sent[0].content == body
    record = scopes[0]['mvibe.diagnostic'].rate_limit
    assert (record.queued_ms, record.rejections, record.exhausted) == (0.0, 0, False)


@pytest.mark.anyio
async def test_a_held_request_sends_the_client_nothing_until_the_upstream_accepts():
    clock = Clock()
    holding, release = asyncio.Event(), asyncio.Event()

    async def held_sleep(seconds):
        holding.set()
        await release.wait()
        clock.advance(seconds)

    app, session, sent, _ = harness(
        upstream=answers(clock, (429, {'retry-after': '2'}, b'{"message":"rate limited"}'),
                         (200, {}, b'{"result":"synthetic"}')),
        gate_factory=clocked_gate(clock, sleep=held_sleep))
    messages = []
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(raw_asgi(
            app, 'POST', '/v1/chat/completions', body=b'{"model":"mistral-large-4"}',
            events=session.events, messages=messages))
        try:
            await asyncio.wait_for(holding.wait(), 5)
            await asyncio.sleep(0)
            # Held after the 429: nothing has reached the client, not even a status.
            assert messages == [] and len(sent) == 1 and not task.done()
            release.set()
            await asyncio.wait_for(task, 5)
        finally:
            task.cancel()
    assert messages[0]['type'] == 'http.response.start' and messages[0]['status'] == 200
    assert body_of(messages) == b'{"result":"synthetic"}' and len(sent) == 2


@pytest.mark.anyio
async def test_embeddings_are_keyed_by_their_model_and_paced_like_chat(capsys):
    clock = Clock()
    sent_at = []
    app, _, sent, _ = harness(
        upstream=answers(clock, (200, {}, b'{"data":[]}'), sent_at=sent_at),
        rate_limit_config=RateLimitConfig(default_rps=0.5), gate_factory=clocked_gate(clock))
    t0 = clock.time()
    diagnostics.configure_stdout()
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                responses = await asyncio.wait_for(asyncio.gather(*(
                    client.post('/v1/embeddings', content=b'{"model":"mistral-embed","input":["x"]}')
                    for _ in range(2))), 5)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    finally:
        diagnostics.disable_stdout()
    assert [response.status_code for response in responses] == [200, 200]
    assert sent_at == [t0, t0 + 2] and len(sent) == 2
    assert [row['model'] for row in rows] == ['mistral-embed', 'mistral-embed']
    assert sorted(row['queued_ms'] for row in rows) == [0.0, 2000.0]


@pytest.mark.parametrize(('path', 'model', 'key'), [
    ('models', 'mistral-large-4', 'models'),
    ('models', None, 'models'),
    ('chat/completions', 'mistral-large-4', 'mistral-large-4'),
    ('embeddings', 'mistral-embed', 'mistral-embed'),
    ('chat/completions', None, 'default'),
    ('embeddings', None, 'default'),
    ('chat/completions', 'evil\nproxy_rate_limit_hold model=forged\r\x1b[2J\x00',
     'evilproxy_rate_limit_hold model=forged[2J'),
    ('chat/completions', '\n\t\x7f', 'default'),
    ('chat/completions', 'm' * 500, 'm' * 128),
    ('embeddings', 'modèle -1', 'modèle-1'),
])
def test_gate_key_is_models_the_printable_model_or_default(path, model, key):
    assert proxy.key_for(path, model) == key


@pytest.mark.anyio
async def test_a_hold_warning_names_the_sanitized_model_on_one_line(caplog):
    clock = Clock()
    app, _, sent, _ = harness(
        upstream=answers(clock, (429, {}, b'{}'), (200, {}, b'{"result":"synthetic"}')),
        gate_factory=clocked_gate(clock, warn=proxy._warn_rate_limit_hold))
    model = 'evil\nproxy_rate_limit_hold model=forged wait_s=0.0\r\n'
    with caplog.at_level(logging.WARNING, logger='mvibe.proxy'):
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                response = await asyncio.wait_for(client.post(
                    '/v1/chat/completions', content=json.dumps({'model': model}).encode()), 5)
    assert response.status_code == 200 and len(sent) == 2
    messages = [record.getMessage() for record in caplog.records if record.name == 'mvibe.proxy']
    assert messages == [
        "proxy_rate_limit_hold model='evilproxy_rate_limit_hold model=forged wait_s=0.0' wait_s=1.0"]
    # One line and three tokens: the forged text stays inside the quoted model.
    assert shlex.split(messages[0]) == [
        'proxy_rate_limit_hold', 'model=evilproxy_rate_limit_hold model=forged wait_s=0.0',
        'wait_s=1.0']


@pytest.mark.anyio
async def test_a_request_arriving_during_a_hold_waits_behind_it_for_the_same_model():
    # Req 2.2: while a 429 holds a model, a later request for that model is not
    # sent before the hold ends, and then only after the held request's re-send.
    clock = Clock()
    holding, release = asyncio.Event(), asyncio.Event()

    async def held_sleep(seconds):
        holding.set()
        await release.wait()
        clock.advance(seconds)

    sent_at = []
    app, session, sent, _ = harness(
        upstream=answers(clock, (429, {}, b'{"message":"rate limited"}'),
                         (200, {}, b'{"result":"synthetic"}'), sent_at=sent_at),
        gate_factory=clocked_gate(clock, sleep=held_sleep))
    first, second = (json.dumps({'model': 'mistral-large-4', 'messages': [
        {'role': 'user', 'content': text}]}).encode() for text in ('first', 'second'))
    messages, scopes = ([], []), []
    t0 = clock.time()
    async with app.router.lifespan_context(app):
        gate = app.state.rate_gate
        a = asyncio.create_task(raw_asgi(app, 'POST', '/v1/chat/completions', body=first,
                                         events=session.events, scopes=scopes,
                                         messages=messages[0]))
        try:
            await asyncio.wait_for(holding.wait(), 5)      # the first was rejected: held
            b = asyncio.create_task(raw_asgi(app, 'POST', '/v1/chat/completions', body=second,
                                             events=session.events, scopes=scopes,
                                             messages=messages[1]))
            try:
                async def second_is_queued():
                    while gate.held < 2 and len(sent) < 2:
                        await asyncio.sleep(0)
                await asyncio.wait_for(second_is_queued(), 5)
                for _ in range(10):
                    await asyncio.sleep(0)
                # Both wait in the gate; the upstream has seen only the first send.
                assert len(sent) == 1 and messages == ([], []) and not b.done()
                release.set()
                await asyncio.wait_for(asyncio.gather(a, b), 5)
            finally:
                b.cancel()
        finally:
            a.cancel()
        assert gate.held == 0
    # The held request is re-sent first, identical, and the later one after it.
    assert [json.loads(request.content)['messages'][0]['content'] for request in sent] == [
        'first', 'first', 'second']
    assert sent[0].content == sent[1].content
    assert sent_at == [t0, t0 + 1, t0 + 1]
    for answered in messages:
        assert answered[0]['type'] == 'http.response.start' and answered[0]['status'] == 200
        assert body_of(answered) == b'{"result":"synthetic"}'
    held_first, held_second = (scope['mvibe.diagnostic'].rate_limit for scope in scopes)
    assert (held_first.rejections, held_first.queued_ms) == (1, 1000.0)
    assert (held_second.rejections, held_second.queued_ms) == (0, 1000.0)
    assert not held_first.exhausted and not held_second.exhausted


# --- observed upstream usage on every inference route (proxy-stats-propagation 3.2)

def _diagnostic_rows(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.strip().splitlines() if line.strip()]


@pytest.mark.anyio
@pytest.mark.parametrize('path, request_body, upstream_body, content_type, expected', [
    ('chat/completions',
     b'{"model":"mistral-large-latest","messages":[{"role":"user","content":"hi"}]}',
     b'{"choices":[{"message":{"role":"assistant","content":"ok"}}],"usage":{"prompt_tokens":11,"completion_tokens":5,"total_tokens":16}}',
     'application/json', ('mistral-large-latest', 11, 5)),
    ('chat/completions',
     b'{"model":"mistral-large-latest","messages":[],"stream":true}',
     b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
     b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
     b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
     b'data: [DONE]\n\n',
     'text/event-stream', ('mistral-large-latest', 3, 2)),
    ('embeddings',
     b'{"model":"mistral-embed","input":"vector me"}',
     b'{"data":[{"embedding":[0.1,0.2],"index":0}],"usage":{"prompt_tokens":8,"total_tokens":8}}',
     'application/json', ('mistral-embed', 8, 0)),
])
async def test_inference_routes_record_upstream_usage_without_altering_bytes(path, request_body, upstream_body, content_type, expected, capsys):
    from mvibe_proxy import diagnostics
    app, session, sent, _ = harness(upstream=lambda request: httpx2.Response(
        200, content=upstream_body, headers={'content-type': content_type}))
    diagnostics.configure_stdout()
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                response = await client.post('/v1/' + path, content=request_body,
                                             headers={'content-type': 'application/json'})
        assert response.status_code == 200
        assert response.content == upstream_body  # the tap never alters forwarded bytes
        rows = _diagnostic_rows(capsys)
    finally:
        diagnostics.disable_stdout()
    (row,) = [r for r in rows if r.get('event') == 'proxy_request' or 'outcome' in r]
    model, prompt, completion = expected
    assert row['model'] == model
    assert (row['usage_prompt_tokens'], row['usage_completion_tokens']) == (prompt, completion)
    assert len(sent) == 1


@pytest.mark.anyio
async def test_model_list_responses_are_not_observed_for_usage(capsys):
    from mvibe_proxy import diagnostics
    app, session, sent, _ = harness(upstream=lambda request: httpx2.Response(
        200, content=b'{"object":"list","data":[{"id":"m"}],"usage":{"prompt_tokens":99,"completion_tokens":1}}',
        headers={'content-type': 'application/json'}))
    diagnostics.configure_stdout()
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                response = await client.get('/v1/models')
        assert response.status_code == 200
        rows = _diagnostic_rows(capsys)
    finally:
        diagnostics.disable_stdout()
    (row,) = [r for r in rows if 'outcome' in r]
    assert 'usage_prompt_tokens' not in row and 'usage_completion_tokens' not in row


@pytest.mark.anyio
async def test_a_response_without_usage_records_nothing(capsys):
    from mvibe_proxy import diagnostics
    app, session, sent, _ = harness(upstream=lambda request: httpx2.Response(
        200, content=b'{"choices":[{"message":{"content":"no usage here"}}]}', headers={'content-type': 'application/json'}))
    diagnostics.configure_stdout()
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                response = await client.post('/v1/chat/completions', content=b'{"model":"m","messages":[]}')
        assert response.status_code == 200
        rows = _diagnostic_rows(capsys)
    finally:
        diagnostics.disable_stdout()
    (row,) = [r for r in rows if 'outcome' in r]
    assert 'usage_prompt_tokens' not in row


# --- lifespan, configuration and GET /stats (proxy-stats-propagation 3.1, 3.3)

class FakeChild:
    """A stand-in for the stdio child that records its lifecycle into `log`."""

    def __init__(self, log=None, *, fail_start=False):
        self.log = [] if log is None else log
        self.calls = []
        self.fail_start = fail_start

    async def start(self):
        self.log.append('child.start')
        if self.fail_start:
            raise RuntimeError('spawn failed: /secret/path')

    async def shutdown(self, timeout_s=2.0):
        self.log.append('child.shutdown')

    async def call_tool(self, name, arguments, *, timeout_ms):
        self.calls.append((name, dict(arguments), timeout_ms))
        return {}


class FakeReporter:
    def __init__(self, log=None):
        self.log = [] if log is None else log

    async def start(self):
        self.log.append('reporter.start')

    async def stop(self):
        self.log.append('reporter.stop')


def python_bin():
    import sys
    return sys.executable


def econ_settings():
    return proxy.EconConfig(mmhmem_bin=python_bin(), project='mvibe', tools=frozenset({'read'}),
                            min_bytes=4096, timeout_ms=45000, memo_max=2000, retry_s=300, concurrency=2)


def stats_harness(*, usage=True, econ=None, child=None, reporter=None, runner=None, upstream=None):
    """harness() with stats wiring: an injected child and reporter recording into one log."""
    log = []
    child = child if child is not None else FakeChild(log)
    reporter = reporter if reporter is not None else FakeReporter(log)
    built = []

    def reporter_factory(client, counters, config):
        built.append((client, counters, config))
        return reporter

    config = proxy.StatsConfig(usage=usage, mmhmem_bin=python_bin() if usage else None)
    options = dict(stats_config=config, econ_config=econ, econ_client=child,
                   reporter_factory=reporter_factory)
    if runner is not None:
        options['stats_runner'] = runner
    app, session, sent, _ = harness(upstream=upstream, **options)
    return SimpleNamespace(app=app, session=session, sent=sent, child=child, reporter=reporter,
                           built=built, log=log, config=config)


def local_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local')


@pytest.mark.anyio
async def test_stats_only_starts_the_injected_child_once_and_stops_the_reporter_before_it():
    h = stats_harness(usage=True)
    async with h.app.router.lifespan_context(h.app):
        assert h.app.state.econ is None
        assert h.app.state.reporter is h.reporter and h.app.state.stats_child is h.child
        assert h.app.state.counters is not None
        assert h.built == [(h.child, h.app.state.counters, h.config)]
        assert h.log == ['child.start', 'reporter.start']
    assert h.log == ['child.start', 'reporter.start', 'reporter.stop', 'child.shutdown']
    assert h.app.state.reporter is None and h.app.state.stats_child is None
    assert diagnostics._counters_sink is None


@pytest.mark.anyio
async def test_economics_and_stats_share_one_child_owned_by_the_shunt():
    h = stats_harness(usage=True, econ=econ_settings())
    async with h.app.router.lifespan_context(h.app):
        assert h.app.state.econ is not None and h.app.state.econ.client is h.child
        assert h.app.state.stats_child is None and h.app.state.reporter is h.reporter
        assert h.log == ['child.start', 'reporter.start']
    assert h.log == ['child.start', 'reporter.start', 'reporter.stop', 'child.shutdown']
    assert h.app.state.econ is None


@pytest.mark.anyio
async def test_economics_alone_starts_the_child_without_a_reporter():
    h = stats_harness(usage=False, econ=econ_settings())
    async with h.app.router.lifespan_context(h.app):
        assert h.app.state.econ is not None and h.app.state.reporter is None
    assert h.log == ['child.start', 'child.shutdown'] and h.built == []


@pytest.mark.anyio
async def test_without_stats_or_economics_nothing_is_started_but_counters_exist():
    h = stats_harness(usage=False)
    async with h.app.router.lifespan_context(h.app):
        assert h.app.state.counters is not None and h.app.state.reporter is None
        assert h.app.state.stats_child is None and h.app.state.econ is None
    assert h.log == [] and h.built == []


@pytest.mark.anyio
async def test_counters_see_the_inference_requests_of_one_lifespan_and_no_local_routes():
    h = stats_harness(usage=False)
    async with h.app.router.lifespan_context(h.app):
        async with local_client(h.app) as client:
            assert (await client.get('/health')).status_code == 200
            assert (await client.get('/v1/models')).status_code == 200
            first = (await client.get('/stats')).json()['proxy']
            second = (await client.get('/stats')).json()['proxy']
        counters = h.app.state.counters
    assert first['requests'] == {'total': 1, 'by_outcome': {'completed': 1}, 'by_status': {'200': 1}}
    assert second['requests']['total'] == 1
    assert diagnostics._counters_sink is None
    diagnostics.RequestDiagnostic().emit()
    assert counters.snapshot()['requests']['total'] == 1


@pytest.mark.anyio
async def test_a_stats_only_child_that_fails_to_start_warns_and_keeps_serving(caplog):
    child = FakeChild(fail_start=True)
    h = stats_harness(usage=True, child=child)
    with caplog.at_level(logging.WARNING, logger='mvibe-proxy.stats'):
        async with h.app.router.lifespan_context(h.app):
            assert h.app.state.reporter is None and h.app.state.stats_child is None
            async with local_client(h.app) as client:
                assert (await client.get('/v1/models')).status_code == 200
    assert child.log == ['child.start', 'child.shutdown'] and h.built == []
    (message,) = [r.getMessage() for r in caplog.records if r.name == 'mvibe-proxy.stats']
    assert 'usage propagation disabled' in message and 'MVIBE_STATS_MMHMEM_BIN=' in message
    assert 'RuntimeError' in message and 'secret' not in message


@pytest.mark.anyio
async def test_an_economics_child_that_fails_to_start_still_fails_fast_and_clears_the_sink():
    h = stats_harness(usage=True, econ=econ_settings(), child=FakeChild(fail_start=True))
    with pytest.raises(RuntimeError, match='economics startup failed'):
        async with h.app.router.lifespan_context(h.app):
            pass
    assert h.built == [] and diagnostics._counters_sink is None


def recording_runner(result):
    calls = []

    async def runner(bin_path, *, env, timeout_s):
        calls.append((bin_path, dict(env), timeout_s))
        return result
    return runner, calls


@pytest.mark.anyio
async def test_stats_route_serves_counters_and_the_mmhmem_view_locally(monkeypatch):
    monkeypatch.setenv('AGENT_MEM_DATA_DIR', '/data/mmhmem')
    view = {'available': True, 'days': [{'day': '2026-11-10'}], 'totals': {'saved_tokens': 5}}
    runner, calls = recording_runner(view)
    h = stats_harness(usage=True, runner=runner)
    async with h.app.router.lifespan_context(h.app):
        async with local_client(h.app) as client:
            response = await client.get('/stats')
    assert response.status_code == 200
    body = response.json()
    assert body['mmhmem'] == view
    assert body['proxy']['requests'] == {'total': 0, 'by_outcome': {}, 'by_status': {}}
    assert set(body['proxy']) == {'started_at', 'requests', 'usage', 'econ', 'rate_limit'}
    ((bin_path, env, timeout_s),) = calls
    assert bin_path == python_bin() and timeout_s == 10.0 and env['AGENT_MEM_DATA_DIR'] == '/data/mmhmem'
    assert h.sent == [] and not any(isinstance(e, tuple) and e[0] == 'resolve' for e in h.session.events)


@pytest.mark.anyio
@pytest.mark.parametrize('reason', ['not_configured', 'timeout', 'exit_3', 'no_stats_yet', 'invalid_json'])
async def test_stats_route_passes_an_unavailable_mmhmem_view_through(reason):
    runner, _ = recording_runner({'available': False, 'reason': reason})
    h = stats_harness(usage=False, runner=runner)
    async with h.app.router.lifespan_context(h.app):
        async with local_client(h.app) as client:
            body = (await client.get('/stats')).json()
    assert body['mmhmem'] == {'available': False, 'reason': reason}
    assert body['proxy']['requests']['total'] == 0


@pytest.mark.anyio
async def test_stats_route_with_the_inert_default_config_asks_the_runner_for_no_binary():
    runner, calls = recording_runner({'available': False, 'reason': 'not_configured'})
    app, _, sent, _ = harness(stats_runner=runner)
    async with app.router.lifespan_context(app):
        async with local_client(app) as client:
            body = (await client.get('/stats')).json()
    assert calls[0][0] is None and calls[0][2] == 10.0
    assert body['mmhmem']['reason'] == 'not_configured' and sent == []


@pytest.mark.anyio
async def test_openapi_documents_the_stats_route():
    app, *_ = harness()
    async with app.router.lifespan_context(app):
        async with local_client(app) as client:
            schema = (await client.get('/openapi.json')).json()
    entry = schema['paths']['/stats']['get']
    assert 'mmhmem' in entry['responses']['200']['description']


def _stats_cli(monkeypatch):
    selected = []
    monkeypatch.setattr(proxy, 'create_app', lambda **kwargs: selected.append(kwargs) or 'app')
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *args, **kwargs: None)
    return selected


def test_cli_enabled_usage_without_a_binary_exits_2_naming_both_settings(monkeypatch, capsys):
    monkeypatch.setenv('MVIBE_STATS_USAGE', '1')
    selected = _stats_cli(monkeypatch)
    with pytest.raises(SystemExit) as error:
        proxy.main([])
    assert error.value.code == 2 and selected == []
    err = capsys.readouterr().err
    assert err.startswith('error: ') and 'MVIBE_STATS_USAGE' in err and 'MVIBE_STATS_MMHMEM_BIN' in err


def test_cli_stats_flag_without_configuration_exits_2(monkeypatch, capsys):
    selected = _stats_cli(monkeypatch)
    with pytest.raises(SystemExit) as error:
        proxy.main(['--stats'])
    assert error.value.code == 2 and selected == []
    err = capsys.readouterr().err
    assert 'MVIBE_STATS_USAGE' in err and 'MVIBE_STATS_MMHMEM_BIN' in err


@pytest.mark.parametrize(('usage', 'exits'), [(None, False), ('0', False), ('1', True)])
def test_cli_invalid_stats_binary_warns_unless_usage_is_requested(monkeypatch, capsys, tmp_path, usage, exits):
    monkeypatch.setenv('MVIBE_STATS_MMHMEM_BIN', str(tmp_path / 'missing-mmhmem'))
    if usage is not None:
        monkeypatch.setenv('MVIBE_STATS_USAGE', usage)
    selected = _stats_cli(monkeypatch)
    if exits:
        with pytest.raises(SystemExit) as error:
            proxy.main([])
        assert error.value.code == 2 and selected == []
        assert 'error: ' in capsys.readouterr().err
    else:
        proxy.main([])
        err = capsys.readouterr().err
        assert 'WARNING: ' in err and 'MVIBE_STATS_MMHMEM_BIN' in err
        assert selected[0]['stats_config'] is None


def test_cli_passes_the_validated_stats_config_to_the_selected_app(monkeypatch):
    monkeypatch.setenv('MVIBE_STATS_USAGE', '1')
    monkeypatch.setenv('MVIBE_STATS_MMHMEM_BIN', python_bin())
    monkeypatch.setenv('MVIBE_STATS_REPORT_INTERVAL_S', '5')
    selected = _stats_cli(monkeypatch)
    proxy.main(['--stats'])
    config = selected[0]['stats_config']
    assert config.usage is True and config.mmhmem_bin == python_bin() and config.report_interval_s == 5.0


def test_cli_without_stats_settings_passes_the_inert_config(monkeypatch):
    selected = _stats_cli(monkeypatch)
    proxy.main([])
    config = selected[0]['stats_config']
    assert config.usage is False and config.mmhmem_bin is None


@pytest.mark.parametrize(('argv', 'url'), [
    ([], 'http://127.0.0.1:8765/stats'),
    (['--port', '8799'], 'http://127.0.0.1:8799/stats'),
    (['--host', '::1'], 'http://[::1]:8765/stats'),
])
def test_cli_prints_the_stats_url_for_the_bound_host_and_port(monkeypatch, capsys, argv, url):
    _stats_cli(monkeypatch)
    proxy.main(argv)
    assert f'Stats: {url}\n' in capsys.readouterr().err


@pytest.mark.parametrize(('data_dir', 'writable', 'warns'), [
    (None, True, False), ('/data/mmhmem', True, False), ('/data/mmhmem', False, True)])
def test_cli_warns_once_when_the_data_dir_is_not_writable(monkeypatch, capsys, data_dir, writable, warns):
    if data_dir is not None:
        monkeypatch.setenv('AGENT_MEM_DATA_DIR', data_dir)
    checked = []
    real_access = proxy.os.access

    def access(path, mode, **kw):
        if path == data_dir:
            checked.append(path)
            return writable
        return real_access(path, mode, **kw)
    monkeypatch.setattr(proxy.os, 'access', access)
    _stats_cli(monkeypatch)
    proxy.main([])
    err = capsys.readouterr().err
    warning = ('WARNING: AGENT_MEM_DATA_DIR=/data/mmhmem is not writable by this user; create it '
               'before starting (mkdir -p) or fix ownership (uid 1000 in Docker)')
    assert (warning in err) is warns and err.count('AGENT_MEM_DATA_DIR') == (1 if warns else 0)
    assert checked == (['/data/mmhmem'] if data_dir else [])


@pytest.mark.anyio
async def test_the_default_runner_reports_not_configured_for_the_inert_config():
    app, _, sent, _ = harness()
    async with app.router.lifespan_context(app):
        async with local_client(app) as client:
            body = (await client.get('/stats')).json()
    assert body['mmhmem'] == {'available': False, 'reason': 'not_configured'}
    assert body['proxy']['requests']['total'] == 0 and sent == []


@pytest.mark.anyio
async def test_the_default_reporter_reports_the_counters_ledger_over_the_shared_child():
    from mvibe_proxy import stats
    child = FakeChild()
    counters = stats.ProxyCounters()
    config = proxy.StatsConfig(usage=True, mmhmem_bin=python_bin(), report_interval_s=5.0,
                               mmhmem_timeout_s=2.5, project='demo')
    reporter = proxy._default_reporter(child, counters, config)
    assert isinstance(reporter, stats.UsageReporter)
    counters.usage.add(stats.Usage(3, 2, 'mistral-large-latest'))
    assert await reporter.report() is True
    ((name, arguments, timeout_ms),) = child.calls
    assert name == 'session_resume_or_handoff' and timeout_ms == 2500
    assert arguments['project'] == 'demo' and arguments['agent_harness'] == 'mvibe-proxy'
    assert arguments['client_usage'] == {
        'prompt_tokens': 3, 'completion_tokens': 2, 'source': 'mvibe-proxy mistral-large-latest'}
    assert counters.last_report_at is not None
    assert counters.snapshot()['usage']['last_report_at'] is not None


@pytest.mark.anyio
async def test_the_lifespan_runs_a_real_usage_reporter_over_the_injected_child_by_default():
    from mvibe_proxy import stats
    child = FakeChild()
    app, _, sent, _ = harness(stats_config=proxy.StatsConfig(usage=True, mmhmem_bin=python_bin()),
                              econ_client=child)
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.reporter, stats.UsageReporter)
        assert child.log == ['child.start']
    assert child.log == ['child.start', 'child.shutdown'] and child.calls == []
    assert app.state.reporter is None and sent == []
