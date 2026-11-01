# Reuse normal Vibe authentication in OpenCode and the proxy

Use the credential from a normal Vibe browser sign-in. The user confirmed that
this path consumes the expected Vibe quota in both the official CLI and the
installed OpenCode provider. The proxy uses the same Python session by default.
Its shared implementation and offline checks establish parity; a separate live
proxy quota check has not been reported.

A proxy integration smoke used the actual native credential and account lookup,
returned lookup-derived `Pro`, and verified that the default session was
`ExistingVibeSession`. Inference was intercepted locally. This checks the proxy's
auth path without adding a model request or claiming a live quota result.

The earlier direct OIDC implementation authenticated successfully but consumed
API usage on the tested account. Adding Vibe's User-Agent, completion metadata
and account-derived `Pro` label did not change that result. The difference was
in the authentication path. A successful response alone does not identify the
budget charged by the server.

## Browser sign-in has two different outcomes

The normal CLI flow and the experimental CLI OIDC registration both involve a
browser. They do not produce interchangeable credentials.

| Detail | Normal Vibe sign-in | Experimental CLI OIDC |
|---|---|---|
| Browser completion | Vibe's gateway exchanges the sign-in for a credential | The dedicated CLI registration obtains an ID token |
| Source evidence | `/vibe/sign-in/{process_id}/exchange` returns an `api_key` field | Native `_oidc_client.py` and `_oidc_runtime.py` validate and forward an ID token |
| Reused credential | Native keyring service `ai.mistral.vibe`, entry `MISTRAL_API_KEY` | Separate OIDC token bundle and refresh lifecycle |
| Account metadata endpoint | `/api/vibe/whoami` on the trusted console origin | `/api/vibe/account-context` on the trusted console origin |
| Observed quota on this account | Vibe quota in normal CLI and integrated OpenCode | API usage in OpenCode and the forced-OIDC native launcher |

The normal gateway's response field is named `api_key`. That name does not make
the credential equivalent to an arbitrary API key supplied through an environment
variable. Conversely, its presence in the native keyring cannot prove how it was
issued. The recommendation rests on the user's working login and counter checks.
mvibe does not accept a general environment API key as a fallback for this mode.

The native reference was Vibe 3.0.4, source commit
`338ad4bf0f73c4dd879d667cf7ab011555ae1751`. Relevant source is
`vibe/setup/auth/http_browser_sign_in_gateway.py`, `vibe/utils/api_keys.py`,
`vibe/utils/keyring.py` and `vibe/setup/auth/whoami.py`. The
[investigation record](../.kiro/specs/vibe-oidc-proxy/quota-investigation.md)
separates failed experiments from the working baseline.

## Python owns authentication for both consumers

The direct provider and proxy call
[`ExistingVibeSession`](../mvibe_auth/existing_vibe.py). The TypeScript plugin
does not implement keyring access, browser login, credential refresh or storage.

```text
Normal Vibe browser sign-in -> native Vibe keyring credential
                                      |
                             ExistingVibeSession
                              /              \
                    Python stdio bridge    Python proxy
                              |              |
                       OpenCode provider   OpenAI-compatible client
                              \              /
                            Mistral inference
```

Each request reads the exact native keyring entry again. A credential replacement
made by normal Vibe is therefore visible to the next request. Native Vibe owns
sign-in and renewal; mvibe does not refresh this opaque credential itself. If it
is missing or rejected, sign in through normal Vibe and retry deliberately.

On macOS the reader uses `/usr/bin/security` with fixed service/account arguments
and a bounded timeout. Other platforms use Python keyring. The reader performs
no writes or legacy migration. It does not read the experimental OIDC store or
the diagnostic launcher's isolated portable store.

Before reading credentials, the session restricts targets to HTTPS
`api.mistral.ai` on the normal TLS port and the three supported inference paths:
`/v1/models`, `/v1/chat/completions` and `/v1/embeddings`. It emits bearer auth
and the Vibe User-Agent, without OIDC external organization/workspace headers.
Snapshots have a 60-second request lifetime and a process-local HMAC revision.
That lifetime bounds the snapshot; it is not a claim about token expiry.

