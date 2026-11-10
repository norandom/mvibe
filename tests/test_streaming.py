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


async def call(app, *, send_hook=None, query=b'', body=b'', method='GET', disconnect=None, scopes=None,
               path=None):
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
             'path': path or ('/v1/models' if method == 'GET' else '/v1/chat/completions'),
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


# ---- Read-only observer tap (proxy-stats-propagation 1.1) ----

class Tap:
    """An observe_factory and its observer, recording what each was handed.

    `raise_on` is the index of the observe() call that raises; `factory_error`
    makes the factory itself raise."""
    def __init__(self, *, raise_on=None, factory_error=False):
        self.upstreams = []
        self.seen = []
        self.raise_on = raise_on
        self.factory_error = factory_error

    def factory(self, upstream):
        self.upstreams.append(upstream)
        if self.factory_error:
            raise RuntimeError('synthetic-secret-factory-error')
        return self.observe

    def observe(self, chunk):
        self.seen.append(chunk)
        if self.raise_on == len(self.seen) - 1:
            raise RuntimeError('synthetic-secret-observer-error')

    @property
    def chunks(self):
        return [chunk for chunk in self.seen if chunk is not None]


class Upper:
    """A stand-in adapter transform: feed() rewrites each chunk, finish() adds a tail."""
    def feed(self, chunk):
        return chunk.upper()

    def finish(self):
        return b'<tail>'


TERMINAL = {'type': 'http.response.body', 'body': b'', 'more_body': False}


def tapped(app, factory):
    """Make every response the app's routes build pass `factory` as observe_factory.

    The routes call response(acquire, diagnostic[, transform]) positionally and,
    since task 3.2, pass their own usage observe_factory as a keyword; the test's
    factory replaces it so these tests observe exactly what they inject."""
    owner = app.state.stream_owner
    plain = owner.response
    owner.response = lambda acquire, diagnostic=None, transform=None, observe_factory=None: plain(
        acquire, diagnostic, transform, observe_factory=factory)


def wire(messages):
    """The response as the client saw it, minus the per-request id."""
    return [{**m, 'headers': [h for h in m['headers'] if h[0] != b'x-request-id']}
            if m['type'] == 'http.response.start' else m for m in messages]


async def tapped_run(tap, chunks=(b'first', b'last'), *, headers=None, path=None, body=b'{"model":"m"}'):
    """One POST through a route; `tap=None` is the no-observer reference run."""
    app, _, _ = app_for([Stream(chunks)], headers=headers)
    scopes = []
    async with app.router.lifespan_context(app):
        if tap is not None:
            tapped(app, tap.factory)
        messages = await asyncio.wait_for(
            call(app, method='POST', body=body, path=path, scopes=scopes), 5)
    return wire(messages), scopes[0]['mvibe.diagnostic'].outcome


async def owned_run(upstream, *, factory=None, transform=None):
    """Serve one acquired upstream response straight from a ProxyStreamOwner."""
    owner, diagnostic = watching_owner()
    async def receive():
        await asyncio.Event().wait()
    async def acquire():
        await asyncio.sleep(0)
        return upstream
    messages = []
    async def send(message):
        messages.append(message.copy())
    if factory is None:
        # Positional, as every route that predates the observer calls it.
        response = owner.response(acquire, diagnostic, transform)
    else:
        response = owner.response(acquire, diagnostic, transform, observe_factory=factory)
    await asyncio.wait_for(response({'type': 'http'}, receive, send), 1)
    assert not owner._active
    return messages, diagnostic


@pytest.mark.anyio
@pytest.mark.parametrize('encoding', [None, 'gzip'])
async def test_observer_sees_every_upstream_chunk_then_completion_and_the_response_is_unchanged(encoding):
    chunks = (b'data: {"id":"c-1"}\n\n', b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n',
              b'data: [DONE]\n\n')
    body = b''.join(chunks)
    headers = {'content-type': 'text/event-stream'}
    upstream_chunks = chunks
    if encoding:
        # The observer sees what the client is sent: the decoded body.
        headers['content-encoding'] = encoding
        upstream_chunks = (gzip.compress(body),)
    plain, plain_outcome = await tapped_run(None, upstream_chunks, headers=headers)
    tap = Tap()
    observed, outcome = await tapped_run(tap, upstream_chunks, headers=headers)
    assert observed == plain and outcome == plain_outcome == 'completed'
    assert b''.join(m.get('body', b'') for m in observed) == body and observed[-1] == TERMINAL
    # One factory call, with the acquired upstream response: status and headers readable.
    assert len(tap.upstreams) == 1 and isinstance(tap.upstreams[0], httpx2.Response)
    assert tap.upstreams[0].status_code == 200
    assert tap.upstreams[0].headers['content-type'] == 'text/event-stream'
    # Every chunk in order, then exactly one terminal None.
    assert tap.seen[-1] is None and tap.seen.count(None) == 1
    assert b''.join(tap.chunks) == body
    if encoding is None:
        assert tap.chunks == list(chunks)


