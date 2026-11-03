# Use the local proxy

Run these commands from the checkout root. The direct Desktop/CLI plugin does not need this proxy.

## 1. Prepare this checkout

Install uv, then open a terminal in the checkout:

```powershell
Set-Location D:/Source/mvibe
uv sync --locked --python 3.12 --extra proxy
```

Setup may download Python and dependencies. Use uv-managed Python throughout;
there is no need to install Windows Store Python. Bun is needed only for the
direct TypeScript plugin and its tests.

## 2. Start the proxy with your existing Vibe login

Complete normal Vibe browser sign-in first. The proxy defaults to the same
`ExistingVibeSession` used by the provider and rereads the native keyring
credential for each request. No second login or manual API key is required.

In terminal A:

```powershell
uv run --locked --offline -- python proxy.py
```

Keep this terminal open and wait for `Application startup complete`. If the
credential is absent or rejected, sign in through normal Vibe and retry. The
default proxy does not open the experimental OIDC browser flow.

`--offline` applies to dependencies. The proxy still contacts Mistral; explicit
OIDC mode also contacts its issuer. The `python` after `--` uses the uv project environment and its
lockfile. `uv run --locked --offline proxy.py` instead selects the script's
separate inline dependency metadata and requires a separate script lockfile.

Stop with Ctrl+C. `--login` validates the existing native credential; it does not
renew it. Use `--port 8766` for another port and update client URLs accordingly.
To deliberately test the separate ID-token route, pass `--auth-source oidc`.
Only that mode uses the experimental browser login and encrypted OIDC store.

### Start scripts and bind address

`./start_proxy.sh` (Bash/WSL) and `./start_proxy.ps1` (PowerShell) wrap the
command above in the locked project environment and forward extra arguments:

```powershell
./start_proxy.ps1                 # 127.0.0.1:8765
./start_proxy.ps1 -Host 0.0.0.0   # all interfaces; prints a warning
./start_proxy.ps1 --port 8799 --log
```

```bash
./start_proxy.sh                  # 127.0.0.1:8765
./start_proxy.sh --host 0.0.0.0   # all interfaces; prints a warning
./start_proxy.sh --port 8799 --log
```

`--host` defaults to `127.0.0.1` (loopback). Binding `0.0.0.0` (or any
non-loopback address) works but prints a warning, because the proxy has no
caller authentication: anyone who can reach the port can use your Vibe
credential. Prefer loopback, or front the port with your own authenticated
proxy. The same flag exists on `proxy.py` itself (`python proxy.py --host
0.0.0.0`).

### Run in Docker

`Dockerfile` + `docker-compose.yml` run only the proxy endpoint. Compose
publishes `127.0.0.1:${MVIBE_PROXY_PORT:-8765}:8765` (loopback on the host;
inside the container the proxy binds `0.0.0.0`, which port publishing requires)
and mounts the Vibe credentials read-only:

```bash
# On this workstation the Docker daemon runs in WSL Debian, so run compose there:
wsl -d Debian bash -c 'cd /mnt/d/Source/mvibe && docker compose up --build'
# The mount paths resolve in the WSL filesystem; point them at the Windows
# profile via /mnt/c, e.g.:
wsl -d Debian bash -c 'cd /mnt/d/Source/mvibe && MVIBE_AUTH_DATA=/mnt/c/Users/mariu/AppData/Local/mvibe-auth MVIBE_KEYRING_DATA=/home/mc/.local/share/python_keyring docker compose up --build'
# plain Linux/macOS host:
docker compose up --build
# override the credential locations on the Docker host if needed:
#   MVIBE_AUTH_DATA     dir with credentials.v1.enc  (default ~/.local/share/mvibe-auth; Windows host via WSL: /mnt/c/Users/<you>/AppData/Local/mvibe-auth)
#   MVIBE_KEYRING_DATA  dir with python_keyring/keyring_pass.cfg
#                       (default ~/.local/share/python_keyring; Windows host via WSL: /mnt/c/Users/<you>/AppData/Local/Python Keyring)
# explicit OIDC login inside the container (publishes the callback port too):
docker compose run --rm --service-ports proxy --auth-source oidc --login
```

