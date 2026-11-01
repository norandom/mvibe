"""Offline transport tests: never contact Mistral or load user credentials."""
import asyncio
import importlib.util
import json
from pathlib import Path
import os
import sys
import ssl
from types import ModuleType
from unittest.mock import patch
from unittest.mock import AsyncMock

import httpx2
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/trace-native-vibe.py"


def load_trace():
    spec = importlib.util.spec_from_file_location("trace_native_vibe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_final_hook_headers_stream_and_scope(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    original = httpx2.AsyncClient._send_single_request
    trace.install_capture(sink, modules=[httpx2])
    reads = []

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            reads.append(True)
            yield b"response must never be captured"

    async def run():
        async def late_auth(request):
            request.headers["Authorization"] = "Bearer abc.def.ghi"
            request.headers["X-Tenant-Id"] = "tenant-private"

        def handler(request):
            assert request.headers["Authorization"] == "Bearer abc.def.ghi"
            return httpx2.Response(200, headers={
                "mistral-correlation-id": "actual-mistral-id",
                "x-kong-request-id": "actual-kong-id",
                "set-cookie": "secret=response-cookie",
            }, stream=Stream())

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler),
                                    event_hooks={"request": [late_auth]}) as client:
            request = client.build_request("POST", "https://api.mistral.ai/v1/chat/completions",
                                           json={"messages": [{"content": "hello"}], "stream": True})
            response = await client.send(request, stream=True)
            assert reads == []
            assert len(list(sink.directory.glob("*.json"))) == 1
            assert await response.aread() == b"response must never be captured"
            await response.aclose()
            for url in ["https://other.example/v1/chat/completions", "https://api.mistral.ai/v1/models"]:
                response = await client.get(url)
                await response.aclose()

    try:
        asyncio.run(run())
    finally:
        httpx2.AsyncClient._send_single_request = original
    files = list(sink.directory.glob("*.json"))
    assert len(files) == 1
    raw = files[0].read_text()
    record = json.loads(raw)
    assert record["request"]["auth_token_kind"] == "jwt_shaped"
    assert record["request"]["json"]["stream"] is True
    assert record["response"] == {"status": 200, "headers": {
        "mistral-correlation-id": "actual-mistral-id", "x-kong-request-id": "actual-kong-id"}}
    assert "abc.def.ghi" not in raw
    assert "tenant-private" not in raw
    assert "tenant-hmac:" in raw
    assert "response must" not in raw and "response-cookie" not in raw


