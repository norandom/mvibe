# Handover — proxy-stats-propagation, task group 1 complete (2026-11-10)

## Status
- Group 1 (seams in existing modules) is done and committed:
  - `2862d52` 1.2 `RequestDiagnostic.usage` → `usage_prompt_tokens`/`usage_completion_tokens` on stdout and export; `diagnostics.set_counters_sink`; 1.3 `StdioMcpClient.call_tool` (controller, direct)
  - `e982979` 1.1 read-only observer tap: `ProxyStreamOwner.response(..., observe_factory=None)`; `_stream_body` feeds raw chunks and a terminal `None` (implementer, Opus xhigh; 21 tests, 6 mutants killed)
- Also committed from later groups: `a627cac` task 4 compose persistence (`./mmhmem-data` bind mount, `.gitkeep`, stats keys active, coupling test), `9cb5b0e` tasks 2.1–2.3 (`mvibe_proxy/stats.py`: `StatsConfig`, `UsageObserver`, `UsageLedger`, `ProxyCounters`; 160 cases, 22 mutants killed), `3.2` routes pass the observer (controller, direct).
- Evidence at the 3.2 commit: proxy-side suites 233 passed (`tests/test_proxy.py`, `test_compat.py`, `test_streaming.py`, `test_proxy_diagnostics.py`); ruff clean.

## In flight / next
- 2.4 `UsageReporter` + `mmhmem_stats` (implementer running): throttled `session_resume_or_handoff` reports with `client_usage`, `report_succeeded/report_failed` on `ProxyCounters`, subprocess runner for `mmhmem stats --json` with a fixture script.
- 3.1 lifespan/config/`main()` wiring (child shared with econ or stats-only, counters sink, reporter start/stop, `--stats`, data-dir writability warning, stats URL line), 3.3 `GET /stats`, 5 validation (python gates, native Dagger, diff check).
- Learnings: the `tapped()` test helper must accept the routes' `observe_factory` keyword; `StatsConfig.from_env` raises on an invalid binary even when usage is off (`main()` downgrades to a warning unless `--stats`/`MVIBE_STATS_USAGE` is set); `ProxyCounters.report_succeeded(at)`/`report_failed()` are the only report writers.
