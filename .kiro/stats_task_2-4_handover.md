# Handover — proxy-stats-propagation, task groups 2, 3 and 4 complete (2026-11-10)

## Status
- Group 2 (stats module): `9cb5b0e` 2.1–2.3 (`StatsConfig`, `UsageObserver`, `UsageLedger`, `ProxyCounters`; implementer Opus xhigh, 160 cases, 22 mutants killed), `8e10ce2` 2.4 (`UsageReporter`, `mmhmem_stats`, fixture `tests/fixtures/fake_mmhmem_stats.py`; implementer Opus xhigh, 56 tests, 7 mutants caught).
- Group 3 (proxy wiring): `86a72d2` 3.2 routes observe usage; `b5e18d8` 3.1+3.3 lifespan, `--stats`, `GET /stats` (controller, direct, 27 tests); follow-up commit wires the defaults to the stats module (3 tests).
- Group 4 (compose persistence): `a627cac`.
- Evidence at the follow-up commit: full Python suite 2017 passed / 7 skipped, ruff clean; protected surfaces unchanged since the pre-stats baseline `c4944d5` (only an additive test in tests/test_version_coupling.py, as task 4 specifies); native Dagger check passed on the tree before 2.4 landed, final run pending for task 5.

## Decisions recorded in tasks.md Implementation Notes
- Local routes (`/health`, `/stats`) are excluded from the proxy counters via `diagnostic.local`.
- A stats-only child that fails to start warns once (class name only) and inference keeps serving; economics keeps fail-fast.
- `MVIBE_STATS_USAGE` opt-out values never turn a configuration error into exit 2.
- `mmhmem_stats` adds the reason `spawn_failed`; stderr and stdin of the child are DEVNULL.

## Next
- Task 5 validation gate → `.kiro/specs/proxy-stats-propagation/validation.md` (GO/NO-GO), then the manual live check outside the offline gate: `docker compose up --build`, one chat request through the proxy, `docker compose exec proxy mmhmem stats`, `curl http://127.0.0.1:8765/stats`, and confirm `./mmhmem-data` is writable by uid 1000 inside the Podman machine.