def test_redaction_is_recursive_and_replaces_known_values_anywhere(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    request = httpx2.Request("POST", "https://api.mistral.ai/v1/chat/completions", headers={
        "authorization": "Bearer actual-token", "cookie": "sid=private-cookie", "x-tenant-id": "tenant-123",
    }, json={"nested": {"access_token": "structured-secret", "tenant_id": "tenant-123"},
             "messages": [{"content": "actual-token structured-secret tenant-123 Bearer unseen-value"}]})
    record = sink.snapshot(request)
    raw = json.dumps(record)
    for secret in ["actual-token", "structured-secret", "tenant-123", "private-cookie", "unseen-value"]:
        assert secret not in raw
    assert "tenant-hmac:" in raw
    assert record["auth_token_kind"] == "opaque"


def test_limits_and_existing_directory_refused(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private", max_requests=2, max_total_bytes=1600)
    request = httpx2.Request("POST", "https://api.mistral.ai/v1/chat/completions",
                            json={"content": "a" * (trace.MAX_REQUEST_BYTES + 1)})
    snapshot = sink.snapshot(request)
    assert snapshot["body_capture"] == "omitted_over_limit"
    for _ in range(4):
        sink.write(snapshot, httpx2.Response(400))
    files = list(sink.directory.glob("*.json"))
    assert len(files) == 2
    assert sum(f.stat().st_size for f in files) <= 1600
    with pytest.raises(FileExistsError):
        trace.CaptureSink(sink.directory)
    with pytest.raises(ValueError):
        trace.CaptureSink(Path("relative-dir"))


@pytest.mark.parametrize("failure", [RuntimeError("private-token"), asyncio.CancelledError()])
def test_exact_exception_no_retry_and_diagnostic_failure_isolated(tmp_path, failure):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    calls = []
    async def failing(self, request):
        calls.append(True)
        raise failure
    async def run():
        async with httpx2.AsyncClient() as client:
            request = client.build_request("POST", "https://api.mistral.ai/v1/chat/completions", json={})
            with pytest.raises(type(failure)) as observed:
                await client.send(request, stream=True)
            assert observed.value is failure
    with patch.object(httpx2.AsyncClient, "_send_single_request", failing):
        trace.install_capture(sink, modules=[httpx2])
        with patch.object(sink, "write", side_effect=OSError("disk full")):
            asyncio.run(run())
    assert calls == [True]


def test_private_directory_before_first_file(tmp_path):
    trace = load_trace()
    events = []
    original = trace.secure_directory
    def secure(path):
        assert list(path.iterdir()) == []
        events.append("secured")
        original(path)
    with patch.object(trace, "secure_directory", secure):
        sink = trace.CaptureSink(tmp_path / "private")
    assert events == ["secured"]
    sink.write({"json": {}}, httpx2.Response(200))
    assert len(list(sink.directory.glob("*.json"))) == 1


def test_response_ids_redact_echoed_request_credentials(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    request = httpx2.Request("POST", "https://api.mistral.ai/v1/chat/completions",
                            headers={"authorization": "Bearer actual-private-token", "x-tenant-id": "raw-private-tenant"},
                            json={})
    sink.write(sink.snapshot(request), httpx2.Response(400, headers={
        "x-kong-request-id": "echo-actual-private-token-raw-private-tenant"}))
    raw = next(sink.directory.glob("*.json")).read_text()
    assert "actual-private-token" not in raw
    assert "raw-private-tenant" not in raw


def test_launcher_selects_installed_tui_and_child_only_oidc(tmp_path):
    trace = load_trace()
    launcher = ModuleType("vibe.cli.launcher")
    calls = []
    launcher.exec_rust_cli = lambda *args, **kwargs: calls.append((args, kwargs))
    before = dict(os.environ)
    directory = tmp_path / "capture"
    with patch.dict(sys.modules, {"vibe.cli.launcher": launcher}):
        trace.main(["--capture-dir", str(directory), "--", "--help"])
    assert dict(os.environ) == before
    args, kwargs = calls[0]
    assert args == (["--help"],)
    assert kwargs["extra_env"] == {"VIBE_CLI_OIDC_ENABLED": "1"}
    assert kwargs["app_server_cmd"] == [sys.executable, str(SCRIPT), "--app-server", "--capture-dir", str(directory)]
    assert not directory.exists()


def test_native_organization_workspace_headers_and_token_counts(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    request = httpx2.Request("POST", "https://api.mistral.ai/v1/chat/completions", headers={
        "x-external-organization-id": "private-native-org",
        "x-external-workspace-id": "private-native-workspace",
    }, json={"max_tokens": 1234, "max_completion_tokens": 5678,
             "nested": {"token": "private-credential", "id_token": "private-id-credential"},
             "messages": [{"content": "private-native-org private-native-workspace private-credential"}]})
    snapshot = sink.snapshot(request)
    raw = json.dumps(snapshot)
    assert "private-native-org" not in raw
    assert "private-native-workspace" not in raw
    assert "private-credential" not in raw
    assert snapshot["json"]["max_tokens"] == 1234
    assert snapshot["json"]["max_completion_tokens"] == 5678
    assert snapshot["headers"]["x-external-organization-id"].startswith("tenant-hmac:")
    assert snapshot["headers"]["x-external-workspace-id"].startswith("tenant-hmac:")


def test_capture_file_is_exclusive_with_private_mode(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    original = os.open
    calls = []
    def capture_open(path, flags, mode=0o777, **kwargs):
        if Path(path).name.startswith("request-"):
            calls.append((flags, mode))
        return original(path, flags, mode, **kwargs)
    with patch.object(trace.os, "open", capture_open):
        sink.write({"json": {}}, httpx2.Response(200))
    assert len(calls) == 1
    flags, mode = calls[0]
    assert flags & os.O_EXCL and flags & os.O_CREAT
    assert mode == 0o600


def test_app_server_restart_uses_new_private_child_directory(tmp_path):
    trace = load_trace()
    server = ModuleType("vibe.app_server.stdio")
    server.main = lambda args: None
    sinks = []
    run_root = tmp_path / "run"
    with patch.dict(sys.modules, {"vibe.app_server.stdio": server}), \
            patch.object(trace, "install_capture", side_effect=sinks.append), \
            patch.object(trace, "install_keyring_diagnostics"):
        for _ in range(2):
            trace.main(["--app-server", "--capture-dir", str(run_root)])
    assert len(sinks) == 2
    assert sinks[0].directory != sinks[1].directory
    assert all(sink.directory.parent == run_root for sink in sinks)


def test_auth_tls_failure_records_types_only_and_preserves_exception(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    tls = ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED private-token private-url")
    failure = httpx2.ConnectError("private-login-payload")
    failure.__cause__ = tls
    original = httpx2.AsyncClient._send_single_request
    calls = []
    def fail(request):
        calls.append(True)
        raise failure
    async def run():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(fail)) as client:
            with pytest.raises(httpx2.ConnectError) as observed:
                await client.post("https://auth.mistral.ai/private-registration?token=private-query",
                                  json={"password": "private-login-payload"})
            assert observed.value is failure
    try:
        trace.install_capture(sink, modules=[httpx2])
        asyncio.run(run())
    finally:
        httpx2.AsyncClient._send_single_request = original
    assert calls == [True]
    raw = next(sink.directory.glob("*.json")).read_text()
    record = json.loads(raw)
    assert record["auth_transport_failure"]["exception_types"] == ["ConnectError", "SSLCertVerificationError"]
    assert record["auth_transport_failure"]["certificate_verify_failed"] is True
    assert "private-" not in raw
    assert "request" not in record


def test_auth_failure_context_fallback_and_cycle_bound(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    failure = RuntimeError("private-top")
    tls = ssl.SSLCertVerificationError("private-inner")
    failure.__context__ = tls
    tls.__context__ = failure
    sink.write_auth_failure(failure)
    record = json.loads(next(sink.directory.glob("*.json")).read_text())
    assert record["auth_transport_failure"] == {
        "exception_types": ["RuntimeError", "SSLCertVerificationError"],
        "certificate_verify_failed": True}


def test_auth_success_and_other_host_failures_are_not_captured(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    original = httpx2.AsyncClient._send_single_request
    def handler(request):
        if request.url.host == "auth.mistral.ai":
            return httpx2.Response(200, json={"access_token": "private-response"})
        raise httpx2.ConnectError("private-unrelated")
    async def run():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            response = await client.get("https://auth.mistral.ai/private")
            assert response.status_code == 200
            with pytest.raises(httpx2.ConnectError):
                await client.get("https://unrelated.example/private")
    try:
        trace.install_capture(sink, modules=[httpx2])
        asyncio.run(run())
    finally:
        httpx2.AsyncClient._send_single_request = original
    assert list(sink.directory.glob("*.json")) == []


def test_keyring_failure_records_type_without_secret_arguments(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    module = ModuleType("keyring")
    class SyntheticWinVaultError(RuntimeError):
        pass
    failure = SyntheticWinVaultError("private-token private-service private-account")
    calls = []
    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise failure
    module.set_password = fail
    with patch.dict(sys.modules, {"keyring": module}):
        trace.install_keyring_diagnostics(sink)
        with pytest.raises(SyntheticWinVaultError) as observed:
            module.set_password("private-service", "private-account", "private-token")
    assert observed.value is failure
    assert calls == [(("private-service", "private-account", "private-token"), {})]
    raw = next(sink.directory.glob("*.json")).read_text()
    record = json.loads(raw)
    assert record["auth_storage_failure"] == {"exception_types": ["SyntheticWinVaultError"]}
    assert "private-" not in raw


def test_keyring_success_and_diagnostic_failure_preserve_behavior(tmp_path):
    trace = load_trace()
    sink = trace.CaptureSink(tmp_path / "private")
    module = ModuleType("keyring")
    result = object()
    module.set_password = lambda *args, **kwargs: result
    with patch.dict(sys.modules, {"keyring": module}):
        trace.install_keyring_diagnostics(sink)
        assert module.set_password("private-service", "private-account", "private-token") is result
    assert list(sink.directory.glob("*.json")) == []
    failure = RuntimeError("private-token")
    def fail(*args, **kwargs):
        raise failure
    module.set_password = fail
    with patch.dict(sys.modules, {"keyring": module}), \
            patch.object(sink, "write_storage_failure", side_effect=OSError("disk full")):
        trace.install_keyring_diagnostics(sink)
        with pytest.raises(RuntimeError) as observed:
            module.set_password("private-service", "private-account", "private-token")
    assert observed.value is failure


def load_native_credentials():
    from conftest import _original_env
    installed = Path(_original_env["APPDATA"]) / "uv/tools/mistral-vibe/Lib/site-packages/vibe/setup/auth/_oidc_credentials.py"
    if not installed.exists():
        pytest.skip("Installed native Vibe source is needed for adapter contract tests")
    spec = importlib.util.spec_from_file_location("_test_native_oidc_credentials", installed)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_portable_native_store_large_lifecycle_isolated_slots_and_cas(tmp_path, monkeypatch):
    trace = load_trace()
    native = load_native_credentials()
    values = {}
    touched = []
    def get(service, entry):
        touched.append((service, entry))
        return values.get((service, entry))
    def put(service, entry, value):
        assert len(value) < 1000  # Simulate the Windows credential-size limit.
        touched.append((service, entry))
        values[(service, entry)] = value
    import keyring
    monkeypatch.setattr(keyring, "get_password", get)
    monkeypatch.setattr(keyring, "set_password", put)
    monkeypatch.chdir(tmp_path)
    portable = trace.install_portable_store(native=native, credential_dir=tmp_path / "portable")
    tokens = native.OidcTokens(issuer="https://synthetic.invalid", client_id="client", audience="audience",
                              subject="subject", access_token="access" * 3000, refresh_token="refresh",
                              id_token="idtoken" * 3000, expires_at=1234567890)
    newer = tokens.model_copy(update={"expires_at": 1234567900})
    tenant = native.OidcTenant(selection="", organization_id="org", workspace_id="workspace")
    async def run():
        store = native.OidcCredentialStore()
        assert await store.load() is None
        assert await store.save(tokens, complete_on_cancel=True) is None
        assert (await store.reload()).model_dump_json() == tokens.model_dump_json()
        assert store._load().model_dump_json() == tokens.model_dump_json()
        assert await store.save_if_current(newer, expected=tokens) is True
        assert await store.save_if_current(tokens, expected=tokens) is False
        # Native tenant writes accept the same identity after a token refresh.
        assert store.save_tenant(tokens, tenant) is None
        current = await store.reload()
        assert current.tenant == tenant and current.expires_at == newer.expires_at
        # Native refresh ignores tenant-only concurrent updates and retains them.
        assert await store.save_if_current(newer, expected=newer) is True
        assert (await store.reload()).tenant == tenant
        # Plain native saves replace the caller's tenant selection, including None.
        assert await store.save(newer) is None
        assert (await store.reload()).tenant is None
        assert await store.clear() is None
        assert await native.OidcCredentialStore().reload() is None
    asyncio.run(run())
    assert portable._KEYRING_SERVICE == "mvibe.native-comparison.oidc"
    assert portable._KEYRING_ENTRY == "native:v1:legacy-disabled"
    assert portable._ENCRYPTION_KEY_ENTRY == "native:v1:fernet-key"
    assert all(service == "mvibe.native-comparison.oidc" for service, _ in touched)
    assert all(entry != "cli-oidc:v1:tokens" for _, entry in touched)
    assert portable.credential_path() == tmp_path / "portable/credentials.v1.enc"
    encrypted = portable.credential_path().read_bytes()
    assert b"signed_out" not in encrypted and b"refresh" not in encrypted


def test_portable_adapter_cancel_forwarding_and_safe_errors(tmp_path):
    trace = load_trace()
    native = load_native_credentials()
    portable = trace.install_portable_store(native=native, credential_dir=tmp_path / "portable")
    backend = type("Backend", (), {})()
    backend.save = AsyncMock(return_value=False)
    backend.coordinator = portable.MutationCoordinator(tmp_path / "portable/credentials.lock")
    tokens = native.OidcTokens(issuer="issuer", client_id="client", audience="aud", subject="sub",
                              access_token="access", refresh_token=None, id_token="id", expires_at=1)
    with patch.object(portable, "OidcCredentialStore", return_value=backend):
        store = native.OidcCredentialStore()
    async def run():
        with pytest.raises(native.OidcStorageError):
            await store.save(tokens, complete_on_cancel=True)
        assert backend.save.call_args.kwargs == {"complete_on_cancel": True}
        cancellation = asyncio.CancelledError()
        backend.save.side_effect = cancellation
        with pytest.raises(asyncio.CancelledError):
            await store.save(tokens)
        assert backend.save.call_args.kwargs == {"complete_on_cancel": False}
        backend.save.side_effect = RuntimeError("private-secret-error")
        with pytest.raises(native.OidcStorageError) as observed:
            await store.save(tokens)
        assert "private" not in str(observed.value)
    asyncio.run(run())
