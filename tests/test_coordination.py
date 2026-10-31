"""Cooperative native-lock contracts, with test-owned paths and storage."""
import asyncio
import threading
import subprocess
import sys
import time
from pathlib import Path

from filelock import FileLock
import pytest

from mvibe_auth._coordination import CoordinationError, MutationCoordinator
from test_credentials import tokens, slot


def test_two_process_tenant_bundle_cannot_overwrite_rotation(tmp_path, owned_workers):
    original = tokens()
    (tmp_path / "slot.json").write_text(original.model_dump_json())
    (tmp_path / "original.json").write_text(original.model_dump_json())

    def spawn(mode):
        child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("coordination_worker.py")), str(tmp_path), mode], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        owned_workers.own(cleanup)
        return child

    def wait(name):
        deadline = time.monotonic() + 5
        while not (tmp_path / name).exists():
            assert time.monotonic() < deadline, name
            time.sleep(.01)

    tenant = spawn("tenant")
    wait("tenant-read")
    refresh = spawn("refresh")
    wait("refresh-started")
    # Tenant is paused between authoritative read and whole-bundle write.
    # An uncoordinated refresh commits in this gap and is then overwritten.
    time.sleep(.3)
    (tmp_path / "release-tenant").touch()
    for child in (tenant, refresh):
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, (out, err)
    from mvibe_auth._oidc_credentials import OidcTokens
    current = OidcTokens.model_validate_json((tmp_path / "slot.json").read_text())
    assert current.refresh_token.get_secret_value() == "rotated-refresh"
    assert current.tenant.workspace_id == "workspace"


@pytest.mark.anyio
async def test_native_coordinator_serializes_independent_tasks(tmp_path):
    coordinator = MutationCoordinator(tmp_path / "slot.lock", timeout=.15)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first():
        async with coordinator.hold_async():
            entered.set()
            await release.wait()

    task = asyncio.create_task(first())
    await entered.wait()
    heartbeat = []
    asyncio.get_running_loop().call_later(.03, heartbeat.append, True)
    try:
        with pytest.raises(CoordinationError, match="retry") as failure:
            async with coordinator.hold_async():
                pytest.fail("competing task entered owned native lock")
        assert failure.value.failure.code == "transient"
        assert failure.value.failure.retryable
        assert heartbeat == [True]  # Native acquisition never blocked this loop.
    finally:
        release.set()
        await task


@pytest.mark.anyio
async def test_acquisition_cancel_drains_worker_and_releases_late_lock(tmp_path, monkeypatch):
    coordinator = MutationCoordinator(tmp_path / "slot.lock")
    started, finish = threading.Event(), threading.Event()
    acquire = coordinator._acquire

    def delayed():
        started.set()
        assert finish.wait(5)
        return acquire()

    monkeypatch.setattr(coordinator, "_acquire", delayed)

    async def run():
        async with coordinator.hold_async():
            pytest.fail("cancelled acquisition entered body")

    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(.02)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    with FileLock(coordinator.path, timeout=.1):
        pass


@pytest.mark.anyio
async def test_nested_async_and_worker_sync_boundary_does_not_deadlock(tmp_path):
    coordinator = MutationCoordinator(tmp_path / "slot.lock", timeout=.1)

    def nested():
        with coordinator.hold():
            return True

    async with coordinator.hold_async():
        async with coordinator.hold_async():
            assert await coordinator.worker(nested)


@pytest.mark.anyio
@pytest.mark.parametrize("explicit", [False, True])
async def test_child_task_worker_cannot_inherit_parent_lock_ownership(tmp_path, slot, explicit):
    from mvibe_auth._oidc_credentials import OidcCredentialStore
    from test_credentials import tenant
    coordinator = MutationCoordinator(tmp_path / "slot.lock", timeout=.1)
    store = OidcCredentialStore(coordinator=coordinator)
    original = tokens()
    slot.external(original)

    async def child():
        if explicit:
            await coordinator.worker(store.save_tenant, original, tenant())
        else:
            await asyncio.to_thread(store.save_tenant, original, tenant())

    async with coordinator.hold_async():
        with pytest.raises(CoordinationError):
            await asyncio.create_task(child())
    assert slot.writes == 0


@pytest.mark.anyio
async def test_inherited_context_cannot_bypass_later_lock_owner(tmp_path):
    coordinator = MutationCoordinator(tmp_path / "slot.lock", timeout=.1)
    release_child = asyncio.Event()

    def mutate():
        with coordinator.hold():
            pytest.fail("stale inherited lease bypassed later native owner")

    async def child():
        await release_child.wait()
        await asyncio.to_thread(mutate)

    async with coordinator.hold_async():
        task = asyncio.create_task(child())
    with FileLock(coordinator.path, timeout=.1):
        release_child.set()
        with pytest.raises(CoordinationError):
            await task