@pytest.mark.anyio
async def test_adapter_replay_acquire_hands_the_factory_the_raw_upstream_before_the_transform():
    owner, diagnostic = watching_owner()
    stream = Stream((b'raw-one', b'raw-two'))
    upstream = httpx2.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)
    async def receive():
        await asyncio.Event().wait()
    async def acquire():
        await asyncio.sleep(0)
        return upstream
    # As adapter_post: acquire while watching, inspect, then replay that response.
    acquired = await asyncio.wait_for(owner.acquire_watching(receive, acquire, diagnostic), 1)
    async def replay():
        return acquired
    log = []
    def factory(response):
        log.append(('factory', response))
        return lambda chunk: log.append(('observe', chunk))
    async def send(message):
        log.append((message['type'].rsplit('.', 1)[1], message.get('body')))
    response = owner.response(replay, diagnostic, Upper(), observe_factory=factory)
    await asyncio.wait_for(response({'type': 'http'}, receive, send), 1)
    # Made once, before the response starts; each raw chunk is observed before
    # its transformed form is sent; completion is observed once the body is read.
    assert log == [('factory', upstream), ('start', None),
                   ('observe', b'raw-one'), ('body', b'RAW-ONE'),
                   ('observe', b'raw-two'), ('body', b'RAW-TWO'),
                   ('observe', None), ('body', b'<tail>'), ('body', b'')]
    assert log[0][1] is upstream and stream.closed.is_set()
    assert diagnostic.outcome == 'completed' and not owner._active


ANTHROPIC_STREAM = (
    b'data: {"id":"chat-1","choices":[{"delta":{"role":"assistant","content":""}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n',
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n',
    b'data: [DONE]\n\n')
OLLAMA_JSON = (b'{"id":"chat-1","model":"m","choices":[{"index":0,"finish_reason":"stop",',
               b'"message":{"role":"assistant","content":"OK"}}],"usage":{"prompt_tokens":8,"completion_tokens":2}}')


@pytest.mark.anyio
@pytest.mark.parametrize('path,body,chunks,content_type', [
    ('/v1/messages', b'{"model":"m","max_tokens":16,"stream":true,"messages":[{"role":"user","content":"hi"}]}',
     ANTHROPIC_STREAM, 'text/event-stream'),
    ('/api/chat', b'{"model":"m","stream":false,"messages":[{"role":"user","content":"hi"}]}',
     OLLAMA_JSON, 'application/json'),
], ids=['anthropic-stream', 'ollama-json'])
async def test_adapter_routes_observe_the_upstream_bytes_not_the_translation(path, body, chunks, content_type,
                                                                             monkeypatch):
    from mvibe_proxy import compat
    # Ollama bodies carry a wall-clock created_at; fix it so the two runs compare.
    monkeypatch.setattr(compat, '_now', lambda: '2026-01-01T00:00:00+00:00')
    headers = {'content-type': content_type}
    plain, _ = await tapped_run(None, chunks, headers=headers, path=path, body=body)
    tap = Tap()
    observed, outcome = await tapped_run(tap, chunks, headers=headers, path=path, body=body)
    assert observed == plain and outcome == 'completed' and observed[-1] == TERMINAL
    translated = b''.join(m.get('body', b'') for m in observed)
    assert translated and translated != b''.join(chunks)
    assert len(tap.upstreams) == 1 and tap.seen == [*chunks, None]


@pytest.mark.anyio
async def test_a_raising_observe_factory_leaves_the_response_unchanged():
    plain, _ = await tapped_run(None)
    tap = Tap(factory_error=True)
    observed, outcome = await tapped_run(tap)
    assert observed == plain and observed[-1] == TERMINAL and outcome == 'completed'
    assert len(tap.upstreams) == 1 and not tap.seen


@pytest.mark.anyio
@pytest.mark.parametrize('raise_on,seen', [
    (0, [b'one']), (1, [b'one', b'two']), (3, [b'one', b'two', b'three', None])],
    ids=['first-chunk', 'second-chunk', 'completion'])
async def test_an_observer_that_raises_is_dropped_and_the_response_is_unchanged(raise_on, seen):
    chunks = (b'one', b'two', b'three')
    plain, _ = await tapped_run(None, chunks)
    tap = Tap(raise_on=raise_on)
    observed, outcome = await tapped_run(tap, chunks)
    assert observed == plain and observed[-1] == TERMINAL and outcome == 'completed'
    # Never called again after the call that raised.
    assert tap.seen == seen


