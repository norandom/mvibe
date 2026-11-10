"""Offline contracts for opt-in observability: OpenObserve-style export + optional OTLP."""
import asyncio
import base64
import json

import httpx
import pytest
from pydantic import SecretStr

import proxy
from mvibe_proxy import diagnostics, observability
from mvibe_proxy.observability import ObserveConfig, ObserveConfigError, OpenObserveExporter, allowlisted
from test_proxy import harness


def observe_env(monkeypatch, *, url='http://oo.local:5080', org=None, stream=None,
                user='dev@example.com', token='synthetic-token'):
    monkeypatch.setenv('MVIBE_OBSERVE_URL', url)
    if org is not None:
        monkeypatch.setenv('MVIBE_OBSERVE_ORG', org)
    if stream is not None:
        monkeypatch.setenv('MVIBE_OBSERVE_STREAM', stream)
    if user is not None:
        monkeypatch.setenv('MVIBE_OBSERVE_USER', user)
    if token is not None:
        monkeypatch.setenv('MVIBE_OBSERVE_TOKEN', token)


def config(**kwargs) -> ObserveConfig:
    return ObserveConfig(url='http://oo.local:5080', org='default', stream='mvibe',
                         user='dev@example.com', token=SecretStr('synthetic-token'))


class FakeExporter:
    def __init__(self):
        self.records = []
        self.started = False
        self.stopped = False

    def enqueue(self, record):
        self.records.append(dict(record))

    async def start(self):
        self.started = True

    async def shutdown(self):
        self.stopped = True


def mock_client(posts, status=200):
    async def handler(request):
        posts.append(request)
        return httpx.Response(status, content=b'{}')
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_allowlist_drops_unknown_and_null_fields():
    record = allowlisted({'event': 'proxy_request', 'request_id': 'abc', 'trace_id': None,
                          'authorization': 'Bearer secret', 'body': 'secret-prompt',
                          'model': 'mistral-large-4'})
    assert record == {'service': 'mvibe-proxy', 'event': 'proxy_request',
                      'request_id': 'abc', 'model': 'mistral-large-4'}


def test_allowlist_keeps_rate_limit_numbers_and_drops_rate_limit_headers():
    assert {'queued_ms', 'rate_limit_rejections'} <= observability._ALLOWED_FIELDS
    assert 'rate_limit_headers' not in observability._ALLOWED_FIELDS
    record = allowlisted({'event': 'proxy_request', 'queued_ms': 0.0, 'rate_limit_rejections': 0,
                          'rate_limit_headers': {'x-ratelimit-remaining-req-minute': '0'}})
    assert record == {'service': 'mvibe-proxy', 'event': 'proxy_request',
                      'queued_ms': 0.0, 'rate_limit_rejections': 0}


