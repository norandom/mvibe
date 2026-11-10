"""Synthetic raw-ASGI checks: no sockets and no buffering test client."""
import asyncio
import gzip
from urllib.parse import parse_qsl

import anyio
import httpx2
import pytest

import proxy
from fakes import Clock
from test_proxy import SHORT_WAIT, Session, clocked_gate


class Stream(httpx2.AsyncByteStream):
    def __init__(self, chunks=(b'first', b'last'), *, gate=None, error=False, close_gate=None):
        self.chunks = chunks
        self.gate = gate
        self.error = error
        self.close_gate = close_gate
        self.closed = asyncio.Event()
        self.close_started = asyncio.Event()
        self.iterated = False

    async def __aiter__(self):
        self.iterated = True
        for index, chunk in enumerate(self.chunks):
            if index and self.gate:
                await self.gate.wait()
            yield chunk
        if self.error:
            raise RuntimeError('synthetic-secret-body-error')

    async def aclose(self):
        self.close_started.set()
        if self.close_gate:
            await self.close_gate.wait()
        self.closed.set()


def app_for(streams, *, status=200, headers=None, clock=None):
    """With `clock` (a fakes.Clock) the app's gate runs on it and waits at most
    SHORT_WAIT, so an upstream 429 is answered by the gate after a single send."""
    requests = []
    async def upstream(request):
        requests.append(request)
        return httpx2.Response(status, stream=streams[len(requests)-1], headers=headers)
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(upstream), follow_redirects=False)
    options = {} if clock is None else {'rate_limit_config': SHORT_WAIT,
                                        'gate_factory': clocked_gate(clock)}
    app = proxy.create_app(Session, lambda **kwargs: client, **options)
    return app, requests, client


async def call(app, *, send_hook=None, query=b'', body=b'', method='GET', disconnect=None, scopes=None):
    messages = []
    read = False
    async def receive():
        nonlocal read
        if not read:
            read = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        if disconnect:
            await disconnect.wait()
            return {'type': 'http.disconnect'}
        await asyncio.Event().wait()
    async def send(message):
        messages.append(message.copy())
        if send_hook:
            await send_hook(message)
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
             'http_version': '1.1', 'method': method, 'scheme': 'http',
             'path': '/v1/models' if method == 'GET' else '/v1/chat/completions',
             'raw_path': b'/v1/models', 'query_string': query, 'root_path': '',
             'headers': [], 'client': ('local', 1), 'server': ('local', 80)}
    if scopes is not None:
        scopes.append(scope)
    await app(scope, receive, send)
    return messages


@pytest.mark.anyio
async def test_query_order_blanks_and_raw_body_preserved():
    app, sent, _ = app_for([Stream()])
    raw = b' {"tools": [ ]}\n\x00'
    async with app.router.lifespan_context(app):
        await call(app, method='POST', query=b'a=one&b=&a=two&a=&flag&b=end', body=raw)
    assert parse_qsl(sent[0].url.query.decode(), keep_blank_values=True) == [('a','one'), ('b',''), ('a','two'), ('a',''), ('flag',''), ('b','end')]
    assert sent[0].method == 'POST' and sent[0].content == raw


@pytest.mark.anyio
async def test_first_chunk_precedes_upstream_completion():
    gate, first = asyncio.Event(), asyncio.Event()
    stream = Stream(gate=gate)
    app, _, _ = app_for([stream], headers={'content-type':'text/event-stream'})
    async def observe(message):
        if message.get('body') == b'first':
            first.set()
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(call(app, send_hook=observe))
        try:
            await asyncio.wait_for(first.wait(), 1)
            assert not task.done() and not stream.closed.is_set()
            gate.set()
            messages = await task
        finally:
            task.cancel()
    assert stream.closed.is_set()
    assert b''.join(m.get('body', b'') for m in messages) == b'firstlast'


@pytest.mark.anyio
async def test_cancel_response_start_closes_without_first_iteration():
    stream = Stream()
    app, _, _ = app_for([stream])
    async def cancel(message):
        if message['type'] == 'http.response.start':
            raise asyncio.CancelledError
    async with app.router.lifespan_context(app):
        with pytest.raises(asyncio.CancelledError):
            await call(app, send_hook=cancel)
        assert stream.closed.is_set()
        assert not stream.iterated



