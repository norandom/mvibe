# Implementation Log — proxy-econ-client

## 2026-11-06 — Task 4.3: Performance check of the request-path rewrite

### Environment (reference workstation)
- CPU: AMD Ryzen AI 9 HX 370 (AMD64 Family 26 Model 36 Stepping 0, GenuineAMD), 24 hardware threads
- OS: Windows 11 Pro (10.0.26200)
- Python: CPython 3.12.10 (`uv run --locked --offline`), pytest 8.x, run single-worker (`-n 0`) so no second xdist worker competes for CPU
- Offline, in-process only: ASGITransport + MockTransport upstream; the mmhmem child is the injected-stream `FakeMmhmem` (no subprocess, no network — Req 6.1 discipline extends to benchmarks)

### Methodology
Benchmark body: one synthetic OpenAI chat-completions body whose **eligible content is exactly 250 KiB** — 40 distinct historical `read` results of exactly 6400 bytes each (40 × 6400 = 256000), plus an ineligible trailing group; raw body ≈ 260 KB. Shape is pinned by an always-run test (`tests/test_econ_perf.py::test_perf_body_shape_40_eligible_results_over_250_kib`).

Three measurements (`tests/test_econ_perf.py`, skipped unless `MVIBE_ECON_PERF=1` so the offline gate stays fast and deterministic):

1. **Micro** — `EconShunt.apply()` alone on the attributed payload dict (captured verbatim from the first raw-relay POST, i.e. exactly the object the hook receives), memo pre-warmed with all 40 rendered summaries (steady-state replacement path), fresh `copy.deepcopy` per iteration, `time.perf_counter_ns` around the `apply` call only. N=100.
2. **Full app** — POST wall-clock (`perf_counter_ns` around `client.post` through `httpx.ASGITransport`) of the real wired app: econ **enabled+warmed** (all 40 replaced on every request) vs **disabled** (no econ object at all), same body, 5 untimed warm-ups then N=50 timed requests each.
3. **Concurrency** — same full-app setup, N=4 concurrent clients (`asyncio.gather` of 4 POST streams × 50 requests each), per-request samples; measures event-loop blocking of the sync parse+walk+rewrite slices.

Repeat runs: 3 consecutive full runs of the file; ranges below span all three.

### Results

| Measurement | mean | p95 | range across 3 runs (mean) |
|---|---|---|---|
| Micro `apply()` enabled+warmed (n=100) | **0.62 ms** | 0.63 ms | 0.616 – 0.621 ms |
| Full app enabled+warmed (n=50) | 2.51 ms | 2.69 ms | 2.493 – 2.521 ms |
| Full app disabled (n=50) | 1.80 ms | 1.90 ms | 1.786 – 1.818 ms |
| **Full-app added latency (enabled − disabled)** | **+0.70 ms** | +0.8 ms | +0.688 – +0.717 ms (p95 delta +0.53 … +0.82) |
| Concurrency=4 enabled+warmed (n=200) | 10.0 ms | 11.5 ms | 9.914 – 10.188 ms |
| Concurrency=4 disabled (n=200) | 8.2 ms | 8.5 ms | 7.898 – 8.417 ms |
| **Concurrency=4 per-request delta** | **+1.8 ms** | +2.3 ms | +1.531 – +2.290 ms (p95 delta −0.4 … +3.8, see note) |

Notes on reading the numbers:
- The micro figure is the pure rewrite cost (select + correlate + SHA-256 over 250 KiB + memo gets + content replacement + counters). The full-app delta additionally contains the fail-open snapshot serialization of the attributed body (~250 KB `json.dumps` before `apply`) **minus** the savings from forwarding the rewritten ~10 KB body instead of ~250 KB — a net number, still ≈ +0.7 ms.
- Under 4-way in-process concurrency the sync slices serialize on the event loop, so the added per-request latency grows ≈ with the number of in-flight rewrites: mean delta +1.5…+2.3 ms, consistent with ≈4 × the serial delta minus overlap. The concurrent p95 *delta* is the noisiest figure (one run negative, one +3.8 ms) because the disabled baseline itself shows 40–52 ms scheduler outliers that move its p95; the mean delta is the steadier indicator. Worst observed concurrent p95 delta (+3.8 ms) remains under budget.

### Budget verdict
Design target: **< 5 ms added request-path latency for a 250 KB body** (design.md, Performance & Scalability).

**PASS with headroom.** Steady-state rewrite (micro): **0.62 ms mean / 0.64 ms p95** — ~8× under budget. Full-app net added latency: **+0.70 ms mean** (~7× under). Under 4 concurrent clients the added per-request cost stays at **+1.5…+2.3 ms mean** (worst observed p95 delta +3.8 ms). No follow-up optimization required; nothing near budget.

### SIMD-JSON / concurrency decision note (feeds operator docs)
- **Stdlib `json` stays.** All figures are far from the 5 ms budget; swapping parsers would put byte-exactness of the relayed body at risk for no needed gain.
- Identified escalation if bodies ever grow or the budget tightens: **loads-only `orjson`** (drop-in parse, byte-identical decoded object; the *serializer* stays stdlib so forwarded bytes stay byte-exact).
- Threads do not help the rewrite slices: the work is pure-Python CPU under the GIL; a thread pool would add contention, not parallelism.
- Concurrency escalation (if many simultaneous large-history clients ever push loop-blocking past budget): **multi-worker deployment** (one uvicorn worker per core, each with its own mmhmem child and memo) — state is per-process by design, so no cross-worker coordination is required.

### Test-hardening rider (from the 4.1 review, applied in this task)
`test_e2e_tool_error_relays_raw_backs_off_then_recovers` and `test_e2e_malformed_line_relays_raw_backs_off_then_recovers` now poll the memo entry directly (`eventually(memo.get(sha).kind == ...)`) instead of `eventually(tools_calls == N)` followed by an immediate `memo.get(sha)` assert; the call-count assert remains as a secondary, non-racy exactness check after the memo settles. RED was demonstrated with a temporary plugin (`slow_memo_plugin`, delay inserted between the fake recording the call and the warm task writing the memo): both old tests failed deterministically at the racy lines; the refactored tests pass with the plugin active and without it. The plugin was throwaway evidence, not committed.

### Artifacts
- `tests/test_econ_perf.py` — benchmarks (env-gated) + always-run body-shape test
- Run command: `MVIBE_ECON_PERF=1 uv run --locked --offline -- pytest -q tests/test_econ_perf.py -s -n 0` (PowerShell: `$env:MVIBE_ECON_PERF='1'; ...`); `-n 0` avoids a second xdist worker adding CPU noise
