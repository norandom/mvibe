"""Offline callback contracts; all listeners, requests and deadlines are synthetic."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mvibe_auth._oidc_callback import OidcCallback
from mvibe_auth._oidc_config import OidcError


URI = "http://127.0.0.1:8766/callback"
STATE = "synthetic-state"


class Listener:
    def __init__(self):
        self.closed = False
        self.joined = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        assert self.closed
        self.joined = True


@pytest.fixture
def listener(monkeypatch):
    server = Listener()

    async def start(handler, host, port, *, limit):
        assert (host, port, limit) == ("127.0.0.1", 8766, 8192)
        server.handler = handler
        return server

    monkeypatch.setattr(asyncio, "start_server", start)
    return server


def request(query, *, method="GET", path="/callback", version="HTTP/1.1"):
    return f"{method} {path}?{query} {version}\r\nHost: 127.0.0.1\r\n\r\n".encode()


@pytest.mark.anyio
@pytest.mark.parametrize("query", [
    "state=synthetic-state&state=&code=one",
    "state=&state=synthetic-state&code=one",
    "state=synthetic-state&state=other&code=one",
    "state=synthetic-state&code=one&code=",
    "state=synthetic-state&code=&code=one",
    "state=synthetic-state&code=one&code=two",
    "state=&code=one", "code=one", "state=wrong&code=one",
    "state=synthetic-state&code=", "state=synthetic-state",
    "state=synthetic-state&error=", "state=synthetic-state&error=denied&error=",
    "state=synthetic-state&error=denied&error=other",
    "state=synthetic-state&error=denied&code=one",
    "state=synthetic-state&state=&error=access_denied",
])
async def test_invalid_callback_cannot_resolve(query, listener, controlled_callback):
    callback = OidcCallback(URI, STATE)
    async with callback:
        reader, writer = controlled_callback(request(query))
        await listener.handler(reader, writer)
        # Malformed/unrelated requests do not terminate the legitimate browser wait.
        assert not callback.result.done()
        assert writer.closed
        assert writer.data.startswith(b"HTTP/1.1 400 Bad Request")
        assert not callback._connections
    assert listener.closed and listener.joined
    assert callback.result.cancelled()


@pytest.mark.anyio
@pytest.mark.parametrize("kwargs", [
    {"method": "POST"}, {"path": "/wrong"},
    {"path": "http://127.0.0.1:8766/callback"}, {"path": "//127.0.0.1/callback"},
    {"version": "garbage"}, {"path": "/callback#fragment"},
])
async def test_request_target_and_method_rejected(kwargs, listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        reader, writer = controlled_callback(request("state=synthetic-state&code=one", **kwargs))
        await listener.handler(reader, writer)
        assert not callback.result.done()
        assert writer.closed
    assert listener.closed and listener.joined


@pytest.mark.anyio
async def test_valid_callback_resolves_once(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        for code in ("first", "second"):
            reader, writer = controlled_callback(request(f"state={STATE}&code={code}"))
            await listener.handler(reader, writer)
            assert writer.closed
            assert b"Cache-Control: no-store" in writer.data
            assert code.encode() not in writer.data
        assert await callback.result == f"{URI}?state={STATE}&code=first"
        assert writer.data.startswith(b"HTTP/1.1 400 Bad Request")
    assert listener.closed and listener.joined
    assert not callback._connections


@pytest.mark.anyio
async def test_concurrent_callbacks_preserve_first_result(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        pairs = [controlled_callback(request(f"state={STATE}&code={code}")) for code in ("one", "two")]
        await asyncio.gather(*(listener.handler(reader, writer) for reader, writer in pairs))
        assert await callback.result in (f"{URI}?state={STATE}&code=one", f"{URI}?state={STATE}&code=two")
        assert sum(writer.data.startswith(b"HTTP/1.1 200 OK") for _, writer in pairs) == 1
        assert all(writer.closed for _, writer in pairs)
        assert not callback._connections


@pytest.mark.anyio
async def test_rejected_request_does_not_prevent_valid_callback(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        for query in ("state=wrong&code=one", f"state={STATE}&code=valid"):
            reader, writer = controlled_callback(request(query))
            await listener.handler(reader, writer)
            assert writer.closed
        assert await callback.result == f"{URI}?state={STATE}&code=valid"


@pytest.mark.anyio
async def test_matching_denial_is_safe_failure(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        reader, writer = controlled_callback(request(f"state={STATE}&error=access_denied&error_description=private"))
        await listener.handler(reader, writer)
        with pytest.raises(OidcError, match="^OIDC authorization was declined$"):
            await callback.result
        assert b"200 OK" in writer.data and b"private" not in writer.data
        assert writer.closed
    assert listener.closed and listener.joined


class BlockedReader:
    def __init__(self):
        self.started = asyncio.Event()
        self.stopped = False

    async def readuntil(self, separator):
        assert separator == b"\r\n\r\n"
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.stopped = True


@pytest.mark.anyio
async def test_connection_limit_and_shutdown_drains_tasks(listener, controlled_callback):
    callback = OidcCallback(URI, STATE)
    await callback.__aenter__()
    active = []
    for _ in range(8):
        reader = BlockedReader()
        _, writer = controlled_callback(b"")
        task = asyncio.create_task(listener.handler(reader, writer))
        await reader.started.wait()
        active.append((reader, writer, task))
    reader, refused = controlled_callback(request(f"state={STATE}&code=one"))
    await listener.handler(reader, refused)
    assert refused.closed and not callback.result.done()
    await callback.__aexit__()
    assert listener.closed and listener.joined and callback.result.cancelled()
    assert not callback._connections
    assert all(reader.stopped and writer.closed and task.done() for reader, writer, task in active)
    # A connection callback already queued when the server closed is rejected too.
    reader, late = controlled_callback(request(f"state={STATE}&code=late"))
    await listener.handler(reader, late)
    assert late.closed and not late.data
    await callback.__aexit__()  # repeated cleanup is harmless


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [b"broken\r\n\r\n", b"\xff\r\n\r\n", b"GET /", b"x" * 9000 + b"\r\n\r\n"])
async def test_malformed_and_oversized_requests(payload, listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        # Model the production stream limit without binding a socket.
        reader = asyncio.StreamReader(limit=8192)
        reader.feed_data(payload)
        reader.feed_eof()
        _, writer = controlled_callback(b"")
        await listener.handler(reader, writer)
        assert writer.closed and not callback.result.done()
        assert not callback._connections
    assert listener.closed and listener.joined


@pytest.mark.anyio
async def test_read_and_drain_deadlines_are_bounded(monkeypatch, listener, controlled_callback):
    real_wait_for = asyncio.wait_for
    deadlines = []

    async def expire(awaitable, timeout):
        deadlines.append(timeout)
        if len(deadlines) == 1:
            # The controlled pending read is cancelled immediately, no clock wait.
            return await real_wait_for(awaitable, 0)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", expire)
    async with OidcCallback(URI, STATE) as callback:
        reader = BlockedReader()
        _, writer = controlled_callback(b"")
        await listener.handler(reader, writer)
        assert writer.closed and not callback.result.done()
    assert deadlines[0] == 5 and listener.closed and listener.joined


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["drain", "wait_closed"])
async def test_write_deadlines_and_errors_release_activity(stage, monkeypatch, listener, controlled_callback):
    real_wait_for = asyncio.wait_for
    deadlines = []

    async def wait_for(awaitable, timeout):
        deadlines.append(timeout)
        return await real_wait_for(awaitable, 0 if awaitable.cr_code.co_name == "stall" else timeout)

    async def stall():
        await asyncio.Future()

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    async with OidcCallback(URI, STATE) as callback:
        reader, writer = controlled_callback(request("state=wrong&code=one"))
        setattr(writer, stage, stall)
        await listener.handler(reader, writer)
        assert writer.closed and not callback._connections and not callback.result.done()
    assert deadlines and set(deadlines) == {5}
    assert listener.closed and listener.joined


@pytest.mark.anyio
async def test_transport_error_during_close_is_contained(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        reader, writer = controlled_callback(request("state=wrong&code=one"))

        async def broken_close():
            raise OSError("synthetic closed transport")

        writer.wait_closed = broken_close
        await listener.handler(reader, writer)
        assert writer.closed and not callback._connections and not callback.result.done()


@pytest.mark.anyio
async def test_complete_oversized_header_cannot_resolve(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        data = request(f"state={STATE}&code=one")[:-2] + b"X-Padding: " + b"x" * 8192 + b"\r\n\r\n"
        reader, writer = controlled_callback(data)
        await listener.handler(reader, writer)
        assert writer.closed and not callback.result.done()


@pytest.mark.anyio
async def test_fragment_and_empty_state_cannot_resolve(listener, controlled_callback):
    for expected_state, data in (
        (STATE, b"GET /callback?state=synthetic-state&code=one#fragment HTTP/1.1\r\n\r\n"),
        ("", request("state=&code=one")),
    ):
        async with OidcCallback(URI, expected_state) as callback:
            reader, writer = controlled_callback(data)
            await listener.handler(reader, writer)
            assert writer.closed and not callback.result.done()


@pytest.mark.anyio
async def test_shutdown_waits_for_writer_close(listener, controlled_callback):
    async with OidcCallback(URI, STATE):
        reader, writer = controlled_callback(request(f"state={STATE}&code=one"))
        joined = []

        async def wait_closed():
            assert writer.closed
            joined.append(True)

        writer.wait_closed = wait_closed
        await listener.handler(reader, writer)
        assert joined == [True]


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_login_deadline_and_cancellation_release_callback(monkeypatch, listener, cancel):
    from mvibe_auth._auth_config import AuthConfig
    from mvibe_auth._oidc_service import OidcAttempt, OidcService

    config = AuthConfig(enabled=True)
    reached_wait = asyncio.Event()

    class Session:
        closed = False

        def create_authorization_url(self, endpoint, **kwargs):
            reached_wait.set()
            return "https://issuer.invalid/authorize", kwargs["state"]

        async def aclose(self):
            self.closed = True

    session = Session()

    async def discover(session):
        return SimpleNamespace(authorization_endpoint="https://issuer.invalid/authorize")

    client = SimpleNamespace(config=config.to_oidc_config(), session=lambda: session, discover=discover)
    service = OidcService(client, object(), config=config)
    callback_instances = []

    class CapturedCallback(OidcCallback):
        def __init__(self, *args):
            super().__init__(*args)
            callback_instances.append(self)

    monkeypatch.setattr("mvibe_auth._oidc_service.OidcCallback", CapturedCallback)
    real_timeout = asyncio.timeout
    deadlines = []
    timeout_contexts = []

    def controlled_timeout(seconds):
        deadlines.append(seconds)
        context = real_timeout(None)
        timeout_contexts.append(context)
        return context

    monkeypatch.setattr(asyncio, "timeout", controlled_timeout)
    attempt = OidcAttempt("synthetic-attempt")
    task = asyncio.create_task(service._complete(attempt))
    await reached_wait.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        timeout_contexts[0].reschedule(asyncio.get_running_loop().time())
        await task
        assert attempt.status == "failed" and attempt.detail
    assert deadlines == [300]
    assert listener.closed and listener.joined and session.closed
    assert callback_instances[0].result.cancelled()
    assert not callback_instances[0]._connections