@pytest.mark.anyio
async def test_explicit_worker_delegation_expires_with_owning_scope(tmp_path):
    coordinator = MutationCoordinator(tmp_path / "slot.lock", timeout=.1)

    def mutate():
        with coordinator.hold():
            pytest.fail("expired explicit delegation bypassed native owner")

    async with coordinator.hold_async():
        pending = coordinator.worker(mutate)
    with FileLock(coordinator.path, timeout=.1):
        with pytest.raises(CoordinationError):
            await pending


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["save", "clear"])
async def test_cancelled_store_worker_retains_native_lock(slot, tmp_path, monkeypatch, operation):
    import keyring
    from filelock import Timeout
    from mvibe_auth._oidc_credentials import OidcCredentialStore
    coordinator = MutationCoordinator(tmp_path / "slot.lock", timeout=.1)
    store = OidcCredentialStore(coordinator=coordinator)
    original = tokens()
    slot.external(original)
    started, release = threading.Event(), threading.Event()
    method = "set_password" if operation == "save" else "delete_password"
    underlying = getattr(keyring, method)

    def blocked(*args):
        started.set()
        assert release.wait(5)
        underlying(*args)

    monkeypatch.setattr(keyring, method, blocked)
    task = asyncio.create_task(store.save(original, expected=original) if operation == "save" else store.clear(expected=original))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(Timeout):
            await asyncio.to_thread(FileLock(coordinator.path).acquire, timeout=.1)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    with FileLock(coordinator.path, timeout=.1):
        pass


@pytest.mark.anyio
async def test_services_on_separate_event_loops_share_rotation(slot, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from mvibe_auth._auth_config import AuthConfig
    from mvibe_auth._oidc_service import OidcService
    config = AuthConfig(issuer="https://synthetic.invalid", client_id="synthetic-client", audience="https://inference.invalid", enabled=True)
    original = tokens()
    rotated = original.model_copy(update={"expires_at": time.time() + 3600})
    slot.external(original)
    calls = []

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def refresh_token(self, *args, **kwargs):
            calls.append(kwargs["refresh_token"])
            await asyncio.sleep(.05)
            return {}

    def service():
        instance = OidcService.from_config(config)
        instance.store.coordinator = MutationCoordinator(tmp_path / "slot.lock")
        instance.client.session = lambda: Session()
        instance.client.discover = AsyncMock(return_value=SimpleNamespace(token_endpoint="https://synthetic.invalid/token"))
        instance.client.validated_tokens = AsyncMock(return_value=rotated)
        return instance

    first, second = service(), service()
    results = await asyncio.gather(
        asyncio.to_thread(lambda: asyncio.run(first.access_tokens())),
        asyncio.to_thread(lambda: asyncio.run(second.access_tokens())),
        asyncio.to_thread(lambda: asyncio.run(first.access_tokens())),
    )
    assert results == [rotated, rotated, rotated]
    assert calls == ["synthetic-refresh"]


def test_native_failure_never_uses_soft_lock(tmp_path, monkeypatch):
    from filelock import SoftFileLock
    from mvibe_auth import _coordination
    monkeypatch.setattr(_coordination, "FileLock", SoftFileLock)
    with pytest.raises(CoordinationError, match="retry"):
        with MutationCoordinator(tmp_path / "slot.lock").hold():
            pytest.fail("soft fallback used")
    assert not (tmp_path / "slot.lock").exists()


@pytest.mark.anyio
async def test_cancelled_snapshot_read_drains_before_releasing_coordinator(slot, tmp_path, monkeypatch):
    import keyring
    from filelock import Timeout
    from mvibe_auth._oidc_credentials import OidcCredentialStore
    coordinator = MutationCoordinator(tmp_path / "slot.lock")
    store = OidcCredentialStore(coordinator=coordinator)
    started, release = threading.Event(), threading.Event()
    original_read = slot.read

    def read(*args):
        started.set()
        assert release.wait(5)
        return original_read(*args)

    monkeypatch.setattr(keyring, "get_password", read)
    task = asyncio.create_task(store.save(tokens()))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(.01)
        with pytest.raises(Timeout):
            await asyncio.to_thread(FileLock(coordinator.path).acquire, timeout=.1)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert slot.writes == 0
    with FileLock(coordinator.path, timeout=.1):
        pass


def test_default_coordinator_is_per_user_slot_and_bounded(monkeypatch, tmp_path):
    from mvibe_auth._coordination import slot_lock_path
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-a"))
    first = slot_lock_path()
    assert first == MutationCoordinator().path
    assert MutationCoordinator().timeout == 90
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-b"))
    assert slot_lock_path() != first


@pytest.mark.anyio
async def test_browser_wait_does_not_hold_mutation_coordinator(tmp_path, monkeypatch, slot):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from mvibe_auth._auth_config import AuthConfig
    from mvibe_auth import _oidc_service
    ready = asyncio.Event()

    class Callback:
        def __init__(self, uri, state):
            self.result = asyncio.get_running_loop().create_future()
        async def __aenter__(self):
            return self
        async def __aexit__(self):
            self.result.cancel()

    class Session:
        def create_authorization_url(self, *args, **kwargs):
            ready.set()
            return "https://synthetic.invalid/authorize", "synthetic-state"
        async def aclose(self):
            pass

    monkeypatch.setattr(_oidc_service, "OidcCallback", Callback)
    service = _oidc_service.OidcService.from_config(AuthConfig(enabled=True))
    service.store.coordinator = MutationCoordinator(tmp_path / "slot.lock")
    service.client.session = lambda: Session()
    service.client.discover = AsyncMock(return_value=SimpleNamespace(authorization_endpoint="https://synthetic.invalid/authorize"))
    attempt = await service.start()
    try:
        await asyncio.wait_for(ready.wait(), 2)
        assert not attempt.task.done()
        # Independent native contender proves no browser-duration ownership.
        with FileLock(service.store.coordinator.path, timeout=.1):
            pass
    finally:
        await service.cancel()