@pytest.mark.anyio
@pytest.mark.parametrize('status', [204, 205, 304])
async def test_bodyless_status_does_not_iterate_and_always_closes(status):
    stream = Stream()
    app, _, _ = app_for([stream], status=status)
    async with app.router.lifespan_context(app):
        messages = await call(app)
    assert messages[0]['status'] == status
    assert not b''.join(m.get('body', b'') for m in messages)
    assert not stream.iterated and stream.closed.is_set()


@pytest.mark.anyio
@pytest.mark.parametrize('status', [200, 401, 403, 429, 500, 302, 307])
async def test_decoded_payload_status_headers_and_no_replay_or_redirect(status):
    payload = b'{"tool_calls":[{"function":{"arguments":"{}"}}]}'
    stream = Stream((gzip.compress(payload),))
    app, requests, _ = app_for([stream], status=status, headers={
        'content-encoding': 'gzip', 'content-length':'999',
        'location':'https://other.example/secret', 'content-type':'application/custom+json'},
        clock=Clock())
    async with app.router.lifespan_context(app):
        messages = await asyncio.wait_for(call(app), 5)
    assert messages[0]['status'] == status and len(requests) == 1
    headers = dict(messages[0]['headers'])
    assert len(headers.pop(b'x-request-id')) == 32
    expected = {b'content-type':b'application/custom+json', b'cache-control':b'no-cache'}
    if status == 429:
        # The gate answers the 429 it could not wait out with a Retry-After of its own.
        expected[b'retry-after'] = b'1'
    assert headers == expected
    assert b''.join(m.get('body',b'') for m in messages) == payload
    assert stream.closed.is_set()


@pytest.mark.anyio
async def test_missing_type_falls_back_to_json():
    app, _, _ = app_for([Stream()])
    async with app.router.lifespan_context(app):
        messages = await call(app)
    assert dict(messages[0]['headers'])[b'content-type'] == b'application/json'


@pytest.mark.anyio
@pytest.mark.parametrize('stage', ['body', 'send', 'disconnect'])
async def test_interruption_closes_without_replacement_json(stage):
    from mvibe_proxy.streaming import StreamAborted
    gate = asyncio.Event()
    stream = Stream(error=stage == 'body', gate=gate if stage == 'disconnect' else None)
    app, _, _ = app_for([stream])
    messages = []
    async def observe(message):
        messages.append(message)
        if message.get('body') == b'first':
            if stage == 'send':
                raise OSError('synthetic-secret-send-error')
            if stage == 'disconnect':
                gate.set()
    async with app.router.lifespan_context(app):
        if stage == 'disconnect':
            # Leave the upstream second chunk blocked while the receive side
            # observes disconnect, instead of releasing the upstream gate.
            stream.gate = asyncio.Event()
            await call(app, send_hook=observe, disconnect=gate)
        else:
            with pytest.raises(StreamAborted) as error:
                await call(app, send_hook=observe)
            assert 'secret' not in str(error.value)
        assert stream.closed.is_set()
    assert len([m for m in messages if m['type'] == 'http.response.start']) == 1
    assert not any(m.get('more_body') is False for m in messages)
    assert not any(b'error' in m.get('body', b'') for m in messages)


