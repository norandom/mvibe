"""Offline contracts for the public API and opt-in safe diagnostics."""
import json
import httpx
import pytest
import proxy
from test_proxy import harness
from test_streaming import Stream, app_for, call


def test_openapi_describes_supported_operations_without_validation():
    schema = proxy.create_app().openapi()
    assert '/v1/{path}' not in schema['paths']
    for path, method in [('/v1/models', 'get'), ('/v1/chat/completions', 'post'), ('/v1/embeddings', 'post')]:
        operation = schema['paths'][path][method]
        assert not operation.get('security')
    chat = schema['paths']['/v1/chat/completions']['post']
    assert 'text/event-stream' in chat['responses']['200']['content']
    rendered = json.dumps(schema)
    assert 'tool_calls' in rendered and 'agent executes' in rendered
    assert 'MCP' in rendered and 'Responses' in rendered
    assert '401' in chat['responses'] and '502' in chat['responses']


RATE_LIMIT_SETTINGS = ('MVIBE_RATE_LIMIT_MAX_WAIT_S', 'MVIBE_RATE_LIMIT_MAX_QUEUE', 'MVIBE_RATE_LIMIT_RPS')
RATE_LIMITED_HINT = ('Upstream rate limit persisted beyond MVIBE_RATE_LIMIT_MAX_WAIT_S or '
                     'MVIBE_RATE_LIMIT_MAX_QUEUE; lower client concurrency, configure '
                     'MVIBE_RATE_LIMIT_RPS, or raise the workspace limit.')
UNRETRIED_429_HINT = 'Respect upstream rate_limit guidance before an explicit retry.'
RATE_LIMIT_FIELDS = ('queued_ms', 'rate_limit_rejections', 'rate_limit_headers')


class SinglePassGate:
    """A gate that sends once and never retries, leaving the record untouched.

    The real gate always holds or gives up on a 429; this one reaches the
    diagnostic's fallback for a 429 that was forwarded without a retry."""

    def now(self):
        return 0.0

    async def run(self, key, attempt, *, arrived, record):
        return await attempt()


@pytest.mark.anyio
@pytest.mark.parametrize(('status', 'gate', 'outcome', 'hints'), [
    (400, 'clocked', 'upstream_error', ('tool_schema',)),
    (401, 'clocked', 'upstream_error', ('login',)),
    (403, 'clocked', 'upstream_error', ('tenant',)),
    (422, 'clocked', 'upstream_error', ('reasoning',)),
    # A 429 forwarded without a retry keeps the upstream rate_limit hint; a 429 the
    # gate answered after giving up is the rate_limited outcome naming the settings.
    (429, 'single-pass', 'upstream_error', ('rate_limit',)),
    (429, 'exhausting', 'rate_limited', RATE_LIMIT_SETTINGS),
])
async def test_correlated_safe_stdout_upstream_errors(status, gate, outcome, hints, capsys):
    from fakes import Clock
    from mvibe_proxy import diagnostics
    from mvibe_proxy.ratelimit import RateLimitConfig
    from test_proxy import answers, clocked_gate
    clock = Clock()
    options = {'gate_factory': clocked_gate(clock)}
    if gate == 'single-pass':
        options = {'gate_factory': lambda config: SinglePassGate()}
    elif gate == 'exhausting':
        # The real gate on a fake clock: the 1 s backoff fits the 1.5 s maximum
        # wait, so it re-sends once; the second backoff (2 s) does not, so it
        # gives up and answers the last upstream 429.
        options = {'rate_limit_config': RateLimitConfig(max_wait_s=1.5),
                   'gate_factory': clocked_gate(clock)}
    diagnostics.configure_stdout()
    try:
        app, _, sent, _ = harness(upstream=answers(clock, (
            status, {'x-ratelimit-remaining-req-minute': '0'}, b'{"result":"synthetic"}')), **options)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                result = await client.post('/v1/chat/completions?secret-query=secret', content=b'not-json-secret-prompt', headers={'x-request-id':'caller-secret', 'authorization':'secret-header'})
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(sent) == (2 if gate == 'exhausting' else 1)
        assert all(request.content == b'not-json-secret-prompt' for request in sent)
        assert result.content == b'{"result":"synthetic"}'
        assert rows[-1]['request_id'] == result.headers['x-request-id']
        assert all(hint in rows[-1]['hint'] for hint in hints)
        assert rows[-1]['status'] == status and rows[-1]['outcome'] == outcome
        assert rows[-1]['phase'] == 'upstream'
        if gate == 'exhausting':
            assert rows[-1]['hint'] == RATE_LIMITED_HINT
            assert (rows[-1]['queued_ms'], rows[-1]['rate_limit_rejections']) == (1000.0, 2)
            assert rows[-1]['rate_limit_headers'] == {'x-ratelimit-remaining-req-minute': '0'}
        else:
            assert 'MVIBE_RATE_LIMIT' not in rows[-1]['hint']
            assert (rows[-1]['queued_ms'], rows[-1]['rate_limit_rejections']) == (0.0, 0)
        assert 'secret' not in json.dumps(rows)
    finally:
        diagnostics.disable_stdout()


