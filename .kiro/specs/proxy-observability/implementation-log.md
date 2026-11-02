# Implementation log — proxy-observability (2026-11-02)

Implemented per tasks.md after requirements (approved) and design (approved)
were generated the same day. All work stayed offline; the OpenObserve backend
was verified by API contract, not by sending real data.

| Task | What landed | Evidence |
| --- | --- | --- |
| 1.1 | `pyproject.toml` optional extra `otel = ["opentelemetry-sdk==1.45.1", "opentelemetry-exporter-otlp-proto-http==1.45.1"]`; `uv.lock` updated | `uv lock --check` exit 0; import check with `--extra otel` prints sdk/exporter 1.45.1 |
| 2.1 | `mvibe_proxy/observability.py`: `ObserveConfig` (env-only, `MVIBE_OBSERVE_URL/ORG/STREAM/USER/TOKEN`, defaults `default`/`mvibe`), `OpenObserveExporter` (bounded queue 1000 drop-oldest, batch ≤100/1s, POST `/api/{org}/{stream}/_json` with Basic auth, 5s timeout, counted failures, 2s bounded shutdown flush), `allowlisted()` field filter | `tests/test_observability.py`: batching/auth-header/token-absence, failure counting, queue bound, shutdown flush — 17 passed |
| 2.2 | Same module: `start_telemetry()` (import-guarded, inert without the extra), `Telemetry` handle with counters, `current_trace_ids()`, `inject_traceparent()` (W3C, proxy→upstream hop only). `mvibe_auth/bridge.py`: optional `bridge.call` span via `_trace_span` (nullcontext without otel) | span tests with in-memory exporter; inert test blocks opentelemetry imports and asserts None/no-op |
| 2.3 | `mvibe_proxy/diagnostics.py`: optional `trace_id`/`span_id`/`model` on `RequestDiagnostic`, `configure_exporter`/`clear_exporter`, `emit()` enqueues the allowlisted record when an exporter is attached; stdout `--log` format unchanged; `ProxyDiagnostics` wraps requests in a `proxy.request` span when telemetry is active | existing `test_proxy_diagnostics.py` green unchanged; new correlated-export test asserts `request_id` == `X-Request-ID`, model extracted, no secrets |
| 2.4 | `mvibe_proxy/streaming.py`: stream phase marked on response start; `proxy.stream` span around body streaming when telemetry active | `test_streaming.py` + diagnostics tests green; otel span test asserts `proxy.stream` |
| 3.1 | `proxy.py`: `--observe` flag; env config with fail-fast on explicit `--observe` + incomplete env, warning-only when only env is set; lifespan starts/stops exporter and telemetry; `auth.resolve` span; `traceparent` injected upstream; model id extracted for chat | CLI tests: `--observe` without env exits 2; incomplete env warns and stays inert; full config passed through |
| 3.2 | `packages/opencode-vibe-oidc/src/provider.ts`: stderr diagnostic gains `model` (allowlisted model id only, never message content); field names match the proxy schema | `test/diagnostics.test.ts`: 31 pass incl. new parity test (request_id UUID, model null off chat, exact key set); leak canaries unchanged |
| 4.1 | `tests/test_observability.py` — 17 offline tests (allowlist, config, exporter, CLI gating, disabled-default inertness, OTel spans/traceparent/bridge span, inert-when-missing) | pass with and without `--extra otel` |
| 4.2 | Plugin Bun tests updated/added in `test/diagnostics.test.ts` | `bun test` 31 pass, 0 fail; `tsc --noEmit` clean; eslint clean |
| 4.3 | `docs/observability.md` operator guide: env-only config, stream `mvibe` (override e.g. `python`), `_json` URL shape, OTLP via `OTEL_EXPORTER_OTLP_*` with `stream-name: mvibe`, collector alternative, smoke curl, host-update re-check, privacy contract | placeholders only; no credentials recorded |
| 4.4 | Full offline gate `scripts/verify.ps1` | see acceptance.json run id in reports/verification (2026-11-02) |

Notes:

- The live OpenObserve access key shared in chat on 2026-11-02 was never
  written to any file; config is env-only. User confirmed LAN-only dev use.
- OTel Python 1.45 moved the trace-context propagator to
  `opentelemetry.trace.propagation.tracecontext` (fixed in
  `observability.py`).
- Global tracer-provider override is process-wide, so tests use local
  providers + in-memory exporters and never set the global provider.
