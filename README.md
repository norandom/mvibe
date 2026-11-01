# mvibe

Use your Vibe subscription through browser OIDC from a local OpenAI-compatible
proxy or the direct OpenCode 2 provider. Both use the same Python authentication
library. No Mistral API key is needed.

For OpenCode, install the direct provider below. For curl and protocol checks,
use the proxy sequence afterward. These examples use PowerShell on Windows.

## Install Mistral-Vibe directly in OpenCode

The direct provider does not use the proxy. It calls Mistral through the shared
Python OIDC library, including the same encrypted credentials and refresh logic.
Prepare native uv, Bun 1.4.4 and OpenCode 2, then run from this checkout:

```powershell
./install.ps1
```

On macOS or Linux:

```bash
bash ./install.sh
```

If tools are not on PATH, pass `-Bun`, `-Uv` and `-OpenCode` executable paths to
PowerShell, or `--bun`, `--uv` and `--opencode` to Bash. The installer prepares
locked dependencies, builds the plugin and adds it to your user config. Existing
JSONC comments and unrelated settings are retained; changed configs get a backup.
Running it again updates the same entry. Use `-ConfigPath` / `--config-path` for
another configuration file. Setup can download dependencies and does not start
the proxy or sign in automatically.

Restart OpenCode, then connect the provider:

```powershell
opencode auth login vibe-oidc --method vibe-browser --standalone
```

Open `/models` and choose **Mistral-Vibe**. Its saved provider ID remains
`vibe-oidc`, so CLI model names use `vibe-oidc/<model-id>`. Existing matching OIDC
credentials can be reused without another browser sign-in. The provider exposes
authenticated model IDs with complete metadata from OpenCode's Mistral catalog
or your explicit overrides; incomplete entries are omitted. Keep this checkout
and its prepared dependencies in place. See the
[provider guide](docs/opencode-provider.md) for configuration and recovery.

The Windows PowerShell installation, provider label, 18-model inventory and a
direct CLI response were verified on OpenCode 2.2.5. See
[live results](docs/live-results.md). Native file/command tools and MCP tool
execution have separate [draft compatibility requirements](.kiro/specs/opencode-vibe-tool-compatibility/requirements.md)
and [end-to-end test matrix](.kiro/specs/opencode-vibe-tool-compatibility/verification-plan.md);
their live round trips have not yet been verified.

## 1. Prepare this checkout

Install uv, then open a terminal in the checkout:

```powershell
Set-Location D:/Source/mvibe
uv sync --locked --python 3.12 --extra proxy
```

Setup may download Python and dependencies. Use uv-managed Python throughout;
there is no need to install Windows Store Python. Bun is needed only for the
direct TypeScript plugin and its tests.

## 2. Start the proxy and finish browser sign-in

In terminal A:

```powershell
uv run --locked --offline -- python proxy.py
```

Keep this terminal open. Startup reuses matching credentials or opens the Vibe
OIDC sign-in in your default browser. Complete sign-in there. The browser's
"Sign-in received. Return to Vibe." message confirms receipt of the callback;
wait for `Application startup complete` in terminal A before continuing.

`--offline` applies to dependencies. The proxy still contacts the issuer and
Mistral. The explicit `python` after `--` uses the uv project environment and its
lockfile. `uv run --locked --offline proxy.py` instead selects the script's
separate inline dependency metadata and requires a separate script lockfile.

Stop with Ctrl+C. Force another browser sign-in with
`uv run --locked --offline -- python proxy.py --login`. Use `--port 8766` for
another port and update client URLs accordingly.

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
can use `local`; the proxy derives upstream authentication from OIDC.

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
authentication still uses Vibe OIDC. This checks the proxy route. The direct
`vibe-oidc` provider has its own
[configuration and model-metadata requirements](docs/opencode-provider.md).

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
| Browser callback only | Read terminal A; token validation and persistence must finish before startup. |
| Proxy startup | Check the configured port and `/health`. |
| Health | Check discovery status, account entitlement and OIDC/tenant configuration. |
| Model discovery | Use an exact chat-capable ID and inspect the chat HTTP response. |
| curl chat | Check OpenCode version, configuration, model ID and proxy access log. |
| Forwarding returns 502 after stream cleanup failure | Restart the proxy; health can still report OK. |

The 2026-10-31 Windows smoke test passed browser OIDC login, encrypted credential
persistence and reuse, curl health/models/chat, and an OpenCode 2.2.5 CLI prompt
through the proxy. The initial `CredWrite` failure was fixed by storing the
encrypted bundle in a file and only a short key in the OS keyring. See
[the live results](docs/live-results.md) for the tested scope. Live refresh,
embeddings and tool execution remain unverified. The direct provider was
subsequently verified separately as described above.

The server binds to loopback and has no local caller authentication. Any local
process that can reach the port can use it. Keep tokens, callback URLs and raw
credential exports out of logs, issues and test evidence.

## Repeatable checks and engineering reference

The offline suite uses synthetic boundaries. It does not establish real account
entitlement, browser registration or inference. Once dependencies and a native
host are prepared, run the full pipeline from the checkout root:

```powershell
pwsh -NoProfile -File scripts/verify.ps1 -Bun C:/tools/bun.exe -HostBinary C:/Users/mariu/AppData/Local/Programs/OpenCode/resources/opencode-cli.exe
```

Use absolute paths to prepared Bun 1.4.4 and OpenCode 2 executables. Reports go
to `reports/verification/<run-id>/acceptance.json`. Focused Python checks use
`uv run --locked --offline pytest`; it uses two xdist workers with scope-based
scheduling. Each worker retains the offline guards and isolated state. Use
`uv run --locked --offline pytest -n 0 tests/test_refresh.py` for serial debugging,
or pass `-n 4` for a different worker count. The full PowerShell runner accepts
`-PythonWorkers 0` for serial execution (default: 2). Packaging uses
`uv build --offline --no-build-isolation`.

Tool compatibility follows a [practical baseline plan](.kiro/specs/opencode-vibe-tool-compatibility/tasks.md):
native files/commands, one MCP tool workflow, then a repeatable report and guide.
Broader tool families remain explicit follow-ups, not an all-tool support claim.

- [Shared auth API and lifecycle](docs/auth-library.md)
- [Direct OpenCode plugin setup, build and compatibility](docs/opencode-provider.md)
- [Live procedure and evidence checklist](docs/live-verification.md)
- [Live results](docs/live-results.md)
- [Offline acceptance](docs/offline-acceptance.md)
- [Synthetic native host evidence](docs/consumer-integration.md)
- [Vibe source attribution and deviations](docs/vibe-provenance.md)

For the runtime-only library, prepare with
`uv sync --locked --python 3.12 --no-dev --no-editable`; add `--extra proxy` when
running the proxy. `uv run --script proxy.py` uses separate inline dependencies
and may download them. When copying that script, retain `mvibe_auth/`,
`mvibe_proxy/` and `LICENSE.vibe`. See `uv.lock` for dependency versions.