@pytest.mark.anyio
async def test_interrupted_stream_reports_safe_correlated_event(capsys):
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    try:
        stream = Stream(error=True)
        app, sent, _ = app_for([stream])
        async with app.router.lifespan_context(app):
            with pytest.raises(Exception):
                await call(app)
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert rows[-1]['outcome'] == 'stream_interrupted'
        assert rows[-1]['phase'] == 'stream'
        assert 'synthetic-secret' not in json.dumps(rows)
        assert stream.closed.is_set() and len(sent) == 1
    finally:
        diagnostics.disable_stdout()

@pytest.mark.anyio
@pytest.mark.parametrize(('failure', 'phase', 'outcome', 'hint'), [
    ('auth', 'authentication', 'auth_failed', '--login'),
    ('connection', 'acquisition', 'connection_failed', 'connectivity'),
    ('unsupported', 'routing', 'unsupported_endpoint', 'unsupported'),
])
async def test_preheader_failure_logs_without_replay_or_secrets(failure, phase, outcome, hint, capsys):
    from mvibe_auth.api import AuthFailure
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    try:
        app, session, sent, _ = harness(fail_send=failure == 'connection')
        if failure == 'auth':
            session.failure = AuthFailure('rejected')
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                result = await client.get('/v1/responses' if failure == 'unsupported' else '/v1/models')
        row = json.loads(capsys.readouterr().out)
        assert row['request_id'] == result.headers['x-request-id']
        assert (row['phase'], row['outcome']) == (phase, outcome)
        assert hint in row['hint'] and row['elapsed_ms'] >= 0
        assert len(sent) == (1 if failure == 'connection' else 0)
        assert 'secret' not in json.dumps(row)
    finally:
        diagnostics.disable_stdout()


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['cancel', 'cleanup'])
async def test_cancellation_and_bounded_cleanup_diagnostics(failure, capsys):
    import asyncio
    from mvibe_proxy import diagnostics
    from mvibe_proxy.streaming import StreamAborted
    diagnostics.configure_stdout()
    try:
        stream = Stream(close_gate=asyncio.Event() if failure == 'cleanup' else None)
        app, sent, _ = app_for([stream])
        async def cancel(message):
            if message.get('body') == b'first':
                raise asyncio.CancelledError
        async with app.router.lifespan_context(app):
            app.state.stream_owner.close_timeout = 0.01
            with pytest.raises(asyncio.CancelledError if failure == 'cancel' else StreamAborted):
                await call(app, send_hook=cancel if failure == 'cancel' else None)
        row = json.loads(capsys.readouterr().out)
        assert row['outcome'] == ('cancelled' if failure == 'cancel' else 'cleanup_failed')
        assert row['phase'] == ('stream' if failure == 'cancel' else 'cleanup')
        assert len(sent) == 1
        if failure == 'cancel':
            assert stream.closed.is_set()
    finally:
        diagnostics.disable_stdout()


@pytest.mark.anyio
async def test_broken_log_output_cannot_break_response(monkeypatch):
    from mvibe_proxy import diagnostics
    class Broken:
        def info(self, *args, **kwargs):
            raise RuntimeError('secret logging failure')
    monkeypatch.setattr(diagnostics, '_logger', Broken())
    app, _, sent, _ = harness()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.get('/v1/models')
    assert response.status_code == 200 and len(sent) == 1


