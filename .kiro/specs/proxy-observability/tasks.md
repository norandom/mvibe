# Implementation Plan — proxy-observability

## 1. Foundation: optional OTel dependency extra

- [x] 1.1 Add optional extra `otel` to `pyproject.toml` pinning `opentelemetry-sdk==1.45.1` and `opentelemetry-exporter-otlp-proto-http==1.45.1`; run `uv lock` and confirm `uv lock --check` passes
  - Verify the exact SDK pin exists on PyPI at lock time (exporter 1.45.1 confirmed 2026-10); adjust the pin together with the lock if the SDK line differs, keeping both at the same 1.45.x release
  - Observable completion: `uv lock --check` exits 0 and `uv run --locked --offline --extra otel -- python -c "import opentelemetry.sdk"` succeeds
  - _Requirements: 5.1, 5.3_

## 2. Core: proxy-side observability

- [ ] 2. New `mvibe_proxy/observability.py` owning all export concerns
- [x] 2.1 (P) OpenObserve-style logs exporter
  - Config from env only: `MVIBE_OBSERVE_URL`, `MVIBE_OBSERVE_ORG` (default `default`), `MVIBE_OBSERVE_STREAM` (default `mvibe`), `MVIBE_OBSERVE_USER`, `MVIBE_OBSERVE_TOKEN`; exporter constructs Basic auth header `base64(user:token)` and never includes the token in any payload
  - Bounded queue (cap 1000, drop-oldest, `dropped_events` counter); background drain task batching ≤100 records or 1s, whichever first; POST `{url}/api/{org}/{stream}/_json` with `Content-Type: application/json`, JSON array body, 5s timeout; failures counted (`export_failures`), never raised into the request path; `shutdown()` flushes with a 2s bound
  - Allowlist record builder: only `event, timestamp, request_id, trace_id?, span_id?, phase, outcome, status?, elapsed_ms?, model?, hint` plus resource attrs `service.name=mvibe-proxy`, `service.version`; any other field is dropped by construction
  - Observable completion: unit tests with a mock httpx transport show correct URL path `/api/default/mvibe/_json`, Basic auth header, JSON array body, token absent from body, queue-bound drops counted, shutdown flush within bound
  - _Boundary: OpenObserveExporter_
  - _Requirements: 5.3, 5.4, 2.1, 2.2, 1.5_
- [x] 2.2 Optional OTel bootstrap in the same module (`observability.py`, after 2.1 — same file, so not parallel)
  - Import-guarded: if the `otel` extra is not installed, export stays inert with a startup note; `TracerProvider`/`MeterProvider` created only when enabled
  - Spans: `proxy.request`, `proxy.stream`, `auth.resolve` (in `mvibe_auth` request resolution), and `bridge.call` (in `mvibe_auth/bridge.py` via existing hooks, no protocol version change); counters: requests, stream outcomes, export_failures, dropped_events
  - Exporter configured purely by standard env (`OTEL_EXPORTER_OTLP_ENDPOINT`/`OTEL_EXPORTER_OTLP_HEADERS`); helper injects W3C `traceparent`/`tracestate` into upstream request headers on the proxy→upstream hop only, and never invents trace IDs
  - Observable completion: with an in-memory span exporter (offline tests), a proxied request produces a `proxy.request` span carrying `request_id`, `auth.resolve`/`bridge.call` spans appear on their paths, and the upstream request has `traceparent` only when enabled
  - _Depends: 2.1_
  - _Boundary: OTel bootstrap_
  - _Requirements: 5.1, 5.2, 1.5_
- [x] 2.3 (P) Extend `mvibe_proxy/diagnostics.py` event schema
  - `RequestDiagnostic` gains optional `trace_id`/`span_id`/`model`; `emit()` keeps the existing `--log` stdout JSON behavior byte-compatible and additionally enqueues the allowlisted record to the exporter interface from 2.1 when enabled
  - Fixed failure-code map (`auth`, `connection`, `unsupported`, `interrupted`, `cancelled`, `cleanup`) and curated upstream-status hints unchanged; `emit()` still swallows all errors
  - Observable completion: existing `tests/test_proxy_diagnostics.py` passes unchanged; new tests show the extra fields present only when set and the enqueue call only when the exporter is enabled
  - _Depends: 2.1_
  - _Boundary: RequestDiagnostic_
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 2.1, 2.2, 2.3_
- [x] 2.4 (P) Stream lifecycle outcomes in `mvibe_proxy/streaming.py`
  - Distinct outcomes for stream start, completion, abort, cancellation and cleanup failure, all flowing through the 2.3 event schema and the 2.1 exporter path
  - Observable completion: new tests drive `_OwnedResponse` through completion, client disconnect and cleanup failure and assert the three distinct `outcome` values in emitted events
  - _Depends: 2.1, 2.3_
  - _Boundary: ProxyStreamOwner_
  - _Requirements: 1.3_

