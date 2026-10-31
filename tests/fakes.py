"""Test-only synthetic boundaries. Never imported by production code."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from filelock import FileLock
import httpx2


@dataclass
class Clock:
    now: float = 1_700_000_000.0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("time cannot move backwards")
        self.now += seconds


class SyntheticStore:
    """A JSON slot and native file lock shared only by test-owned processes."""

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "synthetic-credentials.json"
        self.lock_path = root / "synthetic-credentials.lock"

    def load(self) -> dict[str, Any] | None:
        with FileLock(self.lock_path, timeout=5):
            return json.loads(self.path.read_text()) if self.path.exists() else None

    def save(self, value: dict[str, Any]) -> None:
        with FileLock(self.lock_path, timeout=5):
            self.path.write_text(json.dumps(value), encoding="utf-8")


class ControlledHTTP:
    """Only explicitly queued method/URL pairs may receive fake responses."""

    def __init__(self):
        self.responses: list[tuple[str, str, httpx2.Response]] = []
        self.requests: list[httpx2.Request] = []
        self.transport = httpx2.MockTransport(self.handle)

    def queue(self, method: str, url: str, *, status: int = 200, **kwargs: Any) -> None:
        self.responses.append((method, url, httpx2.Response(status, **kwargs)))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if not self.responses:
            raise AssertionError("unexpected synthetic HTTP request")
        method, url, response = self.responses.pop(0)
        assert (request.method, str(request.url)) == (method, url)
        self.requests.append(request)
        return response


class CallbackWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        assert self.closed


def callback_io(data: bytes) -> tuple[asyncio.StreamReader, CallbackWriter]:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader, CallbackWriter()


class Workers:
    """Own cleanup in LIFO order; shutdown is idempotent and drains workers."""

    def __init__(self):
        self.cleanups: list[Any] = []

    def own(self, cleanup: Any) -> None:
        self.cleanups.append(cleanup)

    def close(self) -> None:
        errors = []
        while self.cleanups:
            try:
                self.cleanups.pop()()
            except BaseException as error:
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("test worker cleanup failed", errors)
