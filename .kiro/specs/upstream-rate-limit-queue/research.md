# Research & Design Decisions

## Summary
- **Feature**: `upstream-rate-limit-queue`
- **Discovery Scope**: Extension (light discovery) of two existing request paths: the proxy's `make_acquire` send and the plugin's `createProviderFetch`.
- **Key Findings**:
  - Neither consumer handles 429 today beyond a diagnostic hint. The proxy forwards the upstream 429 byte-exact on `/v1` routes and translates it on adapter routes; the plugin forwards it with `x-should-retry: false` and `maxRetries = 0`, so the host fails the turn.
  - Mistral's own documentation recommends exponential backoff on 429 and does not guarantee a `Retry-After` header; field observations show `x-ratelimit-limit-req-minute` and `x-ratelimit-remaining-req-minute` without `Retry-After`. Detection must therefore key on the status code and treat `Retry-After` as optional guidance.
  - Both request paths already have a single pre-response seam (resolve credentials, then send) where a hold-and-re-send loop can live without touching streaming, authentication or the bridge protocol. The one structural change needed is to observe client disconnects during the wait.

## Research Log

### Mistral 429 contract
- **Context**: Detection (Req 1) needs the exact upstream signal; the operator reported 0.5 requests per second for Mistral Large on their workspace limits page.
- **Sources Consulted**:
  - https://docs.mistral.ai/resources/error-glossary (read 2026-11-09): error body format `{"object":"error","message","type","param","code"}`; `type` values include `rate_limit_error`; 429 is listed as "Too many requests". "Recommended retry strategy: for transient errors (429, 500, 502, 503, 504), implement exponential backoff." Sample uses `2 ** attempt + random.uniform(0, 1)` with a retry cap. No `Retry-After` or `x-ratelimit-*` header is documented.
  - https://github.com/earendil-works/pi/issues/9815 (observed 2026-09-20): a real 429 body `{"object":"error","message":"Rate limit exceeded","type":"rate_limited","param":null,"code":"1300","raw_status_code":429}` with headers `x-ratelimit-limit-req-minute: 5`, `x-ratelimit-remaining-req-minute: 0`, and **no** `Retry-After`. Mistral support (second-hand) agreed `Retry-After` "should be present".
  - Web search results (fast.io, apistatuscheck.com, Mistral help center snippets): Mistral enforces requests per second, tokens per minute and tokens per month; limits are set at the workspace level and shown per model in the console; the free tier is 1 request per second and 500k tokens per minute. The paid-tier values are only visible in the logged-in console (https://admin.mistral.ai/plateforme/limits), which is where the operator read 0.5 requests per second for Mistral Large.
  - Mistral Python/TypeScript SDK READMEs: retry configuration is opt-in per call (`RetryConfig("backoff", BackoffStrategy(...))`); defaults "fall back to the default retry strategy provided by the API". No documented default for 429.
- **Findings**:
  - The 429 `type` field is not stable (`rate_limit_error` documented, `rate_limited` observed). Detection must use the status code alone (Req 1.1).
  - `Retry-After` is optional in practice; backoff is the required fallback (Req 1.3). `x-ratelimit-*` headers are informational and safe to record (Req 1.4).
  - A 429 is returned before inference; re-sending consumes no inference and is not a replay of inference, which keeps the project's "never replay inference" rule intact.
  - Tokens-per-minute limits cannot be predicted locally; proactive pacing covers only requests per second, so reactive handling must always stay on (Req 3.3).
- **Implications**: One classifier (status 429 → signal with optional Retry-After and header snapshot), one backoff policy, and configuration for the known per-second rate.

### Existing request paths
- **Context**: Find the seam where holding and re-sending fits without changing streaming or auth.
- **Sources Consulted**: `proxy.py` (`make_acquire` L246-L303, `adapter_post` L316-L362, lifespan L171-L234, CLI L489-L533), `mvibe_proxy/streaming.py` (`_OwnedResponse.__call__`), `mvibe_proxy/diagnostics.py`, `mvibe_proxy/observability.py` (`_ALLOWED_FIELDS`), `mvibe_proxy/compat.py` (`adapter_error`, `upstream_error_hint`), `packages/opencode-vibe-oidc/src/provider.ts`, `src/index.ts`, `src/models.ts`, `src/http-capture.ts`, `tests/test_proxy.py`, `tests/test_streaming.py`, `tests/fakes.py` (`Clock`), `test/provider.test.ts`.
- **Findings**:
  - Proxy: `make_acquire` builds the payload (including econ and metadata attribution), resolves a credential snapshot and calls `client.send(..., stream=True)`. `_OwnedResponse` awaits `acquire()` **before** starting its disconnect watcher, so today a client disconnect during acquisition is not observed. The watcher cannot start earlier today because `acquire()` reads the request body through `receive()`; Starlette caches the body once read, so reading it in the route handler first removes the conflict.
  - Proxy: `_OwnedResponse` forwards only `content-type` and `cache-control` on `http.response.start`; a forwarded 429 currently loses `Retry-After`.
  - Proxy: `adapter_post` checks `upstream.status_code >= 400` after `acquire()` and translates through `compat.adapter_error`, with no header passthrough.
  - Proxy: `create_app` already takes injectable factories (`auth_factory`, `client_factory`, `account_plan_factory`) and optional configs (`observe_config`, `econ_config`) validated in `main()` with fail-fast on explicit opt-in. `EconConfig.from_env` is the pattern for env parsing (`MVIBE_ECON_*`).
  - Proxy: `RequestDiagnostic` has fixed failure codes and an `upstream(status)` hint map with 429 → "Respect upstream rate_limit guidance before an explicit retry." The exporter allowlists field names by construction.
  - Plugin: `createProviderFetch` resolves a snapshot per fetch, materializes the chat body as a string when native metadata is attached, builds an outbound `Request` and calls `transport`. Non-chat bodies are passed through as a stream, which cannot be re-sent. `x-should-retry: false` is set on every response and `index.ts` sets `maxRetries = 0`.
  - Plugin: `models.ts` discovery uses the same fetch with a 30-second deadline signal, so any gate placed inside fetch automatically covers discovery, and abort handling covers the deadline (Req 4.5).
  - Tests: Python tests use `httpx2.MockTransport` with scripted responses and `tests/fakes.Clock` (fake `time` and `sleep`); provider tests use a `fixture()` with a scripted `response` and a `sent` list. Both are suitable for deterministic hold/pacing tests without real sleeps.
- **Implications**: The gate wraps "resolve + send" per attempt in both consumers. The proxy additionally needs (a) body read in the route handler, (b) the disconnect watcher started before acquisition, (c) `Retry-After` and `x-ratelimit-*` header forwarding, and (d) adapter error header passthrough. The plugin needs body bytes materialized once per fetch for re-sendability.

### Host behavior around slow responses
- **Context**: Holding a request for up to two minutes changes latency seen by consumers.
- **Sources Consulted**: `docs/opencode-provider.md` (no replay, `x-should-retry` note), `index.ts` (`maxRetries = 0`), proxy client `timeout=600`.
- **Findings**: The proxy's upstream client timeout is 600 seconds and streaming responses can already run for minutes, so a sub-two-minute hold before headers is within existing expectations. OpenCode shows no visible timeout for the provider fetch in the current host; the model-catalog path has its own 30-second deadline.
- **Implications**: Default maximum wait of 120 seconds is safe for the proxy; the plugin uses the same default, and discovery is bounded by its existing deadline.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Reactive only | Send immediately; on 429 hold and re-send | No configuration; adapts to tokens-per-minute limits | Every burst pays one rejected round trip per request; without `Retry-After` the wait is a guess | Must exist in any case (Req 1, 2, 4) |
| Proactive only | Space requests at a configured rate | Avoids most 429s at a known rate | Cannot cover tokens-per-minute or shared-workspace limits; wrong config is either wasteful or ineffective | Insufficient alone |
| Reactive + optional proactive (selected) | Always react to 429; pace when a rate is configured | Works unconfigured; operator can remove bursts with one setting | Two interacting waits must share one queue per model to stay fair | Chosen |
| Per-process gate (selected) vs shared cross-process gate | Each proxy/plugin process paces itself | No IPC, no shared state, no new dependency | Proxy and plugin running concurrently against one workspace can together exceed the rate; reactive handling absorbs it | Cross-process coordination is out of scope |
| Per-model keys (selected) vs one global key | Hold and pace per model id | Matches how the console lists limits; a hold on one model does not block another | If a limit is workspace-wide, two models can briefly double the rate; reactive hold corrects it | Discovery uses its own unpaced key |

## Design Decisions

### Decision: Gate keyed by model with FIFO and head re-entry
- **Context**: Requests for one model must stay ordered (Req 2.2, 3.1) while a rate-limited request waits.
- **Alternatives Considered**:
  1. Each request retries independently with its own backoff — bursts keep hammering the upstream and ordering is lost.
  2. One global queue — a hold on one model stalls unrelated models.
- **Selected Approach**: One gate per consumer process holding per-key state (`next_slot`, `hold_until`, FIFO waiters). A request that receives a 429 sets the key's `hold_until`, re-enters at the head of its key's queue and waits; later arrivals wait behind it. Pacing advances `next_slot` by `1 / rate` at each send for paced keys.
- **Rationale**: Preserves arrival order, lets one rejection pace the whole burst, and isolates models.
- **Trade-offs**: Slightly more state than a global lock; the bound on held requests is counted across all keys so memory stays bounded by one number.
- **Follow-up**: Verify fairness and head re-entry in unit tests with a fake clock.

### Decision: Exhaustion returns a 429 response instead of raising
- **Context**: Both the raw passthrough and the adapter routes already handle an upstream 429 response object; the plugin returns upstream responses as-is.
- **Alternatives Considered**: Raise a dedicated exception and build error responses in each route and in the plugin.
- **Selected Approach**: The gate's `run` returns the last upstream 429 (with the body materialized up to 64 KiB in the proxy so the stream can be closed between attempts; unread in the plugin because the final response is handed straight to the host) with `Retry-After` ensured, or synthesizes a 429 JSON response when no upstream attempt was made (queue full or deadline reached while pacing). Existing status handling downstream stays unchanged.
- **Rationale**: Minimal route changes; byte-exact forwarding of the last upstream body (Req 2.5); the plugin keeps the host's error presentation (Req 4.3).
- **Trade-offs**: A synthesized 429 body is proxy- or plugin-authored; it uses the OpenAI error shape on `/v1`, dialect shape via `compat.adapter_error` on adapters.

### Decision: Fresh credential snapshot per attempt, attribution on the first attempt
- **Context**: Snapshots expire (60 seconds for existing-vibe); a held request may outlive one. Metadata attribution (`account_plan.resolve(snapshot)`) needs the snapshot, so it cannot run before the first resolve.
- **Selected Approach**: `resolve` is inside the attempt callable in both consumers. The body is parsed once up front (key, diagnostic model); attribution and econ run inside the first attempt with that attempt's snapshot and the resulting bytes are cached in the closure; later attempts send the cached bytes with fresh headers.
- **Rationale**: Req 2.8, 4.6; econ and attribution still run exactly once; no auth code changes.

### Decision: Observe client disconnect during the hold (proxy)
- **Context**: Today `_OwnedResponse` starts its disconnect watcher after acquisition; a held request would otherwise be sent after the client has gone.
- **Selected Approach**: Route handlers read the body before handing the request to the stream owner; `_OwnedResponse` starts the disconnect watcher before awaiting `acquire()`; a disconnect cancels the acquisition scope, the gate releases the queue position, and the diagnostic records `cancelled`. Adapter routes, which await the upstream response in the handler before choosing a transform, use a new `ProxyStreamOwner.acquire_watching` helper with the same watcher, because the ASGI server does not cancel a handler on client disconnect.
- **Trade-offs**: Touches the carefully tested streaming owner; covered by extending `tests/test_streaming.py`.

### Decision: Configuration surface
- **Proxy**: environment variables in the existing `MVIBE_*` style: `MVIBE_RATE_LIMIT_RPS` (default rate, unset = no pacing), `MVIBE_RATE_LIMIT_MODEL_RPS` (`model=rps[,model=rps...]`), `MVIBE_RATE_LIMIT_MAX_WAIT_S` (120), `MVIBE_RATE_LIMIT_MAX_QUEUE` (64), `MVIBE_RATE_LIMIT_BACKOFF_CAP_S` (30). Invalid values fail `main()` fast with the setting name (Req 3.4), following the `EconConfig` pattern. No CLI flag: reactive handling is always on and needs no opt-in.
- **Plugin**: `options.rateLimit` object `{ requestsPerSecond?, modelRequestsPerSecond?, maxWaitMs?, maxQueue?, backoffCapMs? }`; invalid values throw the labeled `setup` BridgeError.
- **Rationale**: Never hardcode the operator's 0.5 value (steering: account-specific facts are not defaults); the operator sets it explicitly.

### Decision: Build, not adopt
- Python `tenacity`/`aiolimiter` and TypeScript `p-queue`/`bottleneck` were considered. Rejected: new dependencies for roughly 150 lines of logic each, none of them abort-aware in the exact way the stream owner and `AbortSignal` paths require, and the lockfiles (`uv.lock`, `bun.lock`) are deliberately conservative.

### Synthesis outcomes
- **Generalization**: Pacing and 429 hold are the same primitive, "earliest allowed send time per key"; one gate implements both.
- **Simplification**: No circuit breaker, no persistent queue, no cross-process coordination, no token-per-minute estimation.

## Risks & Mitigations
- Mistral omits `Retry-After` — backoff fallback with 30-second cap; `x-ratelimit-*` recorded for tuning.
- Workspace-wide limit while pacing per model — reactive hold corrects within one rejection; operator can set the default rate lower.
- Long holds hit consumer timeouts — defaults bounded (120 seconds), configurable; exhaustion returns a proper 429 with `Retry-After`.
- Starting the disconnect watcher earlier regresses streaming ownership — body is read in the route first; streaming tests extended for disconnect-during-hold.
- Re-sending a non-chat body in the plugin — bodies are materialized once per fetch as bytes before the first attempt.
- Proxy and plugin both active against one workspace — documented limitation; reactive handling absorbs the overlap.

## References
- [Mistral error glossary](https://docs.mistral.ai/resources/error-glossary) — error body shape, 429 listed, backoff recommendation.
- [pi issue 9815](https://github.com/earendil-works/pi/issues/9815) — observed 429 body and `x-ratelimit-*` headers without `Retry-After`.
- [Mistral workspace limits console](https://admin.mistral.ai/plateforme/limits) — operator-reported 0.5 requests per second for Mistral Large (login required).
- [RFC 9110 §10.2.3 Retry-After](https://www.rfc-editor.org/rfc/rfc9110#name-retry-after) — delay-seconds and HTTP-date forms.
- `.kiro/steering/authentication.md` — no replay of inference, ordinary diagnostics carry no content.
