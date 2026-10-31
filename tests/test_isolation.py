import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import socket
import subprocess
import sys
import threading
import webbrowser

import httpx2
import keyring
import pytest

import conftest
from fakes import Clock, SyntheticStore, Workers


# This probe runs during module collection, before application module imports.
with pytest.raises(AssertionError, match="blocked external effect"):
    keyring.get_password("synthetic", "synthetic")


def test_real_effect_entrypoints_are_denied():
    probes = [lambda: socket.getaddrinfo("issuer.invalid", 443),
              lambda: webbrowser.get(),
              lambda: keyring.core.get_password("synthetic", "synthetic")]
    with socket.socket() as sock:
        probes.extend([lambda: sock.connect(("192.0.2.1", 443)),
                       lambda: sock.connect_ex(("127.0.0.1", 1)),
                       lambda: sock.sendto(b"synthetic", ("192.0.2.1", 443))])
        for probe in probes:
            with pytest.raises(AssertionError, match="blocked external effect"):
                probe()


def test_socketpair_exemption_does_not_enable_other_connections():
    first, second = socket.socketpair()
    with first, second:
        first.sendall(b"synthetic")
        assert second.recv(9) == b"synthetic"
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="blocked external effect"):
            sock.connect(("127.0.0.1", 1))

def test_guards_replace_safe_probes(monkeypatch):
    # Baseline probes are harmless even in RED: no keyring/browser/socket call.
    calls = []
    for target, name in ((socket.socket, "connect"), (webbrowser, "open"), (keyring, "get_password")):
        monkeypatch.setattr(target, name, lambda *args: calls.append(args))
    conftest.install_guards(monkeypatch)
    for probe in (lambda: socket.socket.connect(None, ("192.0.2.1", 443)),
                  lambda: webbrowser.open("https://issuer.invalid"),
                  lambda: keyring.get_password("synthetic", "synthetic")):
        with pytest.raises(AssertionError, match="blocked external effect"):
            probe()
    assert calls == []


@pytest.mark.anyio
async def test_controlled_boundaries(clock, controlled_http, controlled_callback):
    before = clock.time()
    await clock.sleep(300)
    assert clock.time() == before + 300
    controlled_http.queue("GET", "https://issuer.invalid/discovery", json={"issuer": "synthetic"})
    async with httpx2.AsyncClient(transport=controlled_http.transport) as client:
        assert (await client.get("https://issuer.invalid/discovery")).json() == {"issuer": "synthetic"}
        with pytest.raises(AssertionError, match="unexpected synthetic"):
            await client.get("https://uncontrolled.invalid")
    reader, writer = controlled_callback(b"GET /callback HTTP/1.1\r\n\r\n")
    assert await reader.readline() == b"GET /callback HTTP/1.1\r\n"
    writer.write(b"synthetic")
    await writer.drain()
    writer.close()
    await writer.wait_closed()
    assert writer.data == b"synthetic"


@pytest.mark.parametrize("iteration", [1, 2])
def test_fixtures_start_clean(iteration, synthetic_store, clock, owned_workers):
    from mvibe_auth import _oidc_credentials as credentials
    from mvibe_auth import http
    assert credentials._cached is credentials._UNLOADED
    assert not http._pinned and not http._use_system_trust_store
    assert http.build_ssl_context.cache_info().currsize == 0
    assert "VIBE_CLI_OIDC_WORKSPACE_ID" not in os.environ
    assert synthetic_store.load() is None
    assert clock.time() == Clock().time()
    assert not any(thread.name.startswith("vibe-synthetic-worker") for thread in threading.enumerate())
    credentials._cached = object()
    http.pin_ssl_context(enable_system_trust_store=True)
    os.environ["VIBE_CLI_OIDC_WORKSPACE_ID"] = "synthetic-tenant"
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vibe-synthetic-worker")
    owned_workers.own(lambda: executor.shutdown(wait=True))
    assert executor.submit(lambda: iteration).result() == iteration


def test_workers_are_drained():
    owner = Workers()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(lambda: "synthetic")
    owner.own(lambda: executor.shutdown(wait=True))
    owner.close()
    owner.close()
    assert future.result() == "synthetic"
    assert all(not thread.is_alive() for thread in executor._threads)


def test_synthetic_store_shared_only_with_owned_process(synthetic_store):
    synthetic_store.save({"subject": "synthetic-parent"})
    code = "import conftest; from pathlib import Path; from fakes import SyntheticStore; import sys; s=SyntheticStore(Path(sys.argv[1])); assert s.load()['subject']=='synthetic-parent'; s.save({'subject':'synthetic-child'})"
    result = subprocess.run([sys.executable, "-c", code, str(synthetic_store.path.parent)],
                            cwd=str(__import__("pathlib").Path(__file__).parent),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert synthetic_store.load() == {"subject": "synthetic-child"}
