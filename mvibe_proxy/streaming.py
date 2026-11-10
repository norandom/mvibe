"""ASGI response ownership and bounded shared-client retirement."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import TYPE_CHECKING

import anyio
import httpx2
from starlette.responses import JSONResponse, Response

from mvibe_auth.api import AuthError

if TYPE_CHECKING:
    from mvibe_proxy.diagnostics import RequestDiagnostic

logger = logging.getLogger('mvibe.proxy')

# A read-only tap on one upstream response: called with each raw upstream
# chunk, then once with None when the body has been read to its end.
Observer = Callable[[bytes | None], None]
# Builds the tap for an acquired upstream response (status and headers set,
# body unread); None means nothing is observed.
ObserverFactory = Callable[[httpx2.Response], Observer | None]


class StreamAborted(RuntimeError):
    """A started response cannot be completed after transport retirement."""


class ClientDisconnected(RuntimeError):
    """The client left while its upstream response was still being acquired."""


async def _watch_disconnect(receive: Callable[[], Awaitable[dict]],
                            on_disconnect: Callable[[], None]) -> None:
    # Routes read the request body before acquiring, so receive() can only
    # report the client leaving; any other message is ignored.
    while True:
        message = await receive()
        if message['type'] == 'http.disconnect':
            on_disconnect()
            return


def _observe(observe: Observer, chunk: bytes | None) -> bool:
    """Hand one chunk, or the terminal None, to an observer; never raises.

    False means the observer failed: the caller drops it for the rest of the
    response, so forwarded bytes and completion never depend on it."""
    try:
        observe(chunk)
    except Exception:
        return False
    return True


def _start_headers(upstream) -> list[tuple[bytes, bytes]]:
    headers = [(b'content-type', upstream.headers.get('content-type', 'application/json').encode('latin-1')),
               (b'cache-control', b'no-cache')]
    if upstream.status_code == 429:
        # Rate-limit guidance reaches the client only with a 429; every other
        # status keeps exactly the two headers above.
        headers += [(name.lower(), value) for name, value in upstream.headers.raw
                    if name.lower() == b'retry-after' or name.lower().startswith(b'x-ratelimit-')]
    return headers


class ProxyStreamOwner:
    """Lifespan owner. A failed close permanently retires this client."""

    def __init__(self, client, *, close_timeout: float = 2.0):
        self.client = client
        self.close_timeout = close_timeout
        self.retired = False
        self._active: set[anyio.CancelScope] = set()
        self._client_close_attempted = False

    async def _bounded(self, operation: Awaitable[None]) -> bool:
        task = asyncio.create_task(operation)
        done, _ = await asyncio.wait({task}, timeout=self.close_timeout)
        if not done:
            task.cancel()
            # Cancellation of a broken transport must not make shutdown unbounded.
            task.add_done_callback(self._consume)
            return False
        try:
            task.result()
        except BaseException:
            return False
        return True

    @staticmethod
    def _consume(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    async def _close_client(self) -> None:
        if not self._client_close_attempted:
            self._client_close_attempted = True
            await self._bounded(self.client.aclose())

    async def _close_stream(self, stream, diagnostic=None) -> None:
        if not await self._bounded(stream.aclose()):
            self.retired = True
            logger.warning('proxy_cleanup_failed stage=response reason=close_failed')
            if diagnostic is not None:
                diagnostic.failure('cleanup')
            for scope in tuple(self._active):
                scope.cancel()
            await self._close_client()

    async def _finish(self, upstream) -> None:
        if upstream is not None:
            await upstream.aclose()

    async def _protected(self, operation: Awaitable[None]) -> None:
        # A separate task also protects against repeated asyncio.Task.cancel(),
        # while the scope protects against ASGI task-group level cancellation.
        cancelled = False
        with anyio.CancelScope(shield=True):
            task = asyncio.create_task(operation)
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    cancelled = True
                    if task.done():
                        task.result()
                        break
        if cancelled:
            raise asyncio.CancelledError

    async def shutdown(self) -> None:
        self.retired = True
        for scope in tuple(self._active):
            scope.cancel()
        await self._protected(self._close_client())

    def response(self, acquire: Callable[[], Awaitable], diagnostic=None, transform=None,
                 observe_factory: ObserverFactory | None = None):
        return _OwnedResponse(self, acquire, diagnostic, transform, observe_factory)

    async def _discard(self, upstream, diagnostic=None) -> None:
        # Bounded close of an acquired response that no client will read.
        upstream.stream = _OwnedByteStream(self, upstream.stream, diagnostic)
        await self._protected(self._finish(upstream))

    async def acquire_watching(self, receive: Callable[[], Awaitable[dict]],
                               acquire: Callable[[], Awaitable[httpx2.Response]],
                               diagnostic: RequestDiagnostic | None) -> httpx2.Response:
        """Acquire an upstream response for a caller that inspects it before responding.

        The caller has already read the request body. Nothing is sent to the
        client here. A client disconnect cancels the acquisition, records
        `cancelled` and raises ClientDisconnected; retirement raises StreamAborted.
        """
        if self.retired:
            raise StreamAborted('Upstream unavailable')
        upstream = None
        error = None
        disconnected = False
        cancellation = anyio.CancelScope()

        def disconnect() -> None:
            nonlocal disconnected
            disconnected = True
            if diagnostic is not None:
                diagnostic.failure('cancelled')
            cancellation.cancel()

        self._active.add(cancellation)
        try:
            with cancellation:
                async with anyio.create_task_group() as group:
                    group.start_soon(_watch_disconnect, receive, disconnect)
                    try:
                        upstream = await acquire()
                    except Exception as caught:
                        # Re-raised below, not wrapped in the task group's ExceptionGroup.
                        error = caught
                    group.cancel_scope.cancel()
        except BaseException:
            if upstream is not None:
                await self._discard(upstream, diagnostic)
            raise
        finally:
            self._active.discard(cancellation)
        if cancellation.cancel_called or self.retired:
            # The client left or retirement cancelled the acquisition: a response
            # won by the race is closed, never returned.
            if upstream is not None:
                await self._discard(upstream, diagnostic)
            if disconnected:
                raise ClientDisconnected('Client disconnected')
            raise StreamAborted('Upstream unavailable')
        if error is not None:
            raise error
        return upstream


class _OwnedByteStream(httpx2.AsyncByteStream):
    def __init__(self, owner: ProxyStreamOwner, stream, diagnostic=None):
        self.owner = owner
        self.stream = stream
        self.diagnostic = diagnostic

    async def __aiter__(self):
        async for chunk in self.stream:
            yield chunk

    async def aclose(self):
        # HTTPX also closes automatically at iterator exhaustion. Bound that
        # path too, before the downstream terminal body can be sent.
        await self.owner._protected(self.owner._close_stream(self.stream, self.diagnostic))


class _OwnedResponse(Response):
    def __init__(self, owner: ProxyStreamOwner, acquire: Callable[[], Awaitable], diagnostic=None, transform=None,
                 observe_factory: ObserverFactory | None = None):
        super().__init__(content=b'')
        self.owner = owner
        self.acquire = acquire
        self.diagnostic = diagnostic
        # Optional adapter transform: feed(chunk)->bytes, finish()->bytes tail.
        # None keeps the raw passthrough byte-exact.
        self.transform = transform
        # Optional read-only tap, built once per acquired upstream response.
        self.observe_factory = observe_factory

    def _observer(self, upstream) -> Observer | None:
        if self.observe_factory is None:
            return None
        try:
            return self.observe_factory(upstream)
        except Exception:
            # A failing factory only means this response is not observed.
            return None

    @staticmethod
    async def _stream_body(upstream, send, transform=None, observe: Observer | None = None) -> None:
        """Forward the upstream body, through `transform` when one is set.

        `observe` sees each raw upstream chunk before the transform and the
        send, then None once, only after the upstream body was read to its end
        (a later failing tail or close does not take that back). An interrupted
        read (cancellation, disconnect, send or upstream body failure) never
        reports completion, and a bodyless status (1xx, 204, 205, 304) is not
        read, so its observer sees nothing. The first observer exception drops
        the observer for the rest of the response.
        """
        if upstream.status_code not in (204, 205, 304) and upstream.status_code >= 200:
            async for chunk in upstream.aiter_bytes():
                if observe is not None and not _observe(observe, chunk):
                    observe = None
                if transform is not None:
                    out = transform.feed(chunk)
                    if out:
                        await send({'type': 'http.response.body', 'body': out, 'more_body': True})
                else:
                    await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
            if observe is not None:
                _observe(observe, None)
            if transform is not None:
                tail = transform.finish()
                if tail:
                    await send({'type': 'http.response.body', 'body': tail, 'more_body': True})

    async def __call__(self, scope, receive, send) -> None:
        upstream = None
        error = None
        disconnected = False
        completed = False
        cancellation = anyio.CancelScope()
        self.owner._active.add(cancellation)

        def disconnect() -> None:
            nonlocal disconnected
            disconnected = True
            if self.diagnostic is not None and not completed:
                self.diagnostic.failure('cancelled')
            cancellation.cancel()

        try:
            with cancellation:
                # The route has already read the body, so the watcher can run
                # during acquisition: a client leaving while its request is held
                # cancels acquire() before any response byte is sent.
                async with anyio.create_task_group() as group:
                    group.start_soon(_watch_disconnect, receive, disconnect)
                    try:
                        if self.owner.retired:
                            raise StreamAborted('Upstream unavailable')
                        # Acquisition is inside the same try/finally as headers/body.
                        upstream = await self.acquire()
                        upstream.stream = _OwnedByteStream(self.owner, upstream.stream, self.diagnostic)
                        if self.diagnostic is not None and not disconnected:
                            self.diagnostic.upstream(upstream.status_code)
                        if self.owner.retired:
                            raise StreamAborted('Upstream unavailable')
                    except AuthError:
                        # After a disconnect the client left first: a cancellation
                        # that surfaced as an error keeps the outcome `cancelled`.
                        if self.diagnostic is not None and not disconnected:
                            self.diagnostic.failure('auth')
                        error = JSONResponse({'error': {'message': 'Vibe authentication failed; run proxy.py --login'}}, status_code=401)
                    except Exception:
                        if self.diagnostic is not None and not disconnected:
                            self.diagnostic.failure('connection')
                        error = JSONResponse({'error': {'message': 'Upstream connection failed'}}, status_code=502)
                    if error is None and not disconnected:
                        # Only a response that will be streamed gets an observer.
                        observe = self._observer(upstream)
                        # Treat even an interrupted start send as committed: never
                        # attempt replacement headers/JSON after invoking send.
                        await send({'type': 'http.response.start', 'status': upstream.status_code,
                                    'headers': _start_headers(upstream)})
                        if self.diagnostic is not None and self.diagnostic.outcome == 'completed':
                            self.diagnostic.phase = 'stream'
                        telemetry = getattr(scope['app'].state, 'telemetry', None) if 'app' in scope else None
                        if telemetry is not None:
                            with telemetry.tracer.start_as_current_span('proxy.stream'):
                                await self._stream_body(upstream, send, self.transform, observe)
                        else:
                            await self._stream_body(upstream, send, self.transform, observe)
                        # Bodyless/cached responses may bypass iterator auto-close.
                        # Finish ownership before declaring downstream completion.
                        await upstream.aclose()
                        if self.owner.retired:
                            raise StreamAborted('Upstream stream interrupted')
                        completed = True
                        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
                    group.cancel_scope.cancel()
                if error is not None and not disconnected:
                    # Pre-response failures are sent after the watcher stops.
                    await error(scope, receive, send)
                    return
            if cancellation.cancel_called and not disconnected:
                raise StreamAborted('Upstream stream interrupted')
        except Exception:
            raise StreamAborted('Upstream stream interrupted') from None
        finally:
            self.owner._active.discard(cancellation)
            await self.owner._protected(self.owner._finish(upstream))
