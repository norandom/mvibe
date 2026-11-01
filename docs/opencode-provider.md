# OpenCode Mistral-Vibe provider

The user confirmed correct Vibe quota usage with `authSource: "existing-vibe"`.
The [authentication writeup](vibe-authentication.md) explains the working path,
the experimental OIDC distinction and the scope of that live result.

For opt-in direct-provider diagnostics, set `"diagnostics": true` in the plugin's
`options` and restart OpenCode. With the native CLI, add `--print-logs` to see
forwarded plugin events, for example `opencode --print-logs run --standalone --model vibe-oidc/mistral-large-4 "Create hello.ps1"`.
See [diagnostic fields and limits](tool-compatibility.md#diagnose-the-proxy-route).

If a live checkout rebuild produces `Authentication helper is disposed`, finish
the build and run `opencode service restart`, then reconnect the app. OpenCode
2.2.5 can retain a cached SDK across plugin reloads after disposing its helper;
reopening the window alone may retain the background service. A private CLI
test can use `--standalone` instead. Existing credentials can be reused. Avoid
rebuilding the installed checkout during an active request; the release archive
provides a separate prebuilt installation path.

The selectable display name is **Mistral-Vibe**; the stable provider ID remains
`vibe-oidc`. This direct route uses the Python authentication library and does not need the
proxy server. Install with `./install.ps1` on Windows or `bash ./install.sh` on
macOS/Linux from the checkout root. Both support explicit tool/config paths,
prepare locked dependencies and back up changed configuration while preserving
JSONC comments. Restart OpenCode and run
`opencode auth login vibe-oidc --method vibe-browser --standalone`, then open
`/models`. Full entry-point instructions are in the [README](../README.md).

Choose the direct `vibe-oidc` plugin or an OpenAI-compatible client pointed at the
loopback proxy. New provider entries explicitly select `authSource: "existing-vibe"`.
Sign in through the normal Vibe application first. The provider reads only native
keyring service `ai.mistral.vibe`, entry `MISTRAL_API_KEY`, freshly for every
request. It ignores environment API keys and never modifies native credentials.
Normal Vibe owns sign-in and renewal; replacing the native credential is observed
on the next request. The connection method **Use existing Vibe login** verifies
the credential and does not launch another browser flow or accept manual keys.
Python 3.12+ installed and managed
through uv is a prerequisite even for the TypeScript plugin.

Both routes send the exact native Vibe 3.0.4 `User-Agent` from the shared library.
The direct provider accepts only `User-Agent` as helper client metadata alongside
the three credential/tenant headers; it replaces a caller User-Agent only when
the helper supplies one. Other helper headers remain excluded. This metadata
does not establish subscription/quota attribution. The user observed native Vibe
quota usage and experimental direct OIDC API counter usage. The integrated
existing-login provider now has the user's successful live quota check; no quota
guarantee follows from a keyring entry or request metadata. Chat requests receive the shared native metadata defaults,
plus actual OpenCode session/message/client context when available. Unknown
plan fields are omitted. See the [metadata contract](auth-library.md#native-completion-request-metadata).

## Prepare the direct provider

From this checkout, prepare Python and the locked plugin dependencies explicitly
(these setup commands may download dependencies):

```powershell
uv sync --locked --python 3.12 --no-dev --no-editable
Set-Location packages/opencode-vibe-oidc
bun install --frozen-lockfile --ignore-scripts
bun run typecheck
bun run build
```

Use Bun 1.4.4, TypeScript 5.9.3, `@opencode/plugin` 2.2.5 and
`@ai-sdk/openai-compatible` 2.0.88 for the reproducible reference pair. The
prepared native OpenCode CLI reports 2.2.5. User-reported 2.2.6 remains unverified.
Target current OpenCode 2 stable; these exact locks are verification inputs, not
a requirement to disable operator updates. Version 1 packages named
`@opencode-ai/*` are a different release line.

Add this entry to the project's `opencode.json`, replacing absolute paths with
your checkout and uv-created environment. The package directory must retain its
prepared `node_modules` and built `dist`; it is not a published package.

```json
{
  "plugins": [{
    "package": "D:/Source/mvibe/packages/opencode-vibe-oidc",
    "options": {
      "authSource": "existing-vibe",
      "audience": "https://api.mistral.ai",
      "helperCommand": {
        "executable": "D:/Source/mvibe/.venv/Scripts/python.exe",
        "args": ["-m", "mvibe_auth", "bridge"]
      }
    }
  }]
}
```

This executable is the absolute interpreter of the uv-created environment; never
use a bare Python alias or a WindowsApps executable. An alternative command is
`executable: "uv"` with args
`["run", "--offline", "--no-sync", "--project", "D:/Source/mvibe", "-m", "mvibe_auth", "bridge"]`.
Prepare that project/environment first. Request handling never installs Python,
libraries or dependencies. On Windows the helper supervisor requires Windows 10+
native Job Objects and tracks only its owned process tree.

With `authSource: "existing-vibe"`, the provider changes the configured terminal
`bridge` argument to `bridge-vibe` and connects through `login-vibe`. An explicit
terminal `bridge-vibe` is also supported in this mode. The native credential is
sent only to trusted Mistral inference destinations; OIDC external tenant headers
are omitted. Keyring presence cannot establish browser issuance or entitlement;
obtain the intended credential by completing normal Vibe browser sign-in.
Missing credentials require that normal Vibe sign-in, then reconnect in OpenCode.

For experimental direct OIDC, set `authSource: "oidc"` and retain a helper ending
in `bridge`. Omitting `authSource` preserves this legacy behavior. The installer
sets existing-login mode only for newly added plugin entries; rerunning it keeps
existing choices, including entries with no option. To migrate one, add
`"authSource": "existing-vibe"` deliberately and restart OpenCode. Other option
values are rejected.

In experimental OIDC mode, the bridge and login command opt in when `VIBE_CLI_OIDC_ENABLED` is absent;
an explicit false value disables OIDC. The plugin's `options.audience` must match
the Python `VIBE_CLI_OIDC_AUDIENCE` (default
`https://api.mistral.ai`). Configure issuer/client/callback/scope and optional
`VIBE_CLI_OIDC_ORGANIZATION_ID` / `VIBE_CLI_OIDC_WORKSPACE_ID` there as needed.
Select **Mistral-Vibe** and its **Mistral-Vibe** connection method in OpenCode. The host
runs the matching Python `login` command and opens browser sign-in. OpenCode
persists only `vibe-oidc-connected-v1`, a non-secret marker in a key-shaped record;
real OIDC credentials stay in the shared library's separate encrypted store. This
ID-token route is distinct from the credential issued by normal Vibe browser
sign-in. In existing-login mode credentials stay in native Vibe's keyring.
The marker is never
an upstream API key. Each inference request reloads current credentials through
the helper; streams and tool calls pass through and cancellation closes work.

## Discover models and metadata

After connection the plugin queries authenticated `/v1/models`. Account
entitlement controls model availability. Only returned upstream IDs with complete
verified or explicit operator metadata are exposed. The provider reads matching
metadata from OpenCode's canonical Mistral catalog, including inactive templates;
catalog entries never add an ID absent from the authenticated response. Only
safe capabilities, limits, release/status and cost fields are copied, with
explicit operator fields taking precedence. Absent capabilities, token
limits or prices are never invented. If discovery reports incomplete metadata,
set `options.modelMetadata["exact-upstream-id"]` with the verified fields:
`name`, `capabilities.tools` (boolean), nonempty `capabilities.input` and
`capabilities.output` arrays, positive integer `limit.context` / `limit.output`,
numeric `time.released`, `status` (`active`, `alpha`, `beta`, `deprecated`) and
a nonempty `cost` array containing nonnegative `input`, `output` and
`cache.read` / `cache.write` (USD per million tokens). Output limit cannot exceed
context. Verify these values against your service/account contract; metadata
does not grant entitlement or add an ID missing from upstream discovery.

Missing runtime/library: prepare the uv environment and correct `helperCommand`.
Protocol/API/audience mismatch: install matching library/plugin versions, rebuild
the plugin and align audience. Current contract is NDJSON protocol **1** and
distribution/API **0.1.0**, accepted API major **0**. Host registration failures:
check the tested OpenCode 2 SDK pair and rerun compatibility. Auth failures:
reconnect deliberately; model 401/403 also requires checking account access.
These errors give setup/reconnection guidance and never offer API-key fallback.

## Loopback proxy uses the same existing Vibe login

Prepare `uv sync --locked --python 3.12 --extra proxy`, then run
`uv run --locked --offline -- python proxy.py`. The default reads the existing
normal Vibe credential through the shared Python session. Complete normal Vibe
sign-in first; `--login` validates that credential without a browser. Select a loopback port with
`--port`. The script form `uv run --script proxy.py` has separate PEP 723
dependencies and may download them on first use. Copy **both** `mvibe_auth/` and
`mvibe_proxy/` alongside `proxy.py`; include `LICENSE.vibe` when redistributing.
Explicit `--auth-source oidc` retains the experimental ID-token login/refresh
flow. See [the authentication writeup](vibe-authentication.md) for why the two
browser paths differ and what the user confirmed about quota attribution.

Diagnostic logging defaults to off for both consumers. Add `"diagnostics": true`
to the direct plugin's `options` object for safe JSON `provider_request` events
on stderr. Add `--log` to the proxy command for safe structlog `proxy_request`
events on stdout. Status 422 hints point to tool schema and reasoning history
serialization; provider 401 guidance follows its configured authentication source.
Neither mode logs request or
response bodies, credentials, URLs or exception text, nor automatically retries
inference. Plugin events end at response headers, so they do not establish SSE
completion. See [diagnostic fields and limitations](tool-compatibility.md#diagnose-the-proxy-route)
and [observed live results](live-results.md).

For an OpenAI-compatible client configure base URL
`http://127.0.0.1:8765/v1`, a placeholder `apiKey` such as `local` if its SDK
requires one, and an exact ID obtained from `GET /v1/models`. The placeholder
is ignored for upstream authentication. Supported operations:

| Operation | Purpose |
| --- | --- |
| `GET /v1/models` | Discover actual account model IDs |
| `POST /v1/chat/completions` | Chat, streaming and tool-call payloads |
| `POST /v1/embeddings` | Embeddings where entitled |
| `GET /health` | Local liveness only: `{"status":"ok"}` |

Other GET/POST `/v1/` endpoints return 404; unsupported methods are rejected.
Responses API is not implemented. **Local caller authentication is absent**;
any local process able to reach the loopback port can call it. Health does not
validate credentials, entitlement, upstream readiness or stream-client recovery.
Transport cleanup failure retires the client and requires proxy restart; health
can remain healthy while forwarding returns 502.

The experimental OIDC routes share portable encrypted Python persistence with a short OS-keyring
encryption key. The legacy Vibe service `ai.mistral.vibe.oidc`, entry
`cli-oidc:v1:tokens`, is read only when the new store is absent. Subsequent writes
do not synchronize with installed Vibe. Library consumers cooperate with native
locking. A successful cancelled/terminated writer might already have committed;
next resolution reloads authoritative storage. See the
[storage lifecycle](auth-library.md) before moving or deleting credential files.

## Offline and current-host verification

Use the [tool baseline and update guide](tool-compatibility.md) for the sequential
native/MCP command, immutable HTTP/plugin snapshots, exact prepared candidate
pairs, proxy OpenAPI/docs and correlated structured stdout diagnostics.

See [README](../README.md) for the complete offline runner and preparation.
`test/host-integration.ts` loads this plugin in an isolated real pinned host with
synthetic login/helper/fetch responses. It verifies marker persistence, current
credentials, models, incremental streaming, tools, cancellation and no retry after
401/503. No live account is exercised. [Consumer evidence](consumer-integration.md)
separates startup from host evidence. Browser registration, real subscription,
live refresh and inference compatibility remain unverified.

For deliberate network-enabled current stable discovery/preparation, from the
plugin directory run `bun run compatibility:current --discover-current`, then
`bun run compatibility:current --prepare-current` in a fresh preparation location.
The latter prepares a separate `.compat/<version>/provider`, runs the same native
host gate and records a separate report. It refuses to overwrite an existing
prepared pair. Discovery alone is never a compatibility pass.

### Local HTTP capture for quota diagnosis

Capture is off by default and independent of `diagnostics`. In the provider's
options, set `captureDirectory` to an absolute local directory outside tracked
source (for this checkout, `D:/Source/mvibe/reports/http-capture` is ignored by Git).
Set `captureBody: true` separately to include the final adapted chat JSON. Restart
OpenCode after changing these options. Remove both options and restart to disable.

Each provider instance creates a `session-<random UUID>` directory. Request and
response JSON files share `request_id`; inspect `body.model`, `body.metadata`,
client headers and the original response `status`, `x-request-id`, `request-id`,
`x-correlation-id` and `traceparent`. The response's `x-should-retry` value is
recorded before the provider disables retries. Capture never reads response
content or SSE, and never makes an additional inference request. Query values
and unknown header values are redacted. Tenant identifiers are pseudonyms stable
only within that capture session; authorization, cookies, credential-like JSON
fields and known resolved credential strings are redacted.

**Body captures contain conversations and tool content.** Arbitrary secrets in
prompts/tool text cannot be reliably recognized. Keep captures local and private;
review/redact them before sharing. The implementation creates exclusive files
with mode 0600 and session directories with mode 0700 on Unix; on Windows it
restricts the new session directory ACL to the current user before writing.
If ACL setup or writing fails, capture stops without changing inference.

Limits per provider instance: 64 requests, 256 KiB per outgoing JSON body,
384 KiB per record, 8 MiB total. Oversized bodies are omitted completely, with
`body_state` explaining why; session/record exhaustion writes a `capture_limit`
marker. Bodies unavailable as already-adapted chat JSON are not read from a
stream and are marked unavailable. Depth beyond 40 is explicitly omitted.
No response body is captured. IO failure may leave an incomplete pair or an empty
session directory. Restart creates a new bounded session; old captures are not
automatically deleted, so delete the diagnostic directory when finished.