Windows Credential Manager is not a file, so export the native Vibe entry once
into the file keyring (never prints the secret; run on the Windows host with
the `docker` extra installed):

```powershell
uv run --locked --extra docker -- python -c "import keyring; v = keyring.get_password('ai.mistral.vibe', 'MISTRAL_API_KEY'); import keyring.core; keyring.set_keyring(keyring.core.load_keyring('keyrings.alt.file.PlaintextKeyring')); keyring.set_password('ai.mistral.vibe', 'MISTRAL_API_KEY', v); print('exported' if v else 'no native credential found')"
# for --auth-source oidc also export: service 'ai.mistral.vibe.oidc', entry 'mvibe-auth:v1:fernet-key'
```

`keyring_pass.cfg` stores entries escaped and base64-encoded; always generate
it through `keyring.set_password` as above, never hand-edit it. Inside the
container the proxy reads it via `PYTHON_KEYRING_BACKEND=keyrings.alt.file.
PlaintextKeyring` (the `docker` extra) with `XDG_DATA_HOME=/data`, because a
container has no OS keyring/D-Bus.

The container uses the same default `existing-vibe` auth flow as the tested
OpenCode provider. It reads the keyring through a file backend
(`PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring`, installed via the
optional `docker` extra), because a container has no OS keyring/D-Bus. Windows
Credential Manager is not a file: export the two entries once into the mounted
`keyring_pass.cfg`, or sign in inside the container as shown above. The mounts
are read-only, so in-container OIDC refresh cannot persist a renewed token;
the default existing-vibe flow leaves renewal to normal Vibe.

## 3. Check health, models and a chat response with curl

Open terminal B. Use `curl.exe` to avoid the PowerShell curl alias:

```powershell
curl.exe --fail-with-body --silent --show-error --max-time 10 http://127.0.0.1:8765/health
```

Expected: `{"status":"ok"}`. This proves local liveness only. Discover the
models your signed-in account can actually access:

```powershell
$inventory = curl.exe --fail-with-body --silent --show-error --max-time 60 http://127.0.0.1:8765/v1/models
if ($LASTEXITCODE -ne 0) { throw 'Model discovery failed; stop here.' }
$models = ($inventory | ConvertFrom-Json).data
$models | Select-Object id, capabilities
$model = Read-Host 'Paste an exact chat-capable model ID from this list'
if ($model -notin $models.id) { throw 'Choose an ID returned by this account.' }
```

Choose a chat model, not an embedding-only model. Send a small public prompt:

