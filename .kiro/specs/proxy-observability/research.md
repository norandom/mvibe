# Research & Design Decisions — proxy-observability

## Summary

- **Feature**: proxy-observability (extension of the existing opt-in diagnostics baseline)
- **Discovery Scope**: Extension (light discovery) + targeted external API verification
- **Key Findings**:
  - Existing baseline: `mvibe_proxy/diagnostics.py` (opt-in `--log` structlog JSON stdout events, `X-Request-ID` response header, fixed codes/hints) and plugin-side `diagnostics: true` stderr JSON events in `packages/opencode-vibe-oidc/src/provider.ts`. No OTel, no exporter, no metrics.
  - User direction (2026-11-02): integrate **OpenObserve-style** — OTLP/HTTP to `{base}/api/{org}` with `Authorization: Basic …` and `stream-name: mvibe` headers, plus native logs ingest `POST {base}/api/{org}/mvibe/_json` (JSON array batch). Stream name is `mvibe`.
  - OpenObserve verified (openobserve.ai docs, 2026-11-02): logs `_json` batch endpoint, metrics `/api/{org}/ingest/metrics/_json`, OTLP at `/api/{org}/v1/{traces,logs,metrics}`; all APIs use Basic auth `base64(user:token)`. OTel Python exporter releases are at 1.45.1 (2026-10); SDK tracks the same 1.45.x line.

## Research Log

### Gap: what exists vs what the requirements need

- **Context**: Requirements 1–6 (approved 2026-11-02) vs current code.
- **Sources Consulted**: `mvibe_proxy/diagnostics.py`, `mvibe_proxy/streaming.py`, `proxy.py`, `packages/opencode-vibe-oidc/src/provider.ts`, `src/index.ts`, steering `authentication.md`/`tech.md`.
- **Findings**:
  - Req 1 (correlation + lifecycle events): **mostly present** on the proxy (`request_id`, phase/outcome/status/elapsed/hint, stream outcomes cancelled/interrupted/cleanup). Missing: stream start/completion granularity is outcome-only; no trace IDs.
  - Req 2 (safe content): **present** (fixed codes/hints, body never read by diagnostics, emit() swallows errors). Must be extended to any new exporter (allowlist fields).
  - Req 3 (opt-in, inert default): **present** (`--log` default off; plugin `diagnostics: true`).
  - Req 4 (plugin parity): **partial** — plugin emits JSON to stderr with phase/outcome/status/hint; missing request_id and model fields for correlation with proxy/OpenObserve.
  - Req 5 (export): **missing** — no exporter, no OTel, no W3C propagation. This is the main gap.
  - Req 6 (operator docs + offline tests): **missing** — no observability doc; tests cover `--log` only.
- **Implications**: Extend existing modules rather than replace them; new code concentrates in one new proxy module plus small edits.

### OpenObserve ingest API (external verification)