@pytest.mark.anyio
@pytest.mark.parametrize('stage', ['body', 'send', 'disconnect', 'cancel'])
async def test_an_interrupted_stream_never_reports_completion_to_the_observer(stage):
    from mvibe_proxy.streaming import StreamAborted
    gone = asyncio.Event()
    stream = Stream(error=stage == 'body', gate=asyncio.Event() if stage == 'disconnect' else None)
    app, _, _ = app_for([stream])
    tap = Tap()
    async def interrupt(message):
        if message.get('body') == b'first':
            if stage == 'send':
                raise OSError('synthetic-secret-send-error')
            if stage == 'cancel':
                raise asyncio.CancelledError
            if stage == 'disconnect':
                gone.set()
    async with app.router.lifespan_context(app):
        tapped(app, tap.factory)
        if stage == 'disconnect':
            # The upstream's second chunk stays blocked while the client leaves.
            await asyncio.wait_for(call(app, send_hook=interrupt, disconnect=gone), 1)
        elif stage == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await call(app, send_hook=interrupt)
        else:
            with pytest.raises(StreamAborted):
                await asyncio.wait_for(call(app, send_hook=interrupt), 1)
        assert stream.closed.is_set()
    assert len(tap.upstreams) == 1 and None not in tap.seen
    # A body error after the last chunk was read still is not a completion.
    assert tap.seen == ([b'first', b'last'] if stage == 'body' else [b'first'])


@pytest.mark.anyio
async def test_no_observer_is_made_when_the_client_left_before_the_response_started():
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
    tap = Tap()
    messages = []
    async def send(message):
        messages.append(message)
    response = owner.response(acquire, diagnostic, observe_factory=tap.factory)
    await asyncio.wait_for(response({'type': 'http'}, receive, send), 1)
    assert not messages and stream.closed.is_set() and diagnostic.outcome == 'cancelled'
    assert not tap.upstreams and not tap.seen


@pytest.mark.anyio
async def test_completion_follows_the_upstream_body_even_when_its_close_then_fails():
    # The whole upstream body was read (its tokens were spent) before the close
    # failed and retired the client: the observer is told; the client is not.
    from mvibe_proxy.streaming import StreamAborted
    stream = Stream()
    async def broken():
        raise OSError('synthetic-secret-close-error')
    stream.aclose = broken
    app, _, _ = app_for([stream])
    tap = Tap()
    messages = []
    async def record(message):
        messages.append(message)
    async with app.router.lifespan_context(app):
        tapped(app, tap.factory)
        with pytest.raises(StreamAborted):
            await asyncio.wait_for(call(app, send_hook=record), 1)
        assert app.state.stream_owner.retired
    assert tap.seen == [b'first', b'last', None]
    assert not any(m.get('more_body') is False for m in messages)


@pytest.mark.anyio
@pytest.mark.parametrize('status', [204, 205, 304])
async def test_a_bodyless_status_gets_an_observer_that_sees_no_chunks_and_no_completion(status):
    tap = Tap()
    stream = Stream()
    upstream = httpx2.Response(status, stream=stream)
    messages, diagnostic = await owned_run(upstream, factory=tap.factory)
    assert messages[0]['status'] == status and messages[1:] == [TERMINAL]
    assert len(tap.upstreams) == 1 and tap.upstreams[0] is upstream
    assert tap.seen == [] and not stream.iterated and stream.closed.is_set()
    assert diagnostic.outcome == 'completed'


@pytest.mark.anyio
@pytest.mark.parametrize('status', [429, 500])
async def test_an_error_status_body_is_observed_like_any_other(status):
    payload = (b'{"error":', b'{"message":"busy"}}')
    tap = Tap()
    upstream = httpx2.Response(status, headers={'content-type': 'application/json'}, stream=Stream(payload))
    messages, _ = await owned_run(upstream, factory=tap.factory)
    assert messages[0]['status'] == status and messages[-1] == TERMINAL
    assert b''.join(m.get('body', b'') for m in messages) == b''.join(payload)
    assert tap.upstreams[0] is upstream and tap.seen == [*payload, None]


@pytest.mark.anyio
async def test_without_a_factory_the_stream_never_enters_the_observer_path(monkeypatch):
    from mvibe_proxy import streaming
    tapped_chunks = []
    def recording(observe, chunk):
        tapped_chunks.append(chunk)
        return True
    monkeypatch.setattr(streaming, '_observe', recording)
    messages, diagnostic = await owned_run(httpx2.Response(200, stream=Stream()), transform=Upper())
    assert b''.join(m.get('body', b'') for m in messages) == b'FIRSTLAST<tail>'
    assert messages[-1] == TERMINAL and diagnostic.outcome == 'completed'
    assert tapped_chunks == []
    # Control: the same path with a factory does go through the tap.
    await owned_run(httpx2.Response(200, stream=Stream()), factory=Tap().factory, transform=Upper())
    assert tapped_chunks == [b'first', b'last', None]
