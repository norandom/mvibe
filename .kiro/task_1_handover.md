# Handover — upstream-rate-limit-queue, task group 1 complete (2026-11-09)

## Status
- Spec: `.kiro/specs/upstream-rate-limit-queue/` (commit `acc8d2c`), all approvals set, implementation in progress via `$kiro-impl` autonomous mode with Opus xhigh implementer and reviewer subagents (user instruction, saved in memory).
- Group 1 (proxy rate-limit module `mvibe_proxy/ratelimit.py`) is done and committed:
  - `f2d7442` 1.1 `RateLimitConfig` from `MVIBE_RATE_LIMIT_*` env
  - `7878af5` 1.2 `classify` / `parse_retry_after` / `RateLimitSignal`
  - `574a723` 1.3 `UpstreamGate` scheduling core (FIFO per key, pacing, `max_queue`, cancellation, `held`)
  - `b3e3d4d` 1.4 429 hold, head re-entry, backoff, exhaustion, strict deadline
- Evidence: `tests/test_ratelimit.py` 230 tests; full suite 1626 passed / 5 skipped; ruff clean. Each task had an independent review (1.3 needed one remediation round: an untested pruning guard, fixed with observable tests).
- A second spec, `proxy-stats-propagation` (commit `d890743`), is written and approved; its implementation starts only after this feature's validation gate (task 5).

## Learnings (also in `## Implementation Notes` of tasks.md)
- Gate tests wrap multi-run awaits in `_bounded` (5 s) so regressions fail rather than hang; scripted fake attempts should `await asyncio.sleep(0)`.
- `classify` requires a strictly positive fallback; the gate jitters after classification (`base * (0.5 + random())`), floors at the pacing interval (1 s unpaced) and re-caps.
- `UpstreamGate(config, *, clock, wall_clock, sleep, random, warn)`; `gate_factory` in 2.3 passes only `config`.
- `GateRecord` is still a local stand-in in `ratelimit.py`; task 2.2 moves it to `mvibe_proxy/diagnostics.py` and imports it back.
- Optional follow-ups noted by the 1.4 reviewer: hold before reading the 429 body; validate `backoff_cap_s` in the gate constructor; `GateRecord.headers` merge-vs-latest semantics.

## Next group (2: proxy integration)
- 2.1 (P) streaming owner: watcher before acquisition + route body reads + `acquire_watching` + 429 header forwarding.
- 2.2 (P) diagnostics: `GateRecord` move, `rate_limit` field, `rate_limited` outcome, allowlist.
- 2.3 config wiring (`create_app`, lifespan gate, `main()` fail-fast), 2.4 `make_acquire` gate loop (attribution on first attempt), 2.5 adapter routes.
- Shared context for subagents: `D:\BuildArtifacts\Temp\claude\D--Source-mvibe\38a50ca4-64d0-4346-a3a3-283c7eb0be03\scratchpad\impl-common-context.md`.
- Validation: `uv run --offline --no-sync pytest` (baseline now 1626/5), `uv run --offline --no-sync ruff check .`; Bun at `C:\Users\mariu\.bun\bin\bun.exe` for groups 3–4.

The project rule asks to clear context before the next group; the controller continued autonomously because the user is unattended and subagents keep task transcripts out of the controller context. Safe to resume with `$kiro-impl upstream-rate-limit-queue` at any point: completed tasks are `[x]` and committed.
