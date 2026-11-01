"""Opt-in, local-only final HTTP capture for the installed official Vibe TUI.

Uses the installed Vibe runtime; never imports this project's proxy or auth.
Captured prompts/tool arguments can be sensitive even after credential redaction.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import functools
import hashlib
import hmac
import importlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
import threading

MAX_REQUEST_BYTES = 256 * 1024
MAX_REQUESTS = 64
MAX_TOTAL_BYTES = 8 * 1024 * 1024
REDACTED = "[REDACTED]"
_CREDENTIAL = re.compile(
    r"authorization|cookie|secret|password|passwd|credential|api[-_]?key|bearer|"
    r"(?:^|[-_])(?:access[-_]?|refresh[-_]?|id[-_]?|auth[-_]?)?token$", re.I)
_TENANT = re.compile(r"tenant|(?:organization|workspace)[-_]?id", re.I)
_BEARER = re.compile(r"\bBearer\s+[^\s\"'<>;,]+", re.I)
_JWT = re.compile(r"(?<![\w-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![\w-])")


def secure_directory(directory: Path) -> None:
    """Set a private DACL before writing any capture; fail closed on ACL errors."""
    if os.name == "nt":
        output = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
        sid = next(csv.reader(io.StringIO(output.strip())))[1]
        if not re.fullmatch(r"S-1-[0-9-]+", sid):
            raise RuntimeError("Could not determine current Windows SID")
        subprocess.run(["icacls", str(directory), "/inheritance:r", "/grant:r",
                        f"*{sid}:(OI)(CI)F"], check=True, capture_output=True)
    else:
        directory.chmod(0o700)


class Redactor:
    def __init__(self, key: bytes):
        self.key = key
        self.replacements = {}
        for name, value in os.environ.items():
            if _CREDENTIAL.search(name) and len(value) >= 4:
                self.replacements[value] = REDACTED

    def collect(self, value, field=""):
        if _TENANT.search(field):
            self._collect_leaves(value, tenant=True)
        elif _CREDENTIAL.search(field):
            self._collect_leaves(value, tenant=False)
        elif isinstance(value, dict):
            for name, child in value.items():
                self.collect(child, str(name))
        elif isinstance(value, list):
            for child in value:
                self.collect(child)

    def _collect_leaves(self, value, *, tenant):
        if isinstance(value, dict):
            for child in value.values():
                self._collect_leaves(child, tenant=tenant)
        elif isinstance(value, list):
            for child in value:
                self._collect_leaves(child, tenant=tenant)
        elif value is not None:
            raw = str(value)
            if not raw:
                return
            replacement = ("tenant-hmac:" + hmac.new(self.key, raw.encode(), hashlib.sha256).hexdigest()[:20]
                           if tenant else REDACTED)
            self.replacements[raw] = replacement
            if not tenant:
                # Also remove a bearer/cookie value when repeated in prompt text.
                if raw.lower().startswith("bearer "):
                    self.replacements[raw[7:]] = REDACTED
                if "=" in raw:
                    for cookie in raw.split(";"):
                        _, _, token = cookie.partition("=")
                        if token.strip():
                            self.replacements[token.strip()] = REDACTED

    def text(self, raw):
        for secret, replacement in sorted(self.replacements.items(), key=lambda item: -len(item[0])):
            raw = raw.replace(secret, replacement)
        return _JWT.sub(REDACTED, _BEARER.sub("Bearer " + REDACTED, raw))

    def apply(self, value, field=""):
        if _CREDENTIAL.search(field):
            return REDACTED
        if _TENANT.search(field) and not isinstance(value, (dict, list)):
            return self.replacements.get(str(value), REDACTED)
        if isinstance(value, dict):
            return {self.text(str(name)): self.apply(child, str(name)) for name, child in value.items()}
        if isinstance(value, list):
            return [self.apply(child) for child in value]
        return self.text(value) if isinstance(value, str) else value


class _Snapshot(dict):
    """JSON-serializable record with an in-memory-only redaction context."""


class CaptureSink:
    def __init__(self, directory: Path, *, max_requests=MAX_REQUESTS, max_total_bytes=MAX_TOTAL_BYTES):
        if not directory.is_absolute():
            raise ValueError("Capture directory must be absolute")
        # Refuse reuse, including symlinks; existing data is never overwritten.
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        secure_directory(directory)
        self.directory = directory
        self.max_requests = min(max_requests, MAX_REQUESTS)
        self.max_total_bytes = min(max_total_bytes, MAX_TOTAL_BYTES)
        self.requests = 0
        self.total_bytes = 0
        self.key = secrets.token_bytes(32)  # In memory only; pseudonyms are per process.
        self.lock = threading.Lock()

    def snapshot(self, request):
        redactor = Redactor(self.key)
        headers = dict(request.headers)
        body = None
        state = "captured_json"
        try:
            raw = request.content  # Never consume an async request stream.
            if len(raw) > MAX_REQUEST_BYTES:
                state = "omitted_over_limit"
            else:
                body = json.loads(raw)
        except Exception:
            state = "omitted_unbuffered_or_non_json"
        redactor.collect(headers)
        redactor.collect(body)
        auth = headers.get("authorization", "")
        token = auth.partition(" ")[2]
        kind = "absent" if not auth else (
            "jwt_shaped" if re.fullmatch(r"[\w-]+\.[\w-]+\.[\w-]+", token, flags=re.ASCII) else "opaque")
        snapshot = _Snapshot({
            "method": request.method,
            "url": "https://api.mistral.ai/v1/chat/completions",  # No query/userinfo capture.
            "headers": redactor.apply(headers),
            "auth_token_kind": kind,
            "body_capture": state,
            "json": redactor.apply(body),
        })
        snapshot.redactor = redactor
        return snapshot

    def write(self, snapshot, response=None, error=None):
        response_info = None
        if response is not None:
            # Do not access response.content, read, aread, or iterate the stream.
            headers = {name: response.headers[name] for name in (
                "mistral-correlation-id", "x-kong-request-id") if name in response.headers}
            redactor = getattr(snapshot, "redactor", None) or Redactor(self.key)
            # Retain the request's private context for echoed credentials/tenant IDs.
            safe_headers = {name: value[:256] for name, value in redactor.apply(headers).items()}
            response_info = {"status": response.status_code, "headers": safe_headers}
        record = {"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                  "request": snapshot, "response": response_info}
        if error is not None:
            record["transport_error"] = type(error).__name__  # Never exception text.
        self._write_record(record)

    def write_auth_failure(self, error):
        types = []
        seen = set()
        certificate_failure = False
        current = error
        while current is not None and id(current) not in seen and len(types) < 8:
            seen.add(id(current))
            types.append(type(current).__name__)
            certificate_failure |= isinstance(current, ssl.SSLCertVerificationError)
            # Inspect for a fixed marker only; no exception message is persisted.
            certificate_failure |= "CERTIFICATE_VERIFY_FAILED" in str(current)
            current = current.__cause__ or current.__context__
        self._write_record({"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                            "auth_transport_failure": {"exception_types": types,
                                                       "certificate_verify_failed": certificate_failure}})

    def write_storage_failure(self, error):
        self._write_record({"schema": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                            "auth_storage_failure": {"exception_types": [type(error).__name__]}})

    def _write_record(self, record):
        encoded = (json.dumps(record, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
        with self.lock:
            if self.requests >= self.max_requests or self.total_bytes + len(encoded) > self.max_total_bytes:
                return
            # Reserve even on partial write failure, preserving the byte/count ceiling.
            self.requests += 1
            self.total_bytes += len(encoded)
            target = self.directory / f"request-{self.requests:03d}.json"
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)


def create_run_sink(run_root):
    if not run_root.is_absolute() or run_root.is_symlink():
        raise ValueError("Capture root must be an absolute directory without a symlink")
    run_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    secure_directory(run_root)
    child = run_root / ("app-server-" + str(os.getpid()) + "-" + secrets.token_hex(8))
    return CaptureSink(child)


def _in_scope(request):
    return (request.url.scheme == "https" and request.url.host == "api.mistral.ai"
            and request.url.port in (None, 443) and request.url.path == "/v1/chat/completions")


def install_capture(sink, *, modules=None):
    if modules is None:
        modules = []
        for name in ("httpx2", "httpx"):
            try:
                modules.append(importlib.import_module(name))
            except ImportError:
                pass
    for module in modules:
        original = module.AsyncClient._send_single_request

        def wrapper(original):
            @functools.wraps(original)
            async def send(self, request):
                snapshot = None
                try:
                    if _in_scope(request):
                        snapshot = sink.snapshot(request)
                except Exception:
                    pass  # Diagnostic failures never break inference.
                try:
                    response = await original(self, request)
                except BaseException as error:
                    if snapshot is not None:
                        try:
                            sink.write(snapshot, error=error)
                        except Exception:
                            pass
                    try:
                        if (request.url.scheme == "https" and request.url.host == "auth.mistral.ai"
                                and request.url.port in (None, 443)):
                            sink.write_auth_failure(error)
                    except Exception:
                        pass
                    raise
                if snapshot is not None:
                    try:
                        sink.write(snapshot, response)
                    except Exception:
                        pass
                return response
            return send

        # This boundary runs AFTER auth/request hooks (including native OIDC swap).
        # Public send() would see the marker instead of the actual outgoing headers.
        module.AsyncClient._send_single_request = wrapper(original)


def install_keyring_diagnostics(sink):
    """Observe storage failures without inspecting service/account/password arguments."""
    import keyring

    original = keyring.set_password

    @functools.wraps(original)
    def set_password(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception as error:
            try:
                sink.write_storage_failure(error)
            except Exception:
                pass
            raise

    keyring.set_password = set_password


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, help="New absolute private directory; never reused")
    parser.add_argument("--app-server", action="store_true", help=argparse.SUPPRESS)
    args, passthrough = parser.parse_known_args(argv)
    if passthrough[:1] == ["--"]:
        passthrough = passthrough[1:]
    if args.app_server:
        try:
            sink = create_run_sink(args.capture_dir)
            install_capture(sink)
            install_keyring_diagnostics(sink)
        except Exception as error:
            print(f"Native HTTP capture unavailable ({type(error).__name__}); "
                  "Vibe continues without capture.", file=sys.stderr)
        from vibe.app_server.stdio import main as server_main
        server_main(passthrough)
        return
    directory = args.capture_dir
    if directory is None:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "mvibe-native-captures"
        directory = base / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4))
    if not directory.is_absolute() or directory.exists():
        parser.error("--capture-dir must name a new absolute directory")
    from vibe.cli.launcher import exec_rust_cli
    print(f"Native Vibe HTTP capture: {directory}", file=sys.stderr)
    print("Prompts/tool arguments are sensitive. Browser OIDC enabled for this child only.", file=sys.stderr)
    exec_rust_cli(passthrough, extra_env={"VIBE_CLI_OIDC_ENABLED": "1"}, app_server_cmd=[
        sys.executable, str(Path(__file__).resolve()), "--app-server", "--capture-dir", str(directory)])


if __name__ == "__main__":
    main()
