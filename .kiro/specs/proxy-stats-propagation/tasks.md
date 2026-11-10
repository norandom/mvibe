# Implementation Plan

> Start only after `upstream-rate-limit-queue` has passed its validation gate (shared files: `proxy.py`, `mvibe_proxy/streaming.py`, `mvibe_proxy/diagnostics.py`).

- [x] 1. Seams in existing modules
- [x] 1.1 Streaming observer tap: `ProxyStreamOwner.response(acquire, diagnostic=None, transform=None, observe_factory=None)`; after `acquire()` returns, `_OwnedResponse.__call__` obtains `observe = observe_factory(upstream)` (exception → `None`); `_stream_body(upstream, send, transform=None, observe=None)` calls `observe(chunk)` per upstream chunk before transform and `observe(None)` at completion, wrapped so an observer exception never changes forwarded bytes or completion.
  - `tests/test_streaming.py`: the factory is called once with the acquired upstream response; the observer receives every chunk and the terminal `None`; a raising factory or observer leaves forwarded bytes and the terminal body identical to the no-observer run; the adapter `replay()`-style acquire works with the factory.
  - Observable: concatenated observed chunks equal the upstream body bytes.
  - _Boundary: ProxyStreamOwner, _OwnedResponse_
  - _Requirements: 1.3, 1.4_
- [x] 1.2 (P) Diagnostics fields and sink: `RequestDiagnostic.usage: tuple[int, int] | None`; `emit()` adds `usage_prompt_tokens`/`usage_completion_tokens` and calls the module-level counters sink (`set_counters_sink`) inside the never-raise guard; allowlist both fields in `observability._ALLOWED_FIELDS`.
  - `tests/test_proxy_diagnostics.py`: fields present only when set; a raising sink does not propagate; `tests/test_observability.py`: exported record keeps both fields.
  - Observable: a `--log` line for an observed request shows `"usage_prompt_tokens": 5`.
  - _Boundary: RequestDiagnostic, exporter allowlist_
  - _Requirements: 1.7, 5.3_
- [x] 1.3 (P) Generic tool call on the stdio client: `StdioMcpClient.call_tool(name, arguments, *, timeout_ms)` returning the raw result; `shunt_content` delegates to it with unchanged arguments, timeout and parsing.
  - `tests/test_econ.py`: existing shunt tests unchanged and green; new test issues `call_tool('session_resume_or_handoff', {...})` through the injected streams and asserts the JSON-RPC frame and result passthrough.
  - Observable: the frame sent for `call_tool` has `method == 'tools/call'` and `params.name` equal to the requested tool.
  - _Boundary: StdioMcpClient_
  - _Requirements: 2.1, 5.1_

- [ ] 2. Stats module (`mvibe_proxy/stats.py`; tasks 2.1–2.4 extend the same two files sequentially)
- [x] 2.1 Add `StatsConfig`/`StatsConfigError` with `from_env` for `MVIBE_STATS_USAGE` (truthy `1/true/yes/on`, default off), `MVIBE_STATS_REPORT_INTERVAL_S` (60), `MVIBE_STATS_MMHMEM_TIMEOUT_S` (10), `mmhmem_bin` from `MVIBE_STATS_MMHMEM_BIN` falling back to `MVIBE_ECON_MMHMEM_BIN` (must be an existing executable file when set; two different paths raise naming both keys) and `project` from `MVIBE_ECON_PROJECT` (default `mvibe`); enabled usage without a binary raises naming `MVIBE_STATS_USAGE` and `MVIBE_STATS_MMHMEM_BIN`; messages echo no values.
  - `tests/test_stats.py`: defaults; truthy forms; invalid numbers name the variable; enabled-without-binary names both variables; econ binary fallback; conflicting binaries raise.
  - Observable: `StatsConfig.from_env({'MVIBE_STATS_USAGE': '1', 'MVIBE_STATS_MMHMEM_BIN': <tmp executable>}).usage is True`.
  - _Boundary: StatsConfig_
  - _Requirements: 2.5, 2.7, 5.2_
- [x] 2.2 Add `Usage`, `UsageObserver(content_type, model)` with `feed(chunk)`/`finish()`/`as_callable(diagnostic)`: SSE mode line-buffers and parses only `data:` lines containing `"usage"`, keeping the last one; JSON mode accumulates up to 4 MiB and parses once; embeddings yield completion 0; any internal error disables the observer for that response; `feed` never raises or mutates input; the callable stores `finish()` into `diagnostic.usage` on the terminal `None`.
  - `tests/test_stats.py`: non-stream JSON usage; SSE usage split across chunks; SSE without usage → None; over-limit JSON → None; invalid JSON → None; embeddings usage; monkeypatched parser raising → None and no exception; `as_callable` sets `diagnostic.usage`.
  - Observable: feeding `b'data: {"usage":{"prompt_tokens":5,"completion_tokens":7}}\n\n'` then `finish()` returns `Usage(5, 7, model)`.
  - _Depends: 1.2_
  - _Boundary: UsageObserver_
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6_
- [x] 2.3 Add `UsageLedger` (per-model cumulative totals, `revision`) and `ProxyCounters` (`observe(diagnostic)` sink reading outcome, status, `econ`, `rate_limit` record and `usage`; `snapshot()` JSON-safe with the fixed key layout from the design).
  - `tests/test_stats.py`: totals and by_model; sink aggregates a sequence of `RequestDiagnostic` objects (completed, upstream_error 429 with an exhausted rate-limit record, econ mapping, usage set); `json.dumps(snapshot())` succeeds and keys are exactly the documented set plus model ids.
  - Observable: after two chat usages for one model, `snapshot()['usage']['by_model'][model]['prompt_tokens']` equals their sum.
  - _Depends: 1.2_
  - _Boundary: ProxyCounters, UsageLedger_
  - _Requirements: 1.7, 3.1, 3.4_
