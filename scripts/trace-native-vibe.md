# Capture the installed official Vibe client

From PowerShell, run this command to open the installed official Vibe Rust TUI with its actual Python app server instrumented:

```powershell
& D:\Source\mvibe\scripts\trace-native-vibe.ps1
```

The wrapper uses `%APPDATA%\uv\tools\mistral-vibe\Scripts\python.exe`. It uses the installed `vibe.cli.launcher.exec_rust_cli` and `vibe.app_server.stdio.main`; it does not use mvibe's proxy, authentication helpers, local fixture, or sibling source checkout. The only environment change is `VIBE_CLI_OIDC_ENABLED=1` in the launched child. Existing Vibe configuration, credentials and account selection remain native. Select/sign in with the browser account in Vibe before sending the comparison prompt. An existing API key environment/config setting may otherwise select a different account path. `auth_token_kind` is only `jwt_shaped`, `opaque`, or `absent`; shape alone does not prove OIDC or any claim, and tokens are never decoded.

The launcher prints a new absolute capture directory under `%LOCALAPPDATA%\mvibe-native-captures`. To choose a directory, pass `-CaptureDir 'C:\absolute\new-directory'`. It must not already exist. Native Vibe flags can follow the wrapper arguments. The launcher never submits a prompt automatically. A prompt you submit in the TUI makes the normal paid native API call.

The capture includes the final buffered request JSON and redacted outgoing headers for HTTPS `api.mistral.ai:443/v1/chat/completions` only. The instrumented HTTPX boundary runs after native authentication/request hooks, including the browser OIDC token/tenant swap. It captures status and the actual `mistral-correlation-id` and `x-kong-request-id` response headers when present, including error statuses. It never reads or records a response body. Other hosts/routes, login/refresh calls and telemetry are excluded. Native retries remain native; each actual completion transport attempt can produce a separate record, including background completion calls.

Authorization, cookies, credential fields, bearer/JWT-like text, and known credential values are redacted recursively, including repeated values in prompt text and echoed response IDs. Tenant, organization ID, and workspace ID fields and known repeated identity values use HMAC pseudonyms keyed randomly in memory for each app-server process; the key is never saved. Token budget fields such as `max_tokens` remain available for comparison. Redaction does not make prompts, tool arguments, source code or personal information safe to share. Review capture contents locally before sharing. Capture directories are outside the repository by default; never commit them.

The app server creates the directory and removes inherited Windows permissions and grants the current user access before writing any capture (existing explicit system/administrator permissions may remain). Files are created exclusively; an existing directory is refused. Each buffered request body is limited to 256 KiB; oversized, streaming, or non-JSON request bodies are omitted with a reason. A run writes at most 64 files and 8 MiB. No raw body is stored as a fallback. Capture errors do not change native request results, exception/cancellation identity, streams, or retry behavior. If initialization fails, the app server emits a generic stderr warning and continues without capture. If the app server restarts using the same directory, capture is disabled rather than reusing existing files. Missing captures alone therefore do not prove absence of requests.

The wrapper depends on the installed HTTPX/HTTPX2 private `_send_single_request` boundary. Recheck the offline tests after upgrading Vibe or its HTTP library. The implementation was checked against the installed native OIDC request hook, not inferred from project metadata fixtures.

Offline verification (no network, login or inference):

```powershell
.venv\Scripts\python.exe -m pytest tests/test_trace_native_vibe.py -o addopts='' -q
& "$env:APPDATA\uv\tools\mistral-vibe\Scripts\python.exe" .\scripts\trace-native-vibe.py --help
& "$env:APPDATA\uv\tools\mistral-vibe\Scripts\python.exe" .\scripts\trace-native-vibe.py -- --help
```