## 3. Integration and wiring

- [x] 3.1 Wire the proxy CLI and lifespan in `proxy.py`
  - New `--observe` flag (plus env-only fallback when `MVIBE_OBSERVE_URL` is set); invalid/incomplete config (URL set but user/token missing) fails fast at startup with a clear message and leaves export disabled
  - Lifespan starts the exporter drain task and OTel providers when enabled, and flushes both with bounded timeouts on shutdown; `--log`, `--host`, `--port`, `--auth-source`, `--login` behavior unchanged
  - Observable completion: `proxy.py --help` shows `--observe`; CLI tests show default inert (no exporter, no network); lifespan test shows flush on shutdown
  - _Depends: 2.1, 2.2, 2.3, 2.4_
  - _Boundary: proxy CLI/lifespan_
  - _Requirements: 3.1, 3.2, 3.4, 5.3, 5.4_
- [x] 3.2 (P) Direct-plugin diagnostic parity in `packages/opencode-vibe-oidc/src/provider.ts`
  - When `options.diagnostics === true`, the stderr JSON line gains `request_id` (generated per request) and `model` fields, matching the proxy event field names; still stderr-only, still try/catch-guarded, still no bodies/headers/URLs; no new TS dependencies
  - Observable completion: Bun test asserts the stderr line parses as JSON with `request_id`/`model`/`outcome` when enabled and that nothing is printed when disabled
  - _Boundary: Plugin diagnostics (TypeScript)_
  - _Requirements: 4.1, 4.2, 4.3, 3.3_

## 4. Validation

- [x] 4.1 New offline test suite `tests/test_observability.py`
  - Covers: allowlist enforcement (extra fields dropped), `_json` batching/auth-header/token-absence, queue bound + drop counter, shutdown flush bound, disabled-by-default inertness (no exporter task, no sockets), OTel in-memory spans + traceparent only when enabled + ImportError-inert path, end-to-end ASGI request producing one exported record whose `request_id` matches `X-Request-ID`
  - All offline: mock httpx transport and in-memory OTel exporter; conftest socket/keyring guards remain green
  - Observable completion: `uv run --locked --offline -- pytest tests/test_observability.py tests/test_proxy.py tests/test_proxy_diagnostics.py -q` passes without the extra (OTel tests skip cleanly as inert), and the same run with `--extra otel` passes with the span/traceparent assertions active
  - _Depends: 2.1, 2.2, 2.3, 2.4, 3.1_
  - _Requirements: 6.2, 5.4, 2.1, 2.2, 1.1, 1.3, 1.5_
- [x] 4.2 (P) Plugin diagnostics Bun test in `packages/opencode-vibe-oidc/test/provider.test.ts`
  - Asserts stderr JSON shape with `request_id`/`model` when `diagnostics: true`, silence when off, and that a failing stderr write never throws
  - Observable completion: `bun test test/provider.test.ts` passes
  - _Depends: 3.2_
  - _Boundary: Plugin diagnostics (TypeScript)_
  - _Requirements: 6.2, 4.1, 4.2, 4.3_
- [x] 4.3 Operator guide `docs/observability.md` (intentional acceptance coverage for Req 6.1/6.3, not marketing docs)
  - OpenObserve setup: env vars only (placeholders, never real keys), default stream `mvibe` (`MVIBE_OBSERVE_STREAM` override, e.g. an existing `python` stream), native `_json` URL shape `/api/{org}/{stream}/_json`, OTLP via standard `OTEL_EXPORTER_OTLP_*` with `stream-name: mvibe` header, collector `otlphttp/openobserve` alternative, example `curl` ingest + stream search, re-check guidance when the OpenCode host version changes
  - Observable completion: doc renders, contains no credential material, and its commands were exercised against the local instance shape during development
  - _Depends: 3.1_
  - _Requirements: 6.1, 6.3_
- [x] 4.4 Full offline regression gate
  - Ruff on changed Python files, full `pytest` suite, Bun provider tests + typecheck + build, and `scripts/verify.ps1` end-to-end
  - Observable completion: verify.ps1 all mandatory steps pass and Python/Bun suites green; acceptance.json written under reports/verification
  - _Depends: 4.1, 4.2, 4.3_
  - _Requirements: 6.2_