- [ ] 2.4 Add `UsageReporter` (throttled loop, one `call_tool('session_resume_or_handoff', ...)` per model source with cumulative `client_usage`, harness `mvibe-proxy`, per-process task id, result discarded; failure counting with one warning per streak; `stop()` final flush bounded to 2 s) and `mmhmem_stats(bin_path, env, timeout_s)` (runs `stats --json`, 8 MiB stdout cap, parses `{"days": [...], "totals": {...}}`, maps the "No token-savings statistics" notice / non-zero exit / timeout / invalid JSON to `available: false` reasons).
  - `tests/test_stats.py`: fake client records calls; unchanged ledger → no call; two models → two calls with cumulative totals and identical task id; failure → `failures` 1 and one warn; `stop()` flushes once; `mmhmem_stats` against a fixture script emitting `days`/`totals` JSON, the empty notice, exit 3 and a timeout.
  - Observable: `reporter.last_report_at` is set after a successful tick and the fake client saw `client_usage.source == 'mvibe-proxy mistral-large-latest'`.
  - _Depends: 1.3, 2.3_
  - _Boundary: UsageReporter, mmhmem_stats_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.2, 3.3_

- [ ] 3. Proxy wiring
- [ ] 3.1 Lifespan and configuration: `create_app(..., stats_config=None, stats_runner=mmhmem_stats)`; start the stdio child when economics or stats usage is enabled (binary from the econ config when economics is on, else from the stats config) and share it; in stats-only mode the lifespan shuts the child down after `reporter.stop()`; register `ProxyCounters` as the diagnostics sink for the app lifetime; start/stop `UsageReporter`; `main()` adds `--stats` (exit 2 unless `MVIBE_STATS_USAGE` and a binary are configured), loads `StatsConfig.from_env()` (exit 2 on error when `--stats` or `MVIBE_STATS_USAGE` is set, warning otherwise), warns when `AGENT_MEM_DATA_DIR` is set but unwritable, and prints `Stats: http://<host>:<port>/stats`.
  - `tests/test_proxy.py`: stats enabled + econ disabled starts the injected child once and shuts it down at lifespan exit; both enabled share one child; sink registered and cleared across lifespan; `main()` with `MVIBE_STATS_USAGE=1` and no binary exits 2 naming both variables; `--stats` without configuration exits 2; the stats URL line is printed; unwritable data dir warns (monkeypatched `os.access`).
  - Observable: `app.state.counters` exists after lifespan start and the sink is `None` after shutdown.
  - _Depends: 1.2, 1.3, 2.1, 2.4_
  - _Boundary: create_app lifespan, main_
  - _Requirements: 2.5, 2.6, 2.7, 3.6, 4.3, 5.2_
- [ ] 3.2 Observe usage on every upstream route: set `diagnostic.model` for `embeddings` as well as chat (reuse `_request_model`); routes and `adapter_post` pass `observe_factory=lambda upstream: UsageObserver(content_type=upstream.headers.get('content-type'), model=diagnostic.model).as_callable(diagnostic)` to `stream_owner.response(...)`.
  - `tests/test_proxy.py`: `/v1/chat/completions` non-stream and stream usage recorded on the diagnostic and in the ledger, forwarded bytes identical; `/v1/embeddings` usage recorded with its model; `tests/test_compat.py`: `/api/chat` stream and `/v1/messages` record upstream usage while the translated output is unchanged.
  - Observable: the ledger totals equal the sum of scripted upstream usages after the test requests.
  - _Depends: 1.1, 3.1_
  - _Boundary: make_acquire, routes, adapter_post_
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.7_
- [ ] 3.3 `GET /stats` route: returns `{"proxy": counters.snapshot(), "mmhmem": await stats_runner(bin, env, timeout)}`, registered before the catch-all routes, with an OpenAPI entry; never touches the gate, econ or auth.
  - `tests/test_proxy.py`: `/stats` returns both sections with an injected runner; no upstream send and no `resolve` event; `available: false` reasons pass through; `/openapi.json` lists `/stats`.
  - Observable: `curl http://127.0.0.1:8765/stats` on a fresh test app returns `requests.total == 0` and `mmhmem.available == false` with reason `not_configured`.
  - _Depends: 3.1_
  - _Boundary: stats route_
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7_