@pytest.mark.anyio
async def test_swagger_and_openapi_serve_without_lifespan_or_login():
    app = proxy.create_app(lambda: pytest.fail('documentation must not start auth'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
        assert (await client.get('/openapi.json')).json()['paths']['/health']['get']
        docs = await client.get('/docs')
        assert docs.status_code == 200 and 'swagger-ui' in docs.text


def test_explicit_logging_does_not_configure_shared_loggers(monkeypatch):
    import logging
    import structlog
    from mvibe_proxy import diagnostics
    shared_handlers = list(logging.getLogger().handlers)
    config = structlog.get_config().copy()
    diagnostics.configure_stdout()
    try:
        assert logging.getLogger().handlers == shared_handlers
        assert structlog.get_config() == config
    finally:
        diagnostics.disable_stdout()

@pytest.mark.anyio
@pytest.mark.parametrize(('method', 'path', 'status'), [('DELETE', '/v1/models', 405), ('GET', '/unknown', 404)])
async def test_router_errors_report_routing_failure(method, path, status, capsys):
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    try:
        app = proxy.create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.request(method, path)
        row = json.loads(capsys.readouterr().out)
        assert response.status_code == status
        assert response.json() == {'detail': 'Method Not Allowed' if status == 405 else 'Not Found'}
        assert row['outcome'] == 'unsupported_endpoint' and row['phase'] == 'routing'
        assert row['request_id'] == response.headers['x-request-id']
    finally:
        diagnostics.disable_stdout()


@pytest.mark.anyio
async def test_upstream_404_is_not_misclassified_as_local_routing(capsys):
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    try:
        app, _, sent, _ = harness(response_status=404)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                response = await client.get('/v1/models')
        row = json.loads(capsys.readouterr().out)
        assert row['outcome'] == 'upstream_error' and row['phase'] == 'upstream'
        assert response.status_code == 404 and len(sent) == 1
    finally:
        diagnostics.disable_stdout()


@pytest.mark.parametrize('enabled', [False, True])
def test_cli_logging_is_explicit_and_clears_stale_configuration(enabled, monkeypatch, capsys):
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *args, **kwargs: diagnostics.RequestDiagnostic().emit())
    try:
        proxy.main(['--log'] if enabled else [])
        output = capsys.readouterr().out
        assert bool(output) is enabled
        if enabled:
            assert json.loads(output)['event'] == 'proxy_request'
    finally:
        diagnostics.disable_stdout()


# --- rate-limit diagnostics (upstream-rate-limit-queue task 2.2) --------------
# Requirements 1.4, 1.6, 5.1, 5.2, 5.4: the gate's GateRecord reaches the
# proxy_request event as queued_ms and rate_limit_rejections on both channels
# and rate_limit_headers on stdout only; giving up is the rate_limited outcome.


class CapturingExporter:
    """Keeps every payload the diagnostic hands the export sink, before allowlisting."""

    def __init__(self):
        self.records = []

    def enqueue(self, record):
        self.records.append(dict(record))


def _emit(diagnostic, capsys):
    """Emit `diagnostic` once to stdout and to a capturing exporter; return both events."""
    from mvibe_proxy import diagnostics
    exporter = CapturingExporter()
    diagnostics.configure_stdout()
    diagnostics.configure_exporter(exporter)
    try:
        diagnostic.emit()
    finally:
        diagnostics.disable_stdout()
        diagnostics.clear_exporter()
    (line,) = capsys.readouterr().out.splitlines()
    (exported,) = exporter.records
    return json.loads(line), exported


def test_gate_record_is_defined_by_diagnostics_and_shared_with_the_gate():
    import ast
    import dataclasses
    import inspect
    from mvibe_proxy import diagnostics, ratelimit
    assert diagnostics.GateRecord.__module__ == 'mvibe_proxy.diagnostics'
    assert ratelimit.GateRecord is diagnostics.GateRecord
    assert [field.name for field in dataclasses.fields(diagnostics.GateRecord)] == [
        'queued_ms', 'rejections', 'headers', 'exhausted']
    record = diagnostics.GateRecord()
    assert (record.queued_ms, record.rejections, record.headers, record.exhausted) == (
        0.0, 0, {}, False)
    # Dependency direction: ratelimit imports diagnostics, never the reverse.
    imported = set()
    for node in ast.walk(ast.parse(inspect.getsource(diagnostics))):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f'{node.module}.{alias.name}' for alias in node.names}
    assert not any('ratelimit' in name for name in imported)


def test_a_request_without_a_gate_record_emits_no_rate_limit_fields(capsys):
    from mvibe_proxy.diagnostics import RequestDiagnostic
    diagnostic = RequestDiagnostic()
    assert diagnostic.rate_limit is None
    row, exported = _emit(diagnostic, capsys)
    assert not set(RATE_LIMIT_FIELDS) & (set(row) | set(exported))


@pytest.mark.parametrize(('queued_ms', 'rejections', 'rounded'), [
    (0.0, 0, 0.0), (1234.5678, 2, 1234.57), (0.004, 1, 0.0)])
