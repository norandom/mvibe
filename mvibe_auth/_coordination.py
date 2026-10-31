"""Native cooperative locking for the shared Vibe keyring slot."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import threading

from filelock import FileLock, SoftFileLock, Timeout
from mvibe_auth._auth_contracts import AuthError, AuthFailure

@dataclass(eq=False)
class _Lease:
    key: str
    owner: object
    active: bool = True


_LEASE: ContextVar[_Lease | None] = ContextVar("oidc_mutation_lease", default=None)
_DELEGATION: ContextVar[tuple[_Lease, int] | None] = ContextVar("oidc_mutation_worker", default=None)


def _owner() -> object:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return task if task is not None else threading.get_ident()


class CoordinationError(AuthError):
    """Transient native-lock failure; retry the operation later."""

    def __init__(self) -> None:
        super().__init__(AuthFailure("transient", retryable=True))


@dataclass
class ResolutionBudget:
    """One shared conflict retry for the complete token/tenant resolution."""

    _remaining: int = 1

    def conflict(self) -> None:
        if self._remaining <= 0:
            raise AuthError(AuthFailure("conflict", retryable=True)) from None
        self._remaining -= 1


def slot_lock_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    slot = sha256(b"ai.mistral.vibe.oidc\0cli-oidc:v1:tokens").hexdigest()
    return root / "mvibe-auth" / "locks" / (slot + ".lock")


class MutationCoordinator:
    def __init__(self, path: Path | None = None, *, timeout: float = 90) -> None:
        self._path = path
        self.timeout = timeout

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else slot_lock_path()

    def _acquire(self):
        try:
            if FileLock is SoftFileLock:
                raise NotImplementedError("native locks unavailable")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock = FileLock(self.path, thread_local=False)
            lock.acquire(timeout=self.timeout)
            return lock
        except (Timeout, OSError, NotImplementedError):
            raise CoordinationError() from None

    def worker(self, function, *args):
        """Explicitly delegate this owner's active lease to one storage worker.

        Capture ownership before create_task schedules the returned coroutine.
        The caller must drain that worker before leaving its coordination scope.
        Inherited context alone never grants a child task this capability.
        """
        lease = _LEASE.get()
        if lease is not None and not (
            lease.active and lease.key == str(self.path.absolute()) and lease.owner == _owner()
        ):
            lease = None

        def invoke():
            token = _DELEGATION.set((lease, threading.get_ident()) if lease is not None and lease.active else None)
            try:
                return function(*args)
            finally:
                _DELEGATION.reset(token)

        return asyncio.to_thread(invoke)

    @contextmanager
    def hold(self):
        key = str(self.path.absolute())
        lease = _LEASE.get()
        delegated = _DELEGATION.get()
        nested = lease is not None and lease.active and lease.key == key and (
            lease.owner == _owner() or delegated == (lease, threading.get_ident())
        )
        if nested:
            yield
            return
        lock = self._acquire()
        owned = _Lease(key, _owner())
        token = _LEASE.set(owned)
        try:
            yield
        finally:
            owned.active = False
            _LEASE.reset(token)
            lock.release()

    @asynccontextmanager
    async def hold_async(self):
        key = str(self.path.absolute())
        owner = asyncio.current_task()
        lease = _LEASE.get()
        if lease is not None and lease.active and lease.key == key and lease.owner == owner:
            yield
            return
        worker = asyncio.create_task(asyncio.to_thread(self._acquire))
        try:
            lock = await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled() and worker.exception() is None:
                worker.result().release()
            raise
        owned = _Lease(key, owner)
        token = _LEASE.set(owned)
        try:
            yield
        finally:
            owned.active = False
            _LEASE.reset(token)
            lock.release()
