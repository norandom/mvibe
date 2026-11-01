"""Offline transport tests: never contact Mistral or load user credentials."""
import asyncio
import importlib.util
import json
from pathlib import Path
import os
import sys
from types import ModuleType
from unittest.mock import patch

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