@pytest.mark.anyio
async def test_repeated_task_cancel_drains_protected_close():
    first, close_gate = asyncio.Event(), asyncio.Event()
    stream = Stream(gate=asyncio.Event(), close_gate=close_gate)
    app, _, _ = app_for([stream])
    async def observe(message):
        if message.get('body') == b'first':
            first.set()
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(call(app, send_hook=observe))
        await asyncio.wait_for(first.wait(), 1)
        task.cancel()
        await asyncio.wait_for(stream.close_started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not stream.closed.is_set()
        close_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert stream.closed.is_set() and not app.state.stream_owner.retired


@pytest.mark.anyio
async def test_close_timeout_retires_client_aborts_sibling_and_rejects_next(caplog):
    from mvibe_proxy.streaming import StreamAborted
    first = asyncio.Event()
    sibling = Stream(gate=asyncio.Event())
    stalled = Stream(close_gate=asyncio.Event())
    app, requests, client = app_for([sibling, stalled])
    observed = []
    async def observe(message):
        observed.append(message)
        if message.get('body') == b'first':
            first.set()
    async with app.router.lifespan_context(app):
        app.state.stream_owner.close_timeout = 0.01
        running = asyncio.create_task(call(app, send_hook=observe))
        await asyncio.wait_for(first.wait(), 1)
        with pytest.raises(StreamAborted):
            await asyncio.wait_for(call(app), 1)
        with pytest.raises(StreamAborted):
            await asyncio.wait_for(running, 1)
        assert app.state.stream_owner.retired and client.is_closed
        assert sibling.closed.is_set()
        messages = await call(app)
        assert messages[0]['status'] == 502 and len(requests) == 2
        assert b''.join(m.get('body', b'') for m in messages) == b'{"error":{"message":"Upstream connection failed"}}'
        assert not any(m.get('more_body') is False for m in observed)
        assert 'proxy_cleanup_failed' in caplog.text and 'secret' not in caplog.text


@pytest.mark.anyio
async def test_lifespan_shutdown_is_bounded_even_if_client_close_stalls():
    app, _, client = app_for([])
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def stuck_close():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    client.aclose = stuck_close
    async def lifecycle():
        async with app.router.lifespan_context(app):
            app.state.stream_owner.close_timeout = 0.01
    await asyncio.wait_for(lifecycle(), 1)
    await asyncio.sleep(0)
    assert started.is_set() and cancelled.is_set()


@pytest.mark.anyio
async def test_route_constructs_response_before_acquiring_upstream():
    from starlette.requests import Request
    app, sent, _ = app_for([Stream()])
    async with app.router.lifespan_context(app):
        request = Request({'type':'http', 'app':app, 'method':'GET', 'query_string':b''})
        response = await app.routes[-1].endpoint('models', request)
        assert not sent
        task = asyncio.create_task(response({}, None, None))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not sent


@pytest.mark.anyio
async def test_cancel_during_body_send_closes_response():
    stream = Stream()
    app, _, _ = app_for([stream])
    async def cancel(message):
        if message.get('body') == b'first':
            raise asyncio.CancelledError
    async with app.router.lifespan_context(app):
        with pytest.raises(asyncio.CancelledError):
            await call(app, send_hook=cancel)
        assert stream.closed.is_set()


@pytest.mark.anyio
async def test_cancel_during_acquisition_does_not_start_headers_or_leak_task():
    session = Session()
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def blocked(url):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    session.resolve = blocked
    app = proxy.create_app(lambda: session, lambda **kwargs: httpx2.AsyncClient(transport=httpx2.MockTransport(lambda req: None)))
    messages = []
    async def observe(message):
        messages.append(message)
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(call(app, send_hook=observe))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set() and not messages
        assert not app.state.stream_owner._active


@pytest.mark.anyio
async def test_failed_close_retires_with_safe_diagnostic(caplog):
    from mvibe_proxy.streaming import StreamAborted
    stream = Stream()
    async def broken():
        raise OSError('synthetic-secret-close-error')
    stream.aclose = broken
    app, _, client = app_for([stream])
    async with app.router.lifespan_context(app):
        with pytest.raises(StreamAborted):
            await call(app)
        assert app.state.stream_owner.retired and client.is_closed
        assert (await call(app))[0]['status'] == 502
    assert 'proxy_cleanup_failed' in caplog.text and 'secret' not in caplog.text


@pytest.mark.anyio
async def test_bodyless_close_timeout_does_not_send_terminal_success():
    from mvibe_proxy.streaming import StreamAborted
    stream = Stream(close_gate=asyncio.Event())
    app, _, _ = app_for([stream], status=204)
    messages = []
    async def observe(message):
        messages.append(message)
    async with app.router.lifespan_context(app):
        app.state.stream_owner.close_timeout = 0.01
        with pytest.raises(StreamAborted):
            await asyncio.wait_for(call(app, send_hook=observe), 1)
        assert app.state.stream_owner.retired
    assert messages[0]['status'] == 204
    assert not any(m.get('more_body') is False for m in messages)


def blocked_acquisition_app():
    """An app whose credential resolve parks until cancelled; records upstream sends."""
    session = Session()
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def blocked(url):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    session.resolve = blocked
    requests = []
    def upstream(request):
        requests.append(request)
        return httpx2.Response(200, content=b'{}')
    app = proxy.create_app(lambda: session, lambda **kwargs: httpx2.AsyncClient(transport=httpx2.MockTransport(upstream)))
    return app, requests, entered, stopped


@pytest.mark.anyio
@pytest.mark.parametrize('method', ['GET', 'POST'])
async def test_disconnect_while_acquiring_cancels_without_response_start(method):
    app, requests, entered, stopped = blocked_acquisition_app()
    disconnect = asyncio.Event()
    scopes = []
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(call(app, method=method, body=b'{"model":"m"}' if method == 'POST' else b'',
                                        disconnect=disconnect, scopes=scopes))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            disconnect.set()
            # Returns normally: a client that left is not a stream abort.
            messages = await asyncio.wait_for(task, 1)
        finally:
            task.cancel()
        assert stopped.is_set()
        assert not any(m['type'] == 'http.response.start' for m in messages)
        assert not messages and not requests
        assert scopes[0]['mvibe.diagnostic'].outcome == 'cancelled'
        assert not app.state.stream_owner._active


@pytest.mark.anyio
@pytest.mark.parametrize('surfaced', ['auth', 'connection'])
async def test_a_cancellation_surfacing_as_an_error_after_disconnect_stays_cancelled(surfaced):
    from mvibe_auth.api import AuthError, AuthFailure
    session = Session()
    entered = asyncio.Event()
    async def blocked(url):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # A layer that turns its cancellation into an ordinary error.
            if surfaced == 'auth':
                raise AuthError(AuthFailure('rejected')) from None
            raise RuntimeError('synthetic-secret-transport-error') from None
    session.resolve = blocked
    requests = []
    def upstream(request):
        requests.append(request)
        return httpx2.Response(200, content=b'{}')
    app = proxy.create_app(lambda: session, lambda **kwargs: httpx2.AsyncClient(transport=httpx2.MockTransport(upstream)))
    disconnect = asyncio.Event()
    scopes = []
    async with app.router.lifespan_context(app):
        task = asyncio.create_task(call(app, method='POST', body=b'{"model":"m"}',
                                        disconnect=disconnect, scopes=scopes))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            disconnect.set()
            messages = await asyncio.wait_for(task, 1)
        finally:
            task.cancel()
        # The client left first: no error response, and the outcome stays cancelled.
        assert not messages and not requests
        assert scopes[0]['mvibe.diagnostic'].outcome == 'cancelled'
        assert not app.state.stream_owner._active and app.state.rate_gate.held == 0


@pytest.mark.anyio
@pytest.mark.parametrize('status', [429, 200])
async def test_rate_limit_headers_reach_the_client_only_on_429(status):
    payload = b'{"message":"Requests rate limit exceeded"}'
    app, requests, _ = app_for([Stream((payload,))], status=status, headers=[
        ('content-type', 'application/json'), ('retry-after', '2'),
        ('x-ratelimit-remaining-req-minute', '0'), ('X-RateLimit-Limit-Req-Minute', '30'),
        ('x-other', 'never-forwarded'), ('content-length', str(len(payload)))], clock=Clock())
    async with app.router.lifespan_context(app):
        messages = await asyncio.wait_for(call(app), 5)
    assert messages[0]['status'] == status and len(requests) == 1
    headers = [h for h in messages[0]['headers'] if h[0] != b'x-request-id']
    expected = [(b'content-type', b'application/json'), (b'cache-control', b'no-cache')]
    if status == 429:
        # Through the gate, which keeps the upstream's x-ratelimit-* headers in
        # their order and its usable Retry-After after them.
        expected += [(b'x-ratelimit-remaining-req-minute', b'0'),
                     (b'x-ratelimit-limit-req-minute', b'30'), (b'retry-after', b'2')]
    assert headers == expected
    assert b''.join(m.get('body', b'') for m in messages) == payload


class ClosableClient:
    async def aclose(self):
        pass


def watching_owner():
    from mvibe_proxy.diagnostics import RequestDiagnostic
    from mvibe_proxy.streaming import ProxyStreamOwner
    return ProxyStreamOwner(ClosableClient()), RequestDiagnostic()


@pytest.mark.anyio
async def test_acquire_watching_returns_the_upstream_response():
    owner, diagnostic = watching_owner()
    watched, released = asyncio.Event(), asyncio.Event()
    async def receive():
        if not watched.is_set():
            watched.set()
            # Anything but a disconnect is ignored while acquiring.
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        try:
            await asyncio.Event().wait()
        finally:
            released.set()
    stream = Stream()
    upstream = httpx2.Response(429, headers={'retry-after': '2'}, stream=stream)
    async def acquire():
        await asyncio.wait_for(watched.wait(), 1)
        await asyncio.sleep(0)
        return upstream
    result = await asyncio.wait_for(owner.acquire_watching(receive, acquire, diagnostic), 1)
    # Handed back unread and open: the caller owns it from here.
    assert result is upstream and not stream.iterated and not stream.close_started.is_set()
    assert released.is_set() and diagnostic.outcome == 'completed'
    assert not owner._active


@pytest.mark.anyio
async def test_acquire_watching_disconnect_cancels_acquisition():
    from mvibe_proxy.streaming import ClientDisconnected
    owner, diagnostic = watching_owner()
    gone, entered, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finished = []
    async def receive():
        await gone.wait()
        return {'type': 'http.disconnect'}
    async def acquire():
        entered.set()
        try:
            await asyncio.Event().wait()
            finished.append('sent upstream')
        finally:
            stopped.set()
    task = asyncio.create_task(owner.acquire_watching(receive, acquire, diagnostic))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        gone.set()
        with pytest.raises(ClientDisconnected):
            await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
    assert stopped.is_set() and not finished
    assert diagnostic.outcome == 'cancelled'
    assert not owner._active


@pytest.mark.anyio
async def test_acquire_watching_closes_a_response_won_by_a_racing_disconnect():
    from mvibe_proxy.streaming import ClientDisconnected
    owner, diagnostic = watching_owner()
    stream = Stream()
    upstream = httpx2.Response(200, stream=stream)
    async def receive():
        return {'type': 'http.disconnect'}
    async def acquire():
        # Finish acquiring even though the watcher saw the disconnect meanwhile.
        with anyio.CancelScope(shield=True):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        return upstream
    with pytest.raises(ClientDisconnected):
        await asyncio.wait_for(owner.acquire_watching(receive, acquire, diagnostic), 1)
    assert stream.closed.is_set() and diagnostic.outcome == 'cancelled'


@pytest.mark.anyio
async def test_owned_response_sends_nothing_when_a_disconnect_races_acquisition():
    owner, diagnostic = watching_owner()
    stream = Stream()
    upstream = httpx2.Response(429, headers={'retry-after': '2'}, stream=stream)
    async def receive():
        return {'type': 'http.disconnect'}
    async def acquire():
        # Finish acquiring even though the watcher saw the disconnect meanwhile.
        with anyio.CancelScope(shield=True):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        return upstream
    messages = []
    async def send(message):
        messages.append(message)
    await asyncio.wait_for(owner.response(acquire, diagnostic)({'type': 'http'}, receive, send), 1)
    assert not messages and not stream.iterated and stream.closed.is_set()
    assert diagnostic.outcome == 'cancelled' and not owner._active


@pytest.mark.anyio
async def test_acquire_watching_propagates_acquisition_errors_unwrapped():
    from mvibe_auth.api import AuthError, AuthFailure
    owner, diagnostic = watching_owner()
    async def receive():
        await asyncio.Event().wait()
    async def acquire():
        await asyncio.sleep(0)
        raise AuthError(AuthFailure('rejected'))
    with pytest.raises(AuthError):
        await asyncio.wait_for(owner.acquire_watching(receive, acquire, diagnostic), 1)
    assert diagnostic.outcome == 'completed' and not owner._active


@pytest.mark.anyio
async def test_acquire_watching_respects_retirement():
    from mvibe_proxy.streaming import StreamAborted
    owner, diagnostic = watching_owner()
    calls, entered, stopped = [], asyncio.Event(), asyncio.Event()
    async def receive():
        await asyncio.Event().wait()
    async def never():
        calls.append('acquire')
    owner.retired = True
    with pytest.raises(StreamAborted):
        await owner.acquire_watching(receive, never, diagnostic)
    assert not calls

    owner, diagnostic = watching_owner()
    async def held():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    task = asyncio.create_task(owner.acquire_watching(receive, held, diagnostic))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await owner.shutdown()
        with pytest.raises(StreamAborted):
            await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
    assert stopped.is_set() and not owner._active
