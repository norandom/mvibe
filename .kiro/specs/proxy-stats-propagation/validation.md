# Validation Report — proxy-stats-propagation

**Date:** 2026-11-10
**Validated commit:** `83968a7` (follow-up on tasks 3.1+3.3; working tree clean apart from untracked workspace files)
**Baseline before the feature:** `c4944d5` (upstream-rate-limit-queue task 5)
**Decision:** GO (see "Verdict")

## Gate commands (task 5)

| Check | Command | Result |
|---|---|---|
| Lint | `uv run --offline --no-sync ruff check .` | All checks passed |
| Python suite | `uv run --offline --no-sync pytest -q -p no:cacheprovider` | 2017 passed, 7 skipped (38 s); baseline at `c4944d5` was 1738 passed, 6 skipped |
| Plugin suite | not rerun separately: `packages/` is unchanged since `c4944d5` (`git diff --stat c4944d5 HEAD -- packages` is empty); the Dagger check below runs `bun test` in the container | unchanged |
| Container CI | `dagger run uv run --locked --project .dagger --python 3.12 python .dagger/ci.py check` (native Dagger, Podman engine) | exit 0 on `83968a7`: `bun run lint`, `typecheck`, `build`, `bun test`, install-wrapper smoke, `uv run --locked --offline pytest` in the container, image build with `mmhmem 0.5.2`, `proxy.py --help`. A first attempt on the same tree failed before any check with a ghcr.io TLS handshake timeout while pulling the `uv` base image (network); the retry passed. |
| Protected surfaces | `git diff --stat c4944d5 HEAD -- mvibe_auth packages src/protocol.ts src/protocol.schema.json uv.lock bun.lock` | empty |
| Version coupling test | `git diff c4944d5 HEAD -- tests/test_version_coupling.py` | additive only (one new test, 19 lines, no removed lines), as task 4 specifies |
| Econ contract | `git diff c4944d5 HEAD -- mvibe_proxy/econ.py` | `shunt_content` signature identical; only its body now routes through the new generic `call_tool`; no `econ_*` field or `EconShunt` change |
| Dependencies | `uv.lock`, `bun.lock` | unchanged |

## Requirements traceability

