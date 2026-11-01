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


@pytest.mark.anyio
@pytest.mark.parametrize(('status', 'hint'), [(400, 'tool_schema'), (401, 'login'), (403, 'tenant'), (422, 'reasoning'), (429, 'rate_limit')])
async def test_correlated_safe_stdout_upstream_errors(status, hint, capsys):
    from mvibe_proxy import diagnostics
    diagnostics.configure_stdout()
    try:
        app, _, sent, _ = harness(response_status=status)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
                result = await client.post('/v1/chat/completions?secret-query=secret', content=b'not-json-secret-prompt', headers={'x-request-id':'caller-secret', 'authorization':'secret-header'})
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert len(sent) == 1 and sent[0].content == b'not-json-secret-prompt'
        assert result.content == b'{"result":"synthetic"}'
        assert rows[-1]['request_id'] == result.headers['x-request-id']
        assert hint in rows[-1]['hint']
        assert rows[-1]['status'] == status and rows[-1]['outcome'] == 'upstream_error'
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