def test_observe_config_defaults_and_url_shape(monkeypatch):
    for key in ('MVIBE_OBSERVE_URL', 'MVIBE_OBSERVE_ORG', 'MVIBE_OBSERVE_STREAM',
                'MVIBE_OBSERVE_USER', 'MVIBE_OBSERVE_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    assert ObserveConfig.from_env() is None
    observe_env(monkeypatch)
    cfg = ObserveConfig.from_env()
    assert cfg is not None
    assert (cfg.org, cfg.stream) == ('default', 'mvibe')
    assert cfg.logs_url == 'http://oo.local:5080/api/default/mvibe/_json'
    assert cfg.authorization == 'Basic ' + base64.b64encode(b'dev@example.com:synthetic-token').decode()
    observe_env(monkeypatch, stream='python', org='acme')
    assert ObserveConfig.from_env().logs_url == 'http://oo.local:5080/api/acme/python/_json'


def test_observe_config_requires_credentials(monkeypatch):
    observe_env(monkeypatch, token=None, user=None)
    with pytest.raises(ObserveConfigError):
        ObserveConfig.from_env()


@pytest.mark.anyio
async def test_exporter_batches_allowlisted_records_with_basic_auth():
    posts = []
    exporter = OpenObserveExporter(config(), client=mock_client(posts), flush_interval=0.01)
    await exporter.start()
    exporter.enqueue({'event': 'proxy_request', 'request_id': 'r1', 'authorization': 'Bearer secret',
                      'hint': 'ok', 'status': 200})
    exporter.enqueue({'event': 'proxy_request', 'request_id': 'r2', 'prompt': 'secret'})
    await asyncio.sleep(0.1)
    await exporter.shutdown()
    assert len(posts) == 1
    request = posts[0]
    assert str(request.url) == 'http://oo.local:5080/api/default/mvibe/_json'
    assert request.headers['authorization'].startswith('Basic ')
    body = json.loads(request.content)
    assert [row['request_id'] for row in body] == ['r1', 'r2']
    assert 'secret' not in request.content.decode()
    assert all(set(row) <= {'service', 'event', 'request_id', 'hint', 'status'} for row in body)
    assert exporter.exported_events == 2 and exporter.export_failures == 0


@pytest.mark.anyio
@pytest.mark.parametrize('outcome', ['http500', 'exception'])
async def test_exporter_counts_failures_and_never_raises(outcome):
    if outcome == 'http500':
        client = mock_client([], status=500)
    else:
        async def boom(request):
            raise RuntimeError('network down')
        client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    exporter = OpenObserveExporter(config(), client=client, flush_interval=0.01)
    await exporter.start()
    exporter.enqueue({'event': 'proxy_request', 'request_id': 'r1'})
    await asyncio.sleep(0.1)
    await exporter.shutdown()
    assert exporter.export_failures == 1 and exporter.exported_events == 0


@pytest.mark.anyio
async def test_exporter_queue_bound_drops_oldest_and_counts():
    exporter = OpenObserveExporter(config(), client=mock_client([]), max_queue=5)
    for index in range(10):
        exporter.enqueue({'event': 'proxy_request', 'request_id': f'r{index}'})
    assert exporter.dropped_events == 5
    assert exporter._queue.qsize() == 5


@pytest.mark.anyio
async def test_exporter_shutdown_flushes_pending_without_start():
    posts = []
    exporter = OpenObserveExporter(config(), client=mock_client(posts))
    exporter.enqueue({'event': 'proxy_request', 'request_id': 'pending'})
    await exporter.shutdown()
    assert len(posts) == 1 and exporter.exported_events == 1


@pytest.mark.anyio
async def test_exported_record_carries_rate_limit_numbers_but_never_headers():
    posts = []
    exporter = OpenObserveExporter(config(), client=mock_client(posts))
    diagnostic = diagnostics.RequestDiagnostic()
    diagnostic.rate_limit = diagnostics.GateRecord(
        queued_ms=2500.126, rejections=3, exhausted=True,
        headers={'x-ratelimit-remaining-req-minute': '0', 'x-ratelimit-limit-req-minute': '30'})
    diagnostic.status = 429
    diagnostic.upstream(429)
    diagnostics.configure_exporter(exporter)
    try:
        diagnostic.emit()
    finally:
        diagnostics.clear_exporter()
    await exporter.shutdown()
    (row,) = json.loads(posts[0].content)
    assert (row['queued_ms'], row['rate_limit_rejections']) == (2500.13, 3)
    assert (row['outcome'], row['status']) == ('rate_limited', 429)
    assert 'rate_limit_headers' not in row
    assert 'x-ratelimit' not in posts[0].content.decode()


def test_cli_observe_requires_env_config(monkeypatch, capsys):
    for key in ('MVIBE_OBSERVE_URL', 'MVIBE_OBSERVE_USER', 'MVIBE_OBSERVE_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(proxy, 'create_app', lambda **kwargs: 'app')
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *a, **k: None)
    with pytest.raises(SystemExit) as error:
        proxy.main(['--observe'])
    assert error.value.code == 2
    assert 'MVIBE_OBSERVE_URL' in capsys.readouterr().err


def test_cli_incomplete_env_without_flag_warns_and_stays_inert(monkeypatch, capsys):
    observe_env(monkeypatch, token=None, user=None)
    selected = []
    monkeypatch.setattr(proxy, 'create_app', lambda **kwargs: selected.append(kwargs) or 'app')
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *a, **k: None)
    proxy.main([])
    assert selected[0]['observe_config'] is None
    assert 'MVIBE_OBSERVE_URL' in capsys.readouterr().err


def test_cli_observe_passes_config(monkeypatch):
    observe_env(monkeypatch)
    selected = []
    monkeypatch.setattr(proxy, 'create_app', lambda **kwargs: selected.append(kwargs) or 'app')
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *a, **k: None)
    proxy.main(['--observe'])
    assert selected[0]['observe_config'].logs_url == 'http://oo.local:5080/api/default/mvibe/_json'


@pytest.mark.anyio
async def test_default_app_has_no_telemetry_and_no_exporter():
    app, session, sent, _ = harness()
    async with app.router.lifespan_context(app):
        assert app.state.telemetry is None
        assert diagnostics._exporter is None
    assert diagnostics._exporter is None


