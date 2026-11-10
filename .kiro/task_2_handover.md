# Handover — upstream-rate-limit-queue, task group 2 complete (2026-11-10)

## Status
- Group 2 (proxy integration) is done and committed; the rate-limit gate is live on every proxy route:
  - `17ba0b0` 2.1 disconnect-aware acquisition (`acquire_watching`, `ClientDisconnected`, 429-only header forwarding, route body reads via `proxy._read_body`)
  - `657dfc1` 2.2 `GateRecord` in `mvibe_proxy/diagnostics.py`, `rate_limited` outcome, export allowlist
  - `bff7a8e` 2.3 configuration wiring (`create_app(rate_limit_config=, gate_factory=)`, `app.state.rate_gate`, `main()` exit 2)
  - `b56d0e3` 2.4 `make_acquire` gate loop (parse once, `key_for`, fresh snapshot per attempt, attribution and econ once, byte-identical re-sends)
  - `c6b7b29` 2.5 adapter routes through `acquire_watching`, `retry-after`/`x-ratelimit-*` on translated 429s
  - `75bda10` 2.6 gate deadline edge (unclamped exhaustion decision), `perf_counter` clock, raw-byte 429 headers, `%r` warn key, Req 2.2 end-to-end test
- Evidence: full suite 1738 passed / 6 skipped; ruff clean. Every task had an independent Opus-xhigh review; 2.1 needed a controller follow-up (route-level `ClientDisconnect` handling), 2.4's review found the deadline edge that became 2.6.
- Toolchain commits alongside: `73d2a5b` (Podman docker/compose shims + native Dagger in steering and docs/ci.md; `ci.py check` passed natively), `7c75b6f` (stats spec notes).

## Behavior now (proxy)
- Every `/v1/*`, `/api/*` and `/v1/messages` request: body read first, keyed by sanitized model (`models` never paced), held/re-sent on 429 with a fresh credential snapshot per attempt, exhausted 429 forwarded with its body, `retry-after` and `x-ratelimit-*`; client disconnects during a hold cancel without an upstream send.
- Configuration: `MVIBE_RATE_LIMIT_RPS`, `MVIBE_RATE_LIMIT_MODEL_RPS`, `MVIBE_RATE_LIMIT_MAX_WAIT_S` (120), `MVIBE_RATE_LIMIT_MAX_QUEUE` (64), `MVIBE_RATE_LIMIT_BACKOFF_CAP_S` (30); invalid values exit 2. Diagnostics carry `queued_ms`, `rate_limit_rejections` (exported) and `rate_limit_headers` (stdout only); outcome `rate_limited` when the gate gave up.

## Learnings (also in tasks.md Implementation Notes)
- A zero-latency 429 whose Retry-After was clamped to `max_wait_s` used to re-send exactly at the deadline; fixed in 2.6 by deciding exhaustion on the unclamped wait. Windows `time.monotonic` has a 15.6 ms tick on Python 3.12, hence `perf_counter`.
- Always-429 test fakes must use `clocked_gate`/`SHORT_WAIT` (`max_wait_s=0.5`) so they exhaust immediately; `UPSTREAM_LATENCY` is gone.
- `tests/conftest.py::isolate_environment` now strips `MVIBE_RATE_LIMIT_*`.

## Next group (3: plugin rate-limit module `packages/opencode-vibe-oidc/src/rate-limit.ts`)
- 3.1 (P) options parsing + `classifyRateLimit`/`parseRetryAfter`; 3.2 TypeScript `UpstreamGate` scheduling core; 3.3 429 hold handling. Then group 4 (provider fetch attempt loop, gate wiring, `index.ts` options) and task 5 (validation; the native Dagger `ci.py check` can now be part of it).
- Plugin tooling: Bun 1.4.4 at `C:\Users\mariu\.bun\bin\bun.exe` (not on PATH); baseline `bun test` 1089 pass, lint/typecheck/build clean. Mirror the Python decisions: status-only 429 detection, strictly positive fallback into `classify`, jitter after classification, floor at the pacing interval, strict deadline, unclamped exhaustion decision, exhaustion returns the last 429 unread.
- Shared subagent context: `D:\BuildArtifacts\Temp\claude\ecc7ddb1bcf904de\38a50ca4-64d0-4346-a3a3-283c7eb0be03\scratchpad\impl-common-context.md`.

The controller continues without pausing (user unattended); resume anytime with `$kiro-impl upstream-rate-limit-queue`.