- **Context**: User asked for OpenObserve-style integrations and supplied a collector snippet (`otlphttp/openobserve` exporter: `endpoint: {base}/api/default`, `Authorization: Basic …`, `stream-name: default`).
- **Sources Consulted**: https://openobserve.ai/docs/reference/api/ (ingestion/logs, ingestion/logs/json, ingestion/logs/otlp, ingestion/metrics/json); PyPI release pages for `opentelemetry-exporter-otlp-proto-http` (1.45.1, 2026-10-06).
- **Findings**:
  - Native logs: `POST {base}/api/{org}/{stream}/_json`, JSON array body, `Content-Type: application/json`, Basic auth; ≤200 fields per record. With stream `mvibe`: `{base}/api/{org}/mvibe/_json`.
  - OTLP/HTTP: `POST {base}/api/{org}/v1/traces|logs|metrics`; standard `OTEL_EXPORTER_OTLP_ENDPOINT`/`OTEL_EXPORTER_OTLP_HEADERS` work, with `stream-name: mvibe` header to pin the destination stream (per user's collector snippet).
  - OTel Python SDK/exporter 1.45.x is current (Oct 2026); Apache-2.0 licensed.
- **Implications**: Traces/metrics need no custom wire code (standard OTLP env against OpenObserve). The structured log events get a small custom `_json` exporter using the already-present `httpx`, keeping the default dependency footprint unchanged (OTel SDK only in an optional extra).

### Credential handling in config

- **Context**: User's example contained a live Basic-auth credential in chat. Steering forbids credentials in project memory/logs.
- **Findings/Implications**: All OpenObserve config comes from env vars (`MVIBE_OBSERVE_*`, `OTEL_EXPORTER_OTLP_*`) read at runtime; tokens never logged, never in files, never in tests (tests use synthetic tokens). Docs use placeholders. (Advised rotating the credential that appeared in chat.)

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|---|---|---|---|---|
| A: Extend existing diagnostics, add one new exporter module | Keep `diagnostics.py`/`streaming.py`, add `mvibe_proxy/observability.py` | Minimal churn, reuses tested safe-content rules | Exporter must stay strictly opt-in | **Selected** |
| B: Adopt full OTel SDK in core deps | opentelemetry-sdk as a hard dependency | Uniform telemetry | Heavy default footprint, against steering "library now, traces later" spirit | Rejected for core; kept as optional `otel` extra |
| C: External collector only (user's collector snippet) | Proxy emits stdout JSON; collector tails it | Zero proxy code | No in-proxy correlation, fragile file tailing | Rejected as primary; collector config documented as an alternative |

## Design Decisions

### Decision: OpenObserve as the reference backend, two ingest paths
- **Context**: Req 5 (opt-in export) + user direction "OpenObserve style".
- **Alternatives Considered**: generic OTLP-only; collector-tailing; custom-only.
- **Selected Approach**: (1) custom native logs exporter → `{MVIBE_OBSERVE_URL}/api/{MVIBE_OBSERVE_ORG}/mvibe/_json` (stream name `mvibe`); (2) optional `otel` extra: OTel SDK with standard `OTEL_EXPORTER_OTLP_ENDPOINT={url}/api/{org}` + `OTEL_EXPORTER_OTLP_HEADERS` (`Authorization=Basic …`, `stream-name=mvibe`) → `/v1/traces`, `/v1/metrics`.
- **Rationale**: matches the user's existing collector topology, keeps default deps unchanged, both paths opt-in and inert by default.
- **Trade-offs**: two config surfaces (MVIBE_OBSERVE_* and OTEL_*) — documented in one operator guide; acceptable because they map to logs vs traces/metrics.
- **Follow-up**: confirm exact `opentelemetry-sdk` pin at lock time (exporter verified at 1.45.1).

### Decision: field allowlist for exported events
- **Selected**: exported records contain only `{event, timestamp, request_id, trace_id?, span_id?, phase, outcome, status?, elapsed_ms?, model?, hint}` + static resource attrs. Everything else is dropped by construction, not by redaction.
- **Rationale**: Req 2 is structural, not best-effort.

### Decision: W3C trace context only on the proxy→upstream hop
- **Selected**: when OTel is enabled, the proxy injects `traceparent`/`tracestate` into upstream request headers; the TS plugin gains no OTel dependency and correlates via its own `request_id` in stderr events.
- **Rationale**: Req 5.2 (approved boundaries only); bridge protocol unchanged (no version bump).

## Risks & Mitigations

- **Risk**: exporter blocks the request path on a slow backend → **Mitigation**: enqueue-only bounded queue (cap 1000, drop-oldest with `dropped_events` counter), background drain task, hard timeouts, flush-on-shutdown bounded by 2s; all failures counted, never raised into inference.
- **Risk**: secret leakage via config → **Mitigation**: tokens read from env only; allowlist; tests assert token absent from payloads; docs use placeholders.
- **Risk**: OTel SDK import failure in minimal installs → **Mitigation**: optional extra; ImportError → inert with a startup note, Req 3 preserved.
- **Risk**: stream outcome granularity insufficient → **Mitigation**: stream lifecycle events (started/completed/aborted/cancelled/cleanup_failed) added to the same event schema.

## References

- OpenObserve API reference — https://openobserve.ai/docs/reference/api/ (logs `_json`, OTLP `/v1/*`, metrics `_json`)
- OpenObserve blog: monitoring FastAPI with OpenTelemetry + OpenObserve — https://openobserve.ai/blog/monitoring-fastapi-application-using-opentelemetry-and-openobserve/
- OpenObserve blog: handling errors with OpenTelemetry Python — https://openobserve.ai/blog/handling-errors-with-opentelemetry-python/
- OTLP Python exporters — https://opentelemetry.io/docs/languages/python/exporters/ ; PyPI `opentelemetry-exporter-otlp-proto-http` 1.45.1 (2026-10)
- W3C Trace Context — https://www.w3.org/TR/trace-context/
- Internal: steering `authentication.md` (diagnostic privacy rules), `tech.md`; `mvibe_proxy/diagnostics.py`

### Endpoint note (2026-11-02)

The operator's OpenObserve instance exposes native ingest at
`{base}/api/{org}/{stream}/_json` (observed: `/api/default/python/_json`), so org
and stream are URL path segments and fully configurable. This spec's default
stream is `mvibe` (user instruction 2026-11-02); set `MVIBE_OBSERVE_STREAM` to
match an existing stream (e.g. `python`) without code changes. Access keys are
Basic-auth base64(user:token) values supplied via env vars only — the live key
that appeared in chat on 2026-11-02 was deliberately not recorded anywhere;
rotate it.