```powershell
$body = @{
    model = $model
    messages = @(@{ role = 'user'; content = 'Reply with exactly OK.' })
    max_tokens = 32
    stream = $false
} | ConvertTo-Json -Depth 6
$requestFile = Join-Path $env:TEMP ('mvibe-chat-' + [guid]::NewGuid() + '.json')
[IO.File]::WriteAllText($requestFile, $body, [Text.UTF8Encoding]::new($false))
try {
    curl.exe --fail-with-body --silent --show-error --max-time 120 `
        http://127.0.0.1:8765/v1/chat/completions `
        -H 'Content-Type: application/json' --data-binary "@$requestFile"
    if ($LASTEXITCODE -ne 0) { throw 'Chat failed; inspect the HTTP response.' }
} finally {
    Remove-Item -LiteralPath $requestFile -ErrorAction SilentlyContinue
}
```

Success means a chat-completion JSON response containing an assistant answer.
No caller Authorization header is required. Client SDKs that require an `apiKey`
can use `local`; the proxy obtains upstream authentication from the selected
shared session, normally the existing Vibe login.

## 4. Run OpenCode through the proxy

Keep terminal A running. Open a fresh PowerShell terminal for this test; close
it afterward to discard temporary environment overrides. These OpenCode 2
configuration fields were checked against the local 2.2.5 CLI. Record your
version because OpenCode updates independently.

```powershell
$probeRoot = Join-Path $env:TEMP ('mvibe-opencode-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $probeRoot | Out-Null
$env:OPENCODE_TEST_HOME = $probeRoot
$env:OPENCODE_DISABLE_AUTOUPDATE = 'true'
$env:OPENCODE_DISABLE_MODELS_FETCH = 'true'
foreach ($name in @('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME')) {
    Set-Item "env:$name" (Join-Path $probeRoot $name)
}
$env:MISTRAL_API_KEY = 'local'
$config = @{
    update = 'disable'
    share = 'disabled'
    providers = @{
        mistral = @{ settings = @{ baseURL = 'http://127.0.0.1:8765/v1'; apiKey = 'local' } }
    }
} | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $probeRoot 'opencode.json'), $config)
Set-Location $probeRoot
opencode --version
opencode models --standalone
$model = Read-Host 'Enter a chat ID in BOTH proxy discovery and OpenCode, without mistral/'
opencode run --standalone --model "mistral/$model" 'Reply with exactly OK. Do not use tools or read files.'
```

Use the native executable's full path if `opencode` is not on PATH. The desktop
installation here provides
`C:/Users/mariu/AppData/Local/Programs/OpenCode/resources/opencode-cli.exe`.
The `local` value enables the client adapter; it is a placeholder, and upstream
authentication still comes from the proxy's selected shared session. This checks the proxy route. The direct
`vibe-oidc` provider has its own
[configuration and model-metadata requirements](opencode-provider.md).

Require an assistant answer from OpenCode and the corresponding chat request in
terminal A. A visible model list alone does not prove inference. Stop on failure;
repeated sign-ins will not resolve an endpoint or model mismatch.

## Ollama- and Anthropic-compatible endpoints

The proxy also speaks two adapter dialects, translated to the same upstream
passthrough with the same auth flow (default existing-vibe) and diagnostics:

| Dialect | Endpoints |
| --- | --- |
| Ollama | `GET /api/version`, `GET /api/tags`, `POST /api/chat`, `POST /api/generate`, `POST /api/show`, `POST /api/embed` (streaming = NDJSON `done` frames) |
| Anthropic | `POST /v1/messages` (SSE events when `stream=true`), `GET /v1/models` returns the Anthropic paginated shape when an `anthropic-version` header is sent |

Unknown `/api/*` paths return an Ollama-shaped 404; adapter error bodies never
contain upstream payloads. Live smoke (terminal B, proxy running in terminal A):

```powershell
curl.exe -s http://127.0.0.1:8765/api/version
curl.exe -s http://127.0.0.1:8765/api/tags
$body = @{ model = 'mistral-large-4'; messages = @(@{ role = 'user'; content = 'Reply with exactly OK.' }); stream = $false } | ConvertTo-Json -Depth 6
curl.exe -s http://127.0.0.1:8765/api/chat -H 'Content-Type: application/json' --data-binary $body
$body = @{ model = 'mistral-large-4'; prompt = 'Reply with exactly OK.' } | ConvertTo-Json -Depth 6
curl.exe -s http://127.0.0.1:8765/api/generate -H 'Content-Type: application/json' --data-binary $body
$body = @{ model = 'mistral-large-4'; max_tokens = 16; messages = @(@{ role = 'user'; content = 'Reply with exactly OK.' }) } | ConvertTo-Json -Depth 6
curl.exe -s -N http://127.0.0.1:8765/v1/messages -H 'Content-Type: application/json' -H 'anthropic-version: 2023-06-01' --data-binary $body
```

Streaming calls return NDJSON (Ollama) or `event:`/`data:` SSE frames
(Anthropic). Both shapes are pinned by offline contract tests in
`tests/test_compat.py`; see `.kiro/specs/proxy-endpoint-compatibility/`.

### Thinking levels

Release availability: **v1.0.0** already forwards OpenAI request fields,
including `reasoning_effort`. The Anthropic `thinking` and Ollama `think`
mappings below are introduced in **v1.1.0** and absent from v1.0.0.
The proxy does not install the direct provider's OpenCode reasoning picker.

Chat requests can carry a thinking level, forwarded to the Mistral API as the
top-level `reasoning_effort` field (`none` = off; levels are sent as written,
e.g. `low`/`high`/`max` for mistral-large-4, which rejects `none`):

- OpenAI dialect: set `"reasoning_effort": "high"` in the `/v1/chat/completions` body (passed through verbatim).
- Anthropic dialect: `"thinking": {"type": "disabled"}` maps to `"none"`; `{"type": "enabled", "budget_tokens": N}` maps to `low`/`medium`/`high` at the 4096/16384/32000 thresholds (default `high`); a non-standard `"effort"` string is sent as written.
- Ollama dialect: `"think": false` maps to `"none"`; `"low"`/`"medium"`/`"high"` map as written; `true` adds no field (server default).

A level the model rejects surfaces as the provider's own error. See
`.kiro/specs/thinking-levels/`.

This is request-option support, not full reasoning compatibility across dialects:

| Route | Reasoning behavior |
| --- | --- |
| `/v1/chat/completions` | Forwards the supplied request and upstream response without translating reasoning history. Clients must supply Mistral-compatible assistant history. |
| `/v1/messages` | Translates the request setting in v1.1.0; the response adapter does not emit Anthropic thinking blocks, and thinking history blocks are unsupported. |
| `/api/chat`, `/api/generate` | Translate the request setting in v1.1.0; response adapters do not expose a dedicated Ollama thinking field or translate thinking history. |

Mappings are not model-aware: for example, a budget mapping to `medium` can
fail on Large 4, whose curated levels are `low`, `high`, and `max`. `think: true`
omits the effort field; it does not force reasoning on. For explicit control
and unmodified reasoning responses, use the OpenAI route with the model's
accepted `reasoning_effort` value. Live acceptance of every model/level pair
remains separate from offline forwarding tests.

Reusable live smoke scripts covering all three dialects plus embeddings:
`smoketests/smoke.ps1` (Windows) and `smoketests/smoke.sh` (bash); see
`smoketests/README.md`.

## What about searches?

The proxy supports models, chat completions (including streaming and tool-call
payloads) and embeddings. It has no search endpoint or search executor. Forwarding
a model's tool call does not execute the tool. Embeddings do not perform a web search.

OpenCode can execute a separately configured search tool or MCP server and send
its results back through chat. Proxying a Vibe-specific authenticated search
service needs a follow-up spec covering the endpoint, permissions, result shape,
citations, errors and tests. Responses API is also unsupported.

## If a check fails

| Last successful step | Next action |
| --- | --- |
| Missing native credential | Finish normal Vibe sign-in, then retry; there is no automatic environment API-key fallback. |
| Explicit OIDC callback only | Read terminal A; token validation and persistence must finish before startup. |
| Proxy startup | Check the configured port and `/health`. |
| Health | Check discovery, the selected auth source and native credential availability; OIDC tenant settings apply only to explicit OIDC mode. |
| Model discovery | Use an exact chat-capable ID and inspect the chat HTTP response. |
| curl chat | Check OpenCode version, configuration, model ID and proxy access log. |
| Forwarding returns 502 after stream cleanup failure | Restart the proxy; health can still report OK. |

The 2026-10-31 Windows smoke test passed browser OIDC login, encrypted credential
persistence and reuse, curl health/models/chat, and an OpenCode 2.2.5 CLI prompt
through the proxy. The initial `CredWrite` failure was fixed by storing the
encrypted bundle in a file and only a short key in the OS keyring. See
[the live results](live-results.md) for the tested scope. Live refresh,
embeddings and tool execution remain unverified. The direct provider was
subsequently verified separately as described above.

By default the server binds to loopback (`--host 127.0.0.1`) and has no local
caller authentication. Any local process that can reach the port can use it;
`--host 0.0.0.0` extends this to the network and prints a warning. Keep
tokens, callback URLs and raw credential exports out of logs, issues and test
evidence.