The [account-plan resolver](../mvibe_auth/account_plan.py) chooses its endpoint
from the snapshot kind. It bounds response size, time and cache lifetime, and
isolates cached results by credential kind and revision. Unavailable or unknown
plans are omitted. `Pro` is an account response mapping, never a hardcoded user
assumption or a billing instruction.

Cancellation drains owned credential reads. Neither consumer automatically
replays a failed inference request. Clients continue to execute their own native
and MCP tools; the proxy only forwards their protocol payloads.

## Select the working source at the point of use

Complete normal Vibe browser sign-in first. For the direct OpenCode provider,
set this option on its plugin entry:

```json
"authSource": "existing-vibe"
```

New installer entries select it automatically. Re-running an installer preserves
existing choices, including absent options that retain older OIDC behavior.
Migrate those entries explicitly, then restart OpenCode. The connection method
is labelled `Use existing Vibe login`; the provider remains `Mistral-Vibe`, with
the stable internal ID `vibe-oidc`.

The helper uses `bridge-vibe` and `login-vibe` entry points. NDJSON protocol v1
is unchanged. The connection command verifies locally available credential
material and emits the existing non-secret connection marker. It neither starts
a second browser flow nor proves that the server will accept the credential.
See the [provider guide](opencode-provider.md) for complete configuration.

For the proxy, prepare its dependencies once from the checkout:

```powershell
uv sync --locked --python 3.12 --extra proxy
uv run --locked --offline -- python proxy.py --log
```

The default app and CLI both select `ExistingVibeSession`. An explicit
`--auth-source existing-vibe` states the same choice. `--login` checks the native
credential; it does not renew it or open a browser. The proxy listens on loopback
port 8765. Its `/health` endpoint checks liveness, `/v1/models` checks upstream
access, and `/docs` and `/openapi.json` describe the supported surface.
`--offline` prevents dependency downloads, not HTTP requests to Mistral.

Keep experimental OIDC deliberate:

```powershell
uv run --locked --offline -- python proxy.py --auth-source oidc
```

That mode retains its own login, encrypted persistence and refresh behavior.
It has not demonstrated the expected Vibe quota attribution on this account.

## Diagnose a request without changing its authentication

Ordinary diagnostics stay off by default. Proxy `--log` writes safe request
events to stdout; plugin `diagnostics: true` writes safe events to stderr. They
report status and guidance without request bodies, credentials or exception text.
These events do not establish which subscription budget the server used.

Payload inspection requires separate opt-in capture. The plugin's
`captureDirectory` and `captureBody` options record bounded, private, redacted
application requests and original response headers. Response bodies remain
unread by capture. See [capture configuration](opencode-provider.md#local-http-capture-for-quota-diagnosis).
Conversation and tool content can still contain secrets that redaction cannot
recognize, so review captures locally before sharing them.

The native comparison launcher is:

```powershell
& D:\Source\mvibe\scripts\trace-native-vibe.ps1
```

Its default preserves existing native authentication. Earlier versions forced
experimental OIDC and a separate credential store, invalidating the comparison
with normal Vibe. Experimental `-Oidc` and `-PortableStore` options remain
explicit; do not enable them when observing the working default login.

## What the evidence establishes

On 2026-11-01, the user confirmed correct Vibe quota attribution for normal Vibe,
the corrected native capture launcher, and OpenCode after the existing-login
integration at `6fd0853`. The pinned consumer test used actual OpenCode 2.2.5;
the user-reported 2.2.6 installation was not independently established by that
test. Native capture selected `mistral-large-4-0`, while earlier OpenCode captures
selected `mistral-large-4`. The implementation does not silently rewrite aliases.

Offline tests cover credential-source isolation, fresh reads, destination policy,
account lookup selection, cancellation, streaming, tool payloads and no replay.
An actual helper check read the native credential and looked up `Pro`, with
inference intercepted locally. These checks support the implementation; the
user's counter observations establish the live quota result.

This evidence is specific to the tested account and requests. It does not prove
a universal billing rule for all opaque credentials, models or plans. Revisit
the recommendation if the same existing-login route later consumes API usage.
Preserve the selected source, model and server trace IDs when investigating that
case. Do not start by changing User-Agent or plan metadata again.
