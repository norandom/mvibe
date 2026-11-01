"""Install external-effect guards before pytest imports any application tests."""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import webbrowser

import keyring
import pytest

from fakes import Clock, ControlledHTTP, SyntheticStore, Workers, callback_io

def deny_external(*args, **kwargs):
    raise AssertionError("offline test blocked external effect")


def install_guards(patch):
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            patch.setattr(socket.socket, name, deny_external)
    patch.setattr(socket, "getaddrinfo", deny_external)
    for name in ("open", "open_new", "open_new_tab", "get"):
        patch.setattr(webbrowser, name, deny_external)
    for name in ("get_keyring", "get_password", "get_credential", "set_password", "delete_password"):
        patch.setattr(keyring, name, deny_external)
        if hasattr(keyring.core, name):
            patch.setattr(keyring.core, name, deny_external)
    if hasattr(os, "startfile"):
        patch.setattr(os, "startfile", deny_external)


# Windows implements socketpair with a local connection. Permit only stdlib's
# socketpair construction, never arbitrary application callbacks or destinations.
_socketpair = socket.socketpair
_connect = socket.socket.connect


def local_socketpair(*args, **kwargs):
    if sys.platform != "win32":
        return _socketpair(*args, **kwargs)
    # Only this private socket receives an exemption, to an ephemeral listener
    # bound here. Other threads never gain a temporary permission to connect.
    family = kwargs.get("family", args[0] if args else socket.AF_INET)
    kind = kwargs.get("type", args[1] if len(args) > 1 else socket.SOCK_STREAM)
    proto = kwargs.get("proto", args[2] if len(args) > 2 else 0)
    if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM or proto:
        raise ValueError("only local TCP socketpairs are supported")
    host = "127.0.0.1" if family == socket.AF_INET else "::1"
    with socket.socket(family, kind, proto) as listener:
        listener.bind((host, 0))
        listener.listen()
        client = socket.socket(family, kind, proto)
        try:
            client.setblocking(False)
            try:
                _connect(client, listener.getsockname())
            except (BlockingIOError, InterruptedError):
                pass
            client.setblocking(True)
            server, _ = listener.accept()
            if server.getpeername() != client.getsockname() or client.getpeername() != server.getsockname():
                server.close()
                raise ConnectionError("unexpected socketpair peer")
            return server, client
        except BaseException:
            client.close()
            raise


_guards = pytest.MonkeyPatch()
install_guards(_guards)
_guards.setattr(socket, "socketpair", local_socketpair)

_original_env = os.environ.copy()
_session_root = tempfile.TemporaryDirectory(prefix="vibe-offline-session-")


def isolate_environment(root):
    from pathlib import Path
    for key in list(os.environ):
        if key.startswith(("VIBE_", "MISTRAL_", "OPENAI_", "OPENCODE_")) or key.upper() in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        }:
            del os.environ[key]
    for key in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        path = Path(root) / key.lower()
        path.mkdir()
        os.environ[key] = str(path)


isolate_environment(_session_root.name)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # xdist's controller collects no tests. Let workers inherit the original
    # runtime/cache paths; each worker installs these import-time guards and
    # creates its own isolated homes before collecting application tests.
    if getattr(config.option, "numprocesses", 0) and not hasattr(config, "workerinput"):
        _guards.undo()
        os.environ.clear()
        os.environ.update(_original_env)


def pytest_unconfigure(config):
    _guards.undo()
    os.environ.clear()
    os.environ.update(_original_env)
    _session_root.cleanup()


def reset_caches():
    credentials = sys.modules.get("mvibe_auth._oidc_credentials")
    if credentials:
        credentials.clear_oidc_token_cache()
    tls = sys.modules.get("mvibe_auth.http")
    if tls:
        tls.build_ssl_context.cache_clear()
        tls._pinned = False
        tls._use_system_trust_store = False


@pytest.fixture(autouse=True)
def isolated_state(tmp_path):
    # Clear all overrides, including common credentials and proxy/trust settings.
    original_env = os.environ.copy()
    isolate_environment(tmp_path)
    reset_caches()
    workers = Workers()
    try:
        yield workers
    finally:
        try:
            workers.close()
        finally:
            reset_caches()
            os.environ.clear()
            os.environ.update(original_env)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def synthetic_store(tmp_path):
    return SyntheticStore(tmp_path / "store")


@pytest.fixture
def controlled_http():
    return ControlledHTTP()


@pytest.fixture
def controlled_callback():
    return callback_io


@pytest.fixture
def owned_workers(isolated_state):
    return isolated_state
