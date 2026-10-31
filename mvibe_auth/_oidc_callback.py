from __future__ import annotations

import asyncio
import hmac
from urllib.parse import parse_qs, urlsplit

from mvibe_auth._oidc_config import OidcError

_MAX_CONNECTIONS = 8


class OidcCallback:
    """Bounded loopback callback; unrelated requests cannot complete login."""

    def __init__(self, redirect_uri: str, state: str) -> None:
        self.redirect_uri = redirect_uri
        self.state = state
        self.result: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task[None]] = set()
        self._closing = False

    async def __aenter__(self) -> OidcCallback:
        parsed = urlsplit(self.redirect_uri)
        self._server = await asyncio.start_server(
            self._handle, parsed.hostname, parsed.port, limit=8192
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        self._closing = True
        if self._server is not None:
            self._server.close()
        connections = tuple(self._connections)
        for task in connections:
            task.cancel()
        await asyncio.gather(*connections, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        if not self.result.done():
            self.result.cancel()

    async def _close_writer(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 5)
        except (TimeoutError, OSError):
            pass

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is None:
            await self._close_writer(writer)
            return
        if self._closing:
            await self._close_writer(writer)
            return
        self._connections.add(task)
        status, text = "400 Bad Request", "Invalid sign-in callback."
        try:
            if len(self._connections) > _MAX_CONNECTIONS:
                return
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            if len(request) > 8192:
                return
            line = request.split(b"\r\n", 1)[0].decode("ascii")
            method, target, version = line.split(" ")
            parsed = urlsplit(target)
            query = parse_qs(parsed.query, keep_blank_values=True)
            valid_state = query.get("state", [])
            code = query.get("code", [])
            error = query.get("error", [])
            valid_target = (
                method == "GET"
                and version in ("HTTP/1.0", "HTTP/1.1")
                and not parsed.scheme
                and not parsed.netloc
                and not parsed.fragment
                and parsed.path == urlsplit(self.redirect_uri).path
            )
            if (
                valid_target
                and len(valid_state) == 1
                and bool(valid_state[0])
                and hmac.compare_digest(valid_state[0].encode(), self.state.encode())
                and not self.result.done()
            ):
                if len(error) == 1 and error[0] and "code" not in query:
                    self.result.set_exception(
                        OidcError("OIDC authorization was declined")
                    )
                    status, text = "200 OK", "Sign-in declined. Return to Vibe."
                elif len(code) == 1 and code[0] and "error" not in query:
                    self.result.set_result(f"{self.redirect_uri}?{parsed.query}")
                    status, text = "200 OK", "Sign-in received. Return to Vibe."
            body = text.encode()
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                "Connection: close\r\n\r\n".encode()
                + body
            )
            await asyncio.wait_for(writer.drain(), 5)
        except (
            TimeoutError,
            ValueError,
            OSError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            try:
                await self._close_writer(writer)
            finally:
                self._connections.discard(task)