- [x] 4. Compose persistence: activate the bind mount `./mmhmem-data:/data/mmhmem` with `AGENT_MEM_DATA_DIR`, `AGENT_MEM_LOG_FILE`, `MVIBE_STATS_USAGE: "1"` and `MVIBE_STATS_MMHMEM_BIN: /usr/local/bin/mmhmem` in `docker-compose.yml` (economics keys stay commented; rewrite the stale "binary is inert" and named-volume comments), commit `mmhmem-data/.gitkeep` with `.gitignore` entries `mmhmem-data/*` and `!mmhmem-data/.gitkeep`, and extend `tests/test_version_coupling.py` to assert the active bind mount, the data-dir key and the placeholder alongside the existing invariants.
  - `uv run --offline --no-sync pytest tests/test_version_coupling.py -n 0` green; `docker compose config` (Podman compose provider on Windows, optional) renders the bind mount relative to the compose directory; also check that the mounted `./mmhmem-data` directory is writable by uid 1000 inside the Podman machine, since Windows bind mounts may map ownership differently than a Linux Docker daemon did.
  - Observable: `git check-ignore mmhmem-data/anything` prints the path while `mmhmem-data/.gitkeep` is tracked.
  - _Boundary: docker-compose.yml, .gitignore, mmhmem-data placeholder, version-coupling test_
  - _Requirements: 2.7, 4.1, 4.2, 4.4, 4.5, 4.6_

- [ ] 5. Validation gate: `uv run --offline --no-sync ruff check .`, `uv run --offline --no-sync pytest` green; `git diff --stat` shows no changes under `mvibe_auth/`, `packages/`, `EconShunt` replacement code or `econ_*` field names; record that the live Docker check (`docker compose up`, one chat request, `docker compose exec proxy mmhmem stats`, `curl /stats`) is a manual follow-up outside the offline gate.
  - Observable: both commands exit 0 in one run.
  - _Depends: 3.3, 4_
  - _Requirements: 5.1, 5.2, 5.3_

> Operator documentation (`docs/token-economics.md` persistence and stats sections, `docs/proxy-guide.md` `/stats`, release notes) follows at release time; task rules exclude documentation tasks.

## Implementation Notes
- 1.2/1.3 (controller, direct): `RequestDiagnostic.usage: tuple[int, int] | None` → `usage_prompt_tokens`/`usage_completion_tokens` on stdout and export (allowlisted); `diagnostics.set_counters_sink(callable | None)` is called once per `emit()` after the channels, inside its own never-raise guard. `StdioMcpClient.call_tool(name, arguments, *, timeout_ms)` returns the raw `result`; `shunt_content` delegates to it (timeouts name the tool, never the arguments).
- 4 (controller, direct): `./mmhmem-data:/data/mmhmem` bind mount, `AGENT_MEM_DATA_DIR`/`AGENT_MEM_LOG_FILE`, `MVIBE_STATS_USAGE: "1"` and `MVIBE_STATS_MMHMEM_BIN` are active in docker-compose.yml (econ keys stay commented); `mmhmem-data/.gitkeep` tracked with `mmhmem-data/*` + `!mmhmem-data/.gitkeep` ignore rules; `test_compose_persists_mmhmem_state_next_to_the_compose_file` guards it. Verified `docker compose config` through the Podman provider renders the mount as `D:\Source\mvibe\mmhmem-data -> /data/mmhmem`. The compose default sets the stats keys before the proxy reads them (task 3.1); until then they are ignored. Ownership of the bind mount for uid 1000 inside the Podman machine is a live-check item.
- 1.1: `ProxyStreamOwner.response(..., observe_factory=None)`; the factory runs once after acquisition (not when the client already left or on a 401/502 path); `_stream_body` feeds raw upstream chunks before any transform and sends the terminal `None` once the body was read to the end (even if the bounded close then fails); interrupted or cancelled streams never report completion; bodyless statuses get neither chunks nor `None`. Routes do not pass a factory yet (3.2). Test helper `call(..., path=)` and `tapped(app, factory)` exist in tests/test_streaming.py.
- 2.1–2.3 (one dispatch): `mvibe_proxy/stats.py` with `StatsConfig` (off values `0/false/no/off`; any other non-blank value is an error; binary messages omit the path; `from_env` raises on an invalid binary even when usage is off — `main()` downgrades that to a warning unless `--stats`/`MVIBE_STATS_USAGE` is set), `UsageObserver` (SSE keeps the last usage; a partial line beyond 4 MiB disables; malformed usage events are skipped), `UsageLedger` (+ `requests_by_model()`), `ProxyCounters` with `report_succeeded(at)`/`report_failed()` as the only report writers (2.4 must call them). An AST test pins that `stats` imports only `diagnostics` and stdlib.