| Criterion | Evidence |
|---|---|
| 1.1 chat usage (stream + non-stream) with model | `tests/test_proxy.py::test_inference_routes_record_upstream_usage_without_altering_bytes` (JSON and SSE cases); `tests/test_stats.py::test_observable_sse_usage_line`, `test_sse_usage_split_across_chunks` |
| 1.2 embeddings usage with model | `test_inference_routes_record_upstream_usage_without_altering_bytes[embeddings]`; `test_stats.py` embeddings → completion 0 |
| 1.3 adapter routes use upstream numbers | `tests/test_compat.py::test_adapter_routes_record_the_upstream_usage` (`/api/chat` SSE, `/v1/messages`); `tests/test_streaming.py::test_adapter_routes_observe_the_upstream_bytes_not_the_translation` |
| 1.4 bytes unaltered, bounded buffering | `test_streaming.py::test_observer_sees_every_upstream_chunk_then_completion_and_the_response_is_unchanged`, `test_an_observer_that_raises_is_dropped_and_the_response_is_unchanged`; `test_stats.py::test_json_body_at_the_bound_is_parsed_and_one_byte_over_yields_none`, `test_retained_bytes_stay_within_the_bound` |
| 1.5 unparseable / no usage → nothing recorded | `test_proxy.py::test_a_response_without_usage_records_nothing`; `test_stats.py::test_invalid_*`, `test_a_raising_parser_disables_the_observer_without_raising` |
| 1.6 only numbers and model retained | `test_stats.py::test_observer_retains_only_numbers_and_the_model` |
| 1.7 numeric diagnostic fields under the allowlist | `tests/test_proxy_diagnostics.py::test_usage_fields_are_absent_until_observed_and_numeric_once_set`; `tests/test_observability.py::test_exported_record_carries_observed_usage_numbers` |
| 2.1 cumulative client_usage per model, harness `mvibe-proxy`, per-process task id | `test_stats.py::test_one_call_per_model_with_cumulative_totals_and_one_task_id`, `test_observable_successful_tick_sets_last_report_at_and_the_model_source`; `test_proxy.py::test_the_default_reporter_reports_the_counters_ledger_over_the_shared_child` |
| 2.2 one batch per interval, final batch ≤ 2 s | `test_stats.py::test_an_unchanged_ledger_makes_no_call_on_a_tick`, `test_stop_cancels_the_loop_and_flushes_once`, `test_the_final_report_bound_is_two_seconds`, `test_a_hanging_final_report_is_bounded_and_counted` |
| 2.3 cumulative totals, new task per process | `test_a_later_tick_reports_new_cumulative_totals_not_deltas`; task id built once per reporter |
| 2.4 failures counted, one warning per streak, inference unaffected | `test_a_failure_counts_warns_once_and_skips_the_remaining_sources`, `test_one_warning_per_failure_streak`, `test_the_call_result_is_never_logged`; `test_proxy.py::test_a_stats_only_child_that_fails_to_start_warns_and_keeps_serving` |
| 2.5 enabled without binary refuses to start naming both settings | `test_proxy.py::test_cli_enabled_usage_without_a_binary_exits_2_naming_both_settings`, `test_cli_stats_flag_without_configuration_exits_2`; `test_stats.py::test_observable_enabled_usage_with_a_stats_binary` and the enabled-without-binary config test |
| 2.6 child started for stats alone, shared when both on | `test_proxy.py::test_stats_only_starts_the_injected_child_once_and_stops_the_reporter_before_it`, `test_economics_and_stats_share_one_child_owned_by_the_shunt`, `test_the_lifespan_runs_a_real_usage_reporter_over_the_injected_child_by_default` |
| 2.7 stats never enables economics | `test_proxy.py::test_economics_alone_starts_the_child_without_a_reporter`, `test_without_stats_or_economics_nothing_is_started_but_counters_exist`; `tests/test_version_coupling.py::test_compose_persists_mmhmem_state_next_to_the_compose_file` (econ key stays commented) |
| 3.1 counters by outcome, usage totals, econ and rate-limit aggregates, start and last report time | `test_stats.py::test_sink_aggregates_a_sequence_of_diagnostics`, `test_snapshot_is_json_safe_with_only_documented_keys_and_model_ids`; `test_proxy.py::test_counters_see_the_inference_requests_of_one_lifespan_and_no_local_routes` |
| 3.2 mmhmem JSON statistics within a bounded time | `test_stats.py::test_stats_json_is_parsed_into_days_and_totals`, `test_the_callers_environment_reaches_the_child`, `test_a_child_running_past_the_timeout_is_killed`; `test_proxy.py::test_stats_route_serves_counters_and_the_mmhmem_view_locally` (timeout 10 s, env passed) |
| 3.3 unavailable → counters still returned, sanitized reason | `test_stats.py::test_the_empty_store_notice_is_no_stats_yet`, `test_a_non_zero_exit_is_reported_by_its_code`, `test_invalid_output_is_invalid_json`, `test_a_spawn_failure_is_sanitized`; `test_proxy.py::test_stats_route_passes_an_unavailable_mmhmem_view_through`, `test_the_default_runner_reports_not_configured_for_the_inert_config` |
| 3.4 no credentials, prompts, content or env values | `test_stats.py::test_snapshot_is_json_safe_with_only_documented_keys_and_model_ids`; every runner result checked for the stderr sentinel and an env secret |
| 3.5 served locally, no upstream, no pacing, no econ | `test_stats_route_serves_counters_and_the_mmhmem_view_locally` (no upstream send, no `resolve` event); local routes bypass the gate by construction (no `make_acquire`) |
| 3.6 stats URL line at startup | `test_proxy.py::test_cli_prints_the_stats_url_for_the_bound_host_and_port` |
| 3.7 OpenAPI entry | `test_proxy.py::test_openapi_documents_the_stats_route` |
| 4.1 bind mount + data dir defaults | `tests/test_version_coupling.py::test_compose_persists_mmhmem_state_next_to_the_compose_file`; `docker compose config` through the Podman provider rendered `D:\Source\mvibe\mmhmem-data -> /data/mmhmem` |
| 4.2 state survives container recreation | by construction of the bind mount; **manual live check** (outside the offline gate) |
| 4.3 unwritable data dir warns once, keeps serving | `test_proxy.py::test_cli_warns_once_when_the_data_dir_is_not_writable` |
| 4.4 placeholder tracked, contents ignored | `test_compose_persists_mmhmem_state_next_to_the_compose_file`; `git check-ignore mmhmem-data/anything` prints the path while `.gitkeep` is tracked |
| 4.5 `docker compose exec proxy mmhmem stats` and `GET /stats` | compose comment documents it; **manual live check** |
| 4.6 compose invariants intact | `test_docker_mmhmem_release_pin_and_single_ci_pipeline` still green; Dagger build verifies `mmhmem 0.5.2` |
| 5.1 no auth / helper / econ contract changes | protected-surface and econ-contract diffs above |
| 5.2 unconfigured → behaves as today plus `/stats` and the URL line | `test_without_stats_or_economics_nothing_is_started_but_counters_exist`, `test_cli_without_stats_settings_passes_the_inert_config`; full suite green |
| 5.3 new fields numeric, allowlisted by name | `usage_prompt_tokens`, `usage_completion_tokens` in `observability._ALLOWED_FIELDS`; `test_exported_record_carries_observed_usage_numbers` |

## Review evidence
- Implementer status reports with RED output and mutation checks: 1.1 (6 mutants killed), 2.1–2.3 (22 killed), 2.4 (7 caught). Controller-implemented tasks (1.2, 1.3, 3.1, 3.2, 3.3, 4) were gated by the mechanical checks above; the lifespan ordering, sink clearing and local-route exclusion are asserted directly by the 3.1 tests.
- Review mode: lightweight (controller mechanical checks: ruff, focused and full pytest, marker and secret greps, boundary diffs), per the user's instruction to speed the process up.

## Residual risks and manual follow-ups
1. **Live container check** (manual, outside the offline gate): `docker compose up --build`, one chat request through the proxy, `docker compose exec proxy mmhmem stats`, `curl http://127.0.0.1:8765/stats`; confirm `./mmhmem-data` is writable by uid 1000 inside the Podman machine (Windows bind mounts may map ownership differently than a Linux daemon).
2. The reporter resends every non-zero source each tick when the ledger changed; mmhmem keeps the latest report per (project, harness, task, source), so nothing is double-counted. Larger model fleets simply produce more calls per tick.
3. Operator docs (`docs/proxy-guide.md`, `docs/token-economics.md`) do not yet describe `MVIBE_STATS_*`, `--stats` or `/stats`; the compose comments and `--help` do. Documentation at release time.

## Verdict
GO. All 30 acceptance criteria have automated evidence except 4.2 and 4.5, which are container-runtime behaviours covered by construction and listed as the manual live check.