def test_rate_limit_fields_are_present_and_numeric(queued_ms, rejections, rounded, capsys):
    from mvibe_proxy.diagnostics import GateRecord, RequestDiagnostic
    diagnostic = RequestDiagnostic()
    diagnostic.rate_limit = GateRecord(queued_ms=queued_ms, rejections=rejections,
                                       headers={'x-ratelimit-remaining-req-minute': '0'})
    row, exported = _emit(diagnostic, capsys)
    for event in (row, exported):
        assert type(event['queued_ms']) is float and event['queued_ms'] == rounded
        assert type(event['rate_limit_rejections']) is int
        assert event['rate_limit_rejections'] == rejections
    assert row['rate_limit_headers'] == {'x-ratelimit-remaining-req-minute': '0'}
    assert 'rate_limit_headers' not in exported
    assert row['outcome'] == exported['outcome'] == 'completed'


def test_failure_rate_limited_names_the_settings_to_adjust():
    from mvibe_proxy.diagnostics import RequestDiagnostic
    diagnostic = RequestDiagnostic()
    diagnostic.failure('rate_limited')
    assert (diagnostic.phase, diagnostic.outcome, diagnostic.hint) == (
        'upstream', 'rate_limited', RATE_LIMITED_HINT)
    assert all(name in diagnostic.hint for name in RATE_LIMIT_SETTINGS)


@pytest.mark.parametrize(('record', 'status', 'outcome'), [
    (None, 429, 'upstream_error'),
    ({'rejections': 1}, 429, 'upstream_error'),
    ({'rejections': 3, 'exhausted': True}, 429, 'rate_limited'),
    # Queue full: the gate refused the request before any upstream attempt.
    ({'exhausted': True}, 429, 'rate_limited'),
    ({'exhausted': True}, 503, 'upstream_error'),
    ({'exhausted': True}, 200, 'completed'),
])
def test_upstream_429_is_rate_limited_only_when_the_gate_gave_up(record, status, outcome):
    from mvibe_proxy.diagnostics import GateRecord, RequestDiagnostic
    diagnostic = RequestDiagnostic()
    if record is not None:
        diagnostic.rate_limit = GateRecord(**record)
    diagnostic.upstream(status)
    assert diagnostic.outcome == outcome
    if outcome == 'rate_limited':
        assert (diagnostic.phase, diagnostic.hint) == ('upstream', RATE_LIMITED_HINT)
    elif status == 429:
        assert (diagnostic.phase, diagnostic.hint) == ('upstream', UNRETRIED_429_HINT)


@pytest.mark.anyio
async def test_rate_limit_headers_never_carry_body_text_or_credentials(clock, capsys):
    import asyncio
    import httpx2
    from mvibe_proxy.diagnostics import GateRecord, RequestDiagnostic
    from mvibe_proxy.ratelimit import RateLimitConfig, UpstreamGate
    body = b'{"message":"secret-body-text: Requests rate limit exceeded"}'
    snapshots = iter([
        {'x-ratelimit-remaining-req-minute': '0', 'x-ratelimit-limit-req-minute': '30'},
        {'x-ratelimit-remaining-req-minute': '1'},
    ])
    sent = []

    async def attempt():
        sent.append(clock.time())
        await asyncio.sleep(0)
        return httpx2.Response(429, content=body, headers={
            'content-type': 'application/json', 'retry-after': '5',
            'authorization': 'Bearer secret-credential', 'set-cookie': 'session=secret-cookie',
            'x-error-detail': 'secret-body-text', **next(snapshots)})

    # Retry-After 5 s against a 1 s maximum wait: one re-send, then the gate gives up.
    gate = UpstreamGate(RateLimitConfig(max_wait_s=1.0), clock=clock.time, sleep=clock.sleep,
                        wall_clock=clock.time)
    diagnostic = RequestDiagnostic()
    diagnostic.rate_limit = record = GateRecord()
    t0 = clock.time()
    response = await asyncio.wait_for(
        gate.run('mistral-large-latest', attempt, arrived=t0, record=record), 5)
    diagnostic.status = response.status_code
    diagnostic.upstream(response.status_code)
    row, exported = _emit(diagnostic, capsys)
    assert response.status_code == 429 and sent == [t0, t0 + 1]
    assert (row['outcome'], row['status'], row['rate_limit_rejections']) == ('rate_limited', 429, 2)
    assert row['queued_ms'] == 1000.0
    # Merged across rejections, the latest value per name winning.
    assert row['rate_limit_headers'] == {'x-ratelimit-remaining-req-minute': '1',
                                         'x-ratelimit-limit-req-minute': '30'}
    assert all(name.startswith('x-ratelimit-') for name in row['rate_limit_headers'])
    assert 'secret' not in json.dumps(row) and 'secret' not in json.dumps(exported)
    assert 'rate_limit_headers' not in exported
