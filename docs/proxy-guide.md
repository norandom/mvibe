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

The server binds to loopback and has no local caller authentication. Any local
process that can reach the port can use it. Keep tokens, callback URLs and raw
credential exports out of logs, issues and test evidence.
