# Proxy and provider observability

Opt-in, safe diagnostics for the local proxy and the direct OpenCode provider,
with export to an **OpenObserve-style** backend. Nothing is emitted or exported
unless you enable it; no credentials, prompts, bodies, headers or URLs are ever
included — exported records are allowlisted by construction.

## What you get

- Every proxied request gets a correlation ID, returned as `X-Request-ID` and
  included in the event (`request_id`).
- One structured event per request: `event, timestamp, request_id, trace_id?,
  span_id?, phase, outcome, status?, elapsed_ms?, model?, hint`. Streams report
  distinct outcomes (`completed`, `stream_interrupted`, `cancelled`,
  `cleanup_failed`). Hints are fixed, curated strings.
- The direct provider plugin (`diagnostics: true` in its options) emits the same
  field names on **stderr** (never stdout, which carries the plugin/MCP
  protocol), so both paths correlate in one place.

## Local stdout (no backend)

```powershell
./start_proxy.ps1 --log
# or: uv run --locked --extra proxy -- python proxy.py --log
```

JSON lines on stdout, default off.

## OpenObserve export (proxy)

Enable with env vars (never write the key into files) plus the flag:

```powershell
$env:MVIBE_OBSERVE_URL      = 'http://10.0.0.54:40002'   # OpenObserve base URL
$env:MVIBE_OBSERVE_ORG      = 'default'                  # optional, default 'default'
$env:MVIBE_OBSERVE_STREAM   = 'mvibe'                    # optional, default 'mvibe' (set 'python' to reuse an existing stream)
$env:MVIBE_OBSERVE_USER     = 'root@example.com'
$env:MVIBE_OBSERVE_TOKEN    = '<access key>'
./start_proxy.ps1 --observe
```

Two ingest paths are used:

1. **Logs (built in, no extra dependency):** batched `POST {MVIBE_OBSERVE_URL}/api/{org}/{stream}/_json`
   with `Authorization: Basic base64(user:token)` — e.g.
   `http://10.0.0.54:40002/api/default/mvibe/_json`. Records land in the
   `mvibe` stream; search with `_search` on `stream = 'mvibe'`.
2. **Traces/metrics (optional `otel` extra):** install with
   `uv sync --locked --extra otel`, then standard OTel env:

   ```powershell
   $env:OTEL_EXPORTER_OTLP_ENDPOINT = "$env:MVIBE_OBSERVE_URL/api/$env:MVIBE_OBSERVE_ORG"
   $env:OTEL_EXPORTER_OTLP_HEADERS  = "Authorization=Basic <base64(user:token)>,stream-name=mvibe"
   ```

   Spans: `proxy.request`, `proxy.stream`, `auth.resolve`, `bridge.call`;
   counters: requests, stream outcomes, export failures, dropped events.
   When active, the proxy also sends W3C `traceparent` upstream (approved
   proxy→Mistral hop only).

Alternative topology: run an OpenTelemetry Collector with an
`otlphttp/openobserve` exporter (`endpoint: {url}/api/{org}`, `Authorization:
Basic …`, `stream-name: mvibe`) and have the proxy/colletor ship to it — the
proxy needs no direct export then, just `--log` stdout.

Failure safety: export is queued and batched off the request path (queue cap
1000, drop-oldest counted; 5s per-POST timeout; 2s bounded flush on shutdown).
A down backend never blocks or fails inference; failures are counted.

## Verifying

- Offline: `uv run --locked --offline -- pytest tests/test_observability.py -q`
  (add `--extra otel` to also run the span tests).
- Manual against your instance (placeholders only):

  ```bash
  curl -u "$MVIBE_OBSERVE_USER:$MVIBE_OBSERVE_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '[{"event":"smoke","request_id":"manual-1"}]' \
    "$MVIBE_OBSERVE_URL/api/$MVIBE_OBSERVE_ORG/mvibe/_json"
  ```

## When the OpenCode host updates

OpenCode auto-updates; record the observed version. After an update, re-run the
provider diagnostics tests (`bun test test/diagnostics.test.ts` in
`packages/opencode-vibe-oidc`) and one proxied request with `--log --observe`
to confirm the event shape still matches this document. If the plugin protocol
or SDK changes the diagnostic channel, update this spec
(`.kiro/specs/proxy-observability`) — its revalidation triggers list the
affected contracts.

## Privacy contract

Allowlisted fields only. Tokens, cookies, authorization headers, sign-in URLs,
request/response bodies, prompts, tool arguments/results and raw exception
messages never appear in logs or exports. The OpenObserve access key is read
from the environment at runtime only; if it ever leaks (chat, issue, log),
rotate it in OpenObserve immediately.