@pytest.mark.anyio
async def test_enabled_export_enqueues_correlated_allowlisted_record(monkeypatch):
    fake = FakeExporter()
    monkeypatch.setattr(proxy, 'OpenObserveExporter', lambda cfg: fake)
    monkeypatch.setattr(proxy, 'start_telemetry', lambda: None)
    original_create = proxy.create_app

    def create_with_config(*args, **kwargs):
        kwargs['observe_config'] = config()
        return original_create(*args, **kwargs)

    monkeypatch.setattr(proxy, 'create_app', create_with_config)
    app, session, sent, _ = harness()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.post('/v1/chat/completions',
                                         content=b'{"model":"mistral-large-4","messages":[{"role":"user","content":"secret-prompt"}]}')
    assert response.status_code == 200
    assert fake.started and fake.stopped
    assert len(fake.records) == 1
    record = fake.records[0]
    assert record['request_id'] == response.headers['x-request-id']
    assert record['model'] == 'mistral-large-4'
    assert 'secret' not in json.dumps(record)
    assert set(record) <= {'service', 'event', 'timestamp', 'request_id', 'trace_id',
                           'span_id', 'phase', 'outcome', 'status', 'elapsed_ms', 'model', 'hint',
                           'queued_ms', 'rate_limit_rejections'}
    # Every request goes through the rate-limit gate, so its numbers are always exported.
    assert type(record['queued_ms']) is float and record['queued_ms'] >= 0
    assert record['rate_limit_rejections'] == 0


def _client_factory(sent):
    def factory(**kwargs):
        import httpx2
        async def upstream(request):
            sent.append(request)
            return httpx2.Response(200, content=b'{"result":"synthetic"}')
        return httpx2.AsyncClient(transport=httpx2.MockTransport(upstream), follow_redirects=False)
    return factory


# Only the tests that build an OpenTelemetry SDK need the optional otel extra; the
# export, allowlist and inert-path tests above and below run in every environment.
OTEL_MISSING = 'optional otel extra not installed; inert path covered by default runs'


@pytest.mark.anyio
async def test_otel_spans_and_traceparent_when_enabled(monkeypatch):
    pytest.importorskip('opentelemetry.sdk', reason=OTEL_MISSING)
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    fake = FakeExporter()
    holder = {}
    monkeypatch.setattr(proxy, 'OpenObserveExporter', lambda cfg: fake)

    def patched_start():
        # Local providers only: the global tracer provider is process-wide and
        # cannot be overridden, so tests never call trace.set_tracer_provider.
        exporter = InMemorySpanExporter()
        holder['exporter'] = exporter
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
        meter_provider = MeterProvider(metric_readers=[InMemoryMetricReader()])
        return observability.Telemetry(tracer_provider, meter_provider,
                                       tracer_provider.get_tracer('mvibe.proxy'),
                                       meter_provider.get_meter('mvibe.proxy'))

    monkeypatch.setattr(proxy, 'start_telemetry', patched_start)
    app, session, sent, _ = harness()
    app = proxy.create_app(lambda: session, _client_factory(sent), force_login=False,
                           observe_config=config())
    async with app.router.lifespan_context(app):
        telemetry = app.state.telemetry
        assert telemetry is not None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            response = await client.get('/v1/models')
    assert response.status_code == 200
    assert 'traceparent' in sent[0].headers
    span_names = {span.name for span in holder['exporter'].get_finished_spans()}
    assert {'proxy.request', 'auth.resolve', 'proxy.stream'} <= span_names
    record = fake.records[-1]
    assert record['trace_id'] and record['span_id']
    assert record['request_id'] == response.headers['x-request-id']


def test_inject_traceparent_never_invents_ids_without_active_span():
    headers = {'Authorization': 'Bearer x'}
    observability.inject_traceparent(headers)
    # No active span -> no traceparent may be written.
    assert 'traceparent' not in headers


def test_start_telemetry_inert_when_otel_extra_missing(monkeypatch):
    import builtins
    import sys
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith('opentelemetry'):
            raise ImportError('blocked for test')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', blocked)
    for module in [m for m in sys.modules if m.startswith('opentelemetry')]:
        monkeypatch.delitem(sys.modules, module)
    assert observability.start_telemetry() is None
    assert observability.current_trace_ids() is None


def test_bridge_call_span_recorded_when_otel_enabled(monkeypatch):
    pytest.importorskip('opentelemetry.sdk', reason=OTEL_MISSING)
    from opentelemetry import trace as ottrace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from mvibe_auth import bridge
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Route the bridge's tracer at this local provider without touching globals.
    monkeypatch.setattr(ottrace, 'get_tracer', lambda *a, **k: provider.get_tracer(*a, **k))
    with bridge._trace_span('bridge.call', {'bridge.method': 'resolve'}):
        pass
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ['bridge.call']
    assert spans[0].attributes['bridge.method'] == 'resolve'
