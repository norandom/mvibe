"""ASGI response ownership and bounded shared-client retirement."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging

import anyio
import httpx2
from starlette.responses import JSONResponse, Response

from mvibe_auth.api import AuthError

logger = logging.getLogger('mvibe.proxy')


class StreamAborted(RuntimeError):
    """A started response cannot be completed after transport retirement."""


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

    async def _close_stream(self, stream) -> None:
        if not await self._bounded(stream.aclose()):
            self.retired = True
            logger.warning('proxy_cleanup_failed stage=response reason=close_failed')
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

    def response(self, acquire: Callable[[], Awaitable]):
        return _OwnedResponse(self, acquire)


class _OwnedByteStream(httpx2.AsyncByteStream):
    def __init__(self, owner: ProxyStreamOwner, stream):
        self.owner = owner
        self.stream = stream

    async def __aiter__(self):
        async for chunk in self.stream:
            yield chunk

    async def aclose(self):
        # HTTPX also closes automatically at iterator exhaustion. Bound that
        # path too, before the downstream terminal body can be sent.
        await self.owner._protected(self.owner._close_stream(self.stream))


class _OwnedResponse(Response):
    def __init__(self, owner: ProxyStreamOwner, acquire: Callable[[], Awaitable]):
        super().__init__(content=b'')
        self.owner = owner
        self.acquire = acquire

    async def __call__(self, scope, receive, send) -> None:
        upstream = None
        disconnected = False
        cancellation = anyio.CancelScope()
        self.owner._active.add(cancellation)
        try:
            with cancellation:
                try:
                    if self.owner.retired:
                        raise StreamAborted('Upstream unavailable')
                    # Acquisition is inside the same try/finally as headers/body.
                    upstream = await self.acquire()
                    upstream.stream = _OwnedByteStream(self.owner, upstream.stream)
                    if self.owner.retired:
                        raise StreamAborted('Upstream unavailable')
                except AuthError:
                    await JSONResponse({'error': {'message': 'Vibe authentication failed; run proxy.py --login'}}, status_code=401)(scope, receive, send)
                    return
                except Exception:
                    await JSONResponse({'error': {'message': 'Upstream connection failed'}}, status_code=502)(scope, receive, send)
                    return

                async def disconnect() -> None:
                    nonlocal disconnected
                    while True:
                        message = await receive()
                        if message['type'] == 'http.disconnect':
                            disconnected = True
                            cancellation.cancel()
                            return

                async with anyio.create_task_group() as group:
                    group.start_soon(disconnect)
                    # Treat even an interrupted start send as committed: never
                    # attempt replacement headers/JSON after invoking send.
                    await send({'type': 'http.response.start', 'status': upstream.status_code,
                                'headers': [(b'content-type', upstream.headers.get('content-type', 'application/json').encode('latin-1')),
                                            (b'cache-control', b'no-cache')]})
                    if upstream.status_code not in (204, 205, 304) and upstream.status_code >= 200:
                        async for chunk in upstream.aiter_bytes():
                            await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
                    # Bodyless/cached responses may bypass iterator auto-close.
                    # Finish ownership before declaring downstream completion.
                    await upstream.aclose()
                    if self.owner.retired:
                        raise StreamAborted('Upstream stream interrupted')
                    await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
                    group.cancel_scope.cancel()
            if cancellation.cancel_called and not disconnected:
                raise StreamAborted('Upstream stream interrupted')
        except Exception:
            raise StreamAborted('Upstream stream interrupted') from None
        finally:
            self.owner._active.discard(cancellation)
            await self.owner._protected(self.owner._finish(upstream))
