# Research & Design Decisions

## Summary
- **Feature**: `proxy-stats-propagation`
- **Discovery Scope**: Extension (light discovery) of the proxy's response path, lifespan and Compose deployment; integration with the sibling mmhmem project's stats contract.
- **Key Findings**:
  - The proxy already feeds mmhmem's estimated savings: every `shunt_content` call carries `project` (MVIBE_ECON_PROJECT, default `mvibe`) and `agent_harness: 'mvibe-proxy'`, so `mmhmem stats` shows an `mvibe-proxy` row per day once the child's store persists.
  - mmhmem's only intake for measured spend is `client_usage` on `session_resume_or_handoff` (`prompt_tokens`, `completion_tokens`, `source`), stored as `token_stats` rows of kind `client_reported`; `mmhmem stats` counts the latest cumulative report per (project, harness, task, source) on the report's UTC day. Each resume call also appends a breadcrumb, so reporting must be throttled.
  - `mmhmem stats --json` opens the store read-only and merges audit-log activity; its JSON rows are `DayAggregate` objects (day, project, harness, `avoided_input_tokens_est`, `frontier_*_client_reported`, `client_reported_sources`, worker tokens, activity counts). With nothing recorded it prints a plain-text notice, not JSON.
  - In Docker the child's state (`AGENT_MEM_DATA_DIR`) is optional and currently commented out as a named volume; the container runs as uid 1000 (`app`), and Docker creates a missing bind-mount directory root-owned, which the child could not write.

## Research Log

### How the proxy's savings reach mmhmem today
- **Context**: The operator asked what the proxy reports for mmhmem stats and whether there is a stats link.
- **Sources Consulted**: `mvibe_proxy/econ.py` (`StdioMcpClient.shunt_content` L536-L560, `AGENT_HARNESS = 'mvibe-proxy'` L362, `_parse_shunt_result` L426-L465), `docs/token-economics.md` ("Reading econ events"), `.kiro/specs/proxy-econ-client/design.md` (Out of Boundary: "Usage metering, `GET /stats`, budget 429s (future spec)").
- **Findings**: The proxy's only mmhmem interaction is `tools/call shunt_content`. mmhmem records `avoided_input_tokens_est` per call under the proxy's project and harness. The proxy exposes only `econ_*` diagnostic fields on its own events; there is no `/stats` endpoint and no link.
- **Implications**: Estimated savings need no proxy change beyond persistence. Measured spend and a stats surface are new.

### mmhmem stats contract
- **Context**: Determine how to propagate measured spend and how to read statistics.
- **Sources Consulted** (sibling checkout D:\Source\mmhmem at `97b882e`, v0.5.2): `src/mcp/params.rs` (`client_usage: Option<ClientUsageParam>` on `session_resume_or_handoff`), `src/mcp/tools.rs` L442-L467 (`record_client_usage`, failures only warn), `src/services/stats.rs` L197-L222 (row kind `client_reported`, details `{prompt_tokens, completion_tokens, source, label}`), `src/cli/stats_client.rs` (latest report per project/harness/task/source, UTC day), `src/cli/stats.rs` (`DayAggregate`, `Totals`, `AGGREGATE_SQL`), `src/cli/stats_args.rs` (`--json`, `--all`), `src/cli/mod.rs` (`NO_STATS_MESSAGE` returned before JSON rendering when empty), `README.md` ("mmhmem stats --json # all data for scripts"), `docs/usage.md` ("Statistics are grouped by day, project and agent. Estimated savings and client-reported values remain separately labelled."), `docs/configuration.md` (`AGENT_MEM_DATA_DIR`, `AGENT_MEM_LOG_FILE` default `<data dir>/audit.jsonl`).
- **Findings**:
  - `client_usage` is cumulative per task; the proxy must keep per-source cumulative totals and re-send them, and must use a task id unique per process so a restart does not overwrite an earlier process's totals.
  - `source` is a free label; one label per model yields a per-model breakdown in `client_reported_sources` while totals stay summed per day.
  - `session_resume_or_handoff` appends a breadcrumb per call and may return checkpoint text; the proxy must ignore the returned state and throttle calls (one batch per interval).
  - `mmhmem stats --json` is a separate process invocation reading the same store read-only (WAL journal), safe to run while the stdio child is alive. The empty case returns non-JSON text that must be tolerated.
- **Implications**: A generic `tools/call` method on the existing stdio client, a throttled reporter, and a subprocess runner for `mmhmem stats --json`.

### Response path and usage shape
- **Context**: Usage must be observed without altering byte-exact forwarding.
- **Sources Consulted**: `mvibe_proxy/streaming.py` (`_OwnedResponse._stream_body` with the optional mutating `transform`), `mvibe_proxy/compat.py` (`_usage_fields` L184-L191; `OllamaStreamTranslator` captures `obj['usage']` from stream chunks L267-L268 and emits it at the end L291, which is live evidence that Mistral's stream carries `usage` in a late chunk), web search (Lago integration guide: "usage is captured from the final chunk of the stream"; Mistral counts cached tokens inside `prompt_tokens`).
- **Findings**: Non-streamed responses are one JSON object with `usage`; streamed responses are SSE `data:` lines where a late chunk carries `usage`. Adapter translators already parse upstream chunks, so an observer on the raw upstream bytes (before any transform) covers raw and adapter routes alike. Mistral's documentation for the exact final-chunk shape was not reachable; the observer must tolerate `usage` appearing in any chunk and take the last one seen.
- **Implications**: A read-only observer fed with upstream chunks: SSE mode scans complete `data:` lines and parses only those containing `"usage"`; JSON mode accumulates up to a bound (4 MiB) and parses once at completion. Nothing it does can change the bytes sent.

### Docker persistence
- **Context**: "Persist that in ./ from where we run docker-compose."
- **Sources Consulted**: `docker-compose.yml` (commented `mmhmem-data:/data/mmhmem` named volume and `AGENT_MEM_*` keys), `Dockerfile` (`useradd --uid 1000 app`, `USER app`, `XDG_DATA_HOME=/data`), `docs/token-economics.md` ("Volumes" bullet: fresh named volume is root-owned), `tests/test_version_coupling.py::test_docker_mmhmem_release_pin_and_single_ci_pipeline` (asserts the `MMHMEM_VERSION` default pattern and absence of `MMHMEM_REF`), Compose semantics (relative bind-mount paths resolve against the project directory, which is the compose file's directory).
- **Findings**: A bind mount `./mmhmem-data:/data/mmhmem` persists next to the compose file. Docker creates a missing host directory as root, which uid 1000 cannot write; creating it beforehand (`mkdir -p mmhmem-data`) as the invoking user avoids that on WSL where the default user is uid 1000. The proxy can detect an unwritable data directory at startup and warn with the fix. The compose invariants test is unaffected by volume changes.
- **Implications**: Enable the bind mount and `AGENT_MEM_*` keys by default; add `mmhmem-data/` to `.gitignore`; add a startup writability warning; document `mkdir -p` once.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Observer on upstream bytes (selected) | Read-only tap in `_stream_body` before any transform | Covers raw and adapter routes; byte-exact forwarding preserved | Must bound memory and never raise | Mirrors the existing transform seam |
| Parse translated adapter output | Reuse translators' usage | Already parsed | Misses raw `/v1` routes; translators drop fields | Rejected |
| Report per request | One mmhmem call per response | Simple | Breadcrumb spam; mmhmem counts latest cumulative anyway | Rejected |
| Throttled cumulative reporter (selected) | Per-source cumulative totals, one batch per interval + shutdown | Matches mmhmem's latest-report semantics; low noise | A crash between batches loses up to one interval | Accepted |
| `/stats` via subprocess `mmhmem stats --json` (selected) | Run the embedded binary read-only | No new protocol; identical to the CLI the operator uses | Subprocess per call; needs timeout and non-JSON tolerance | Loopback-only endpoint, infrequent |
| `/stats` via a new mmhmem MCP tool | Ask the child | No subprocess | Requires a mmhmem change | Rejected for now |
| Bind mount (selected) vs named volume | `./mmhmem-data` next to compose file | Visible, backup-able, requested by operator | Ownership on first creation | Startup warning + docs |

## Design Decisions

### Decision: Usage observer as a read-only tap
- **Selected Approach**: `UsageObserver.feed(chunk)` receives every upstream chunk before transform; `finish()` returns `Usage(prompt_tokens, completion_tokens, model)` or `None`. SSE detection by upstream `content-type: text/event-stream`; otherwise JSON accumulation up to 4 MiB. Exceptions are swallowed and disable the observer for that response.
- **Rationale**: Req 1.3, 1.4, 1.5, 1.6.

### Decision: Reporter semantics
- **Selected Approach**: `UsageLedger` keeps cumulative totals per model; `UsageReporter` sends one `session_resume_or_handoff` per source with `client_usage={prompt_tokens, completion_tokens, source: 'mvibe-proxy <model>'}`, `agent_harness: 'mvibe-proxy'`, `project: MVIBE_ECON_PROJECT`, `workspace_task_id: 'mvibe-proxy-<start-utc>-<8 hex>'`; at most once per `MVIBE_STATS_REPORT_INTERVAL_S` (60) when the ledger changed, plus a final flush bounded to 2 s at shutdown. Returned checkpoint text is discarded.
- **Rationale**: mmhmem counts the latest cumulative report per task and source (Req 2.1–2.3); breadcrumbs stay sparse.

### Decision: One shared stdio child
- **Selected Approach**: The lifespan starts `StdioMcpClient` when `econ_config` is present or `stats_config.usage` is enabled, and both `EconShunt` and `UsageReporter` use it. `StdioMcpClient` gains a generic `call_tool(name, arguments, timeout_ms)`; `shunt_content` becomes a thin wrapper.
- **Rationale**: Req 2.6; no second process.

### Decision: `/stats` composition
- **Selected Approach**: `ProxyCounters` (fed by the diagnostics middleware through a sink callable) provides request/outcome, usage, econ and rate-limit aggregates; `mmhmem_stats(bin, env, timeout)` runs `mmhmem stats --json` with the proxy's environment and returns parsed JSON or an `unavailable` reason (`not_configured`, `timeout`, `exit_<code>`, `no_stats_yet`, `invalid_json`).
- **Rationale**: Req 3.1–3.5.

### Decision: Configuration
- `MVIBE_STATS_USAGE` (`1`/`true` enables propagation; default off), `MVIBE_STATS_REPORT_INTERVAL_S` (60), `MVIBE_STATS_MMHMEM_TIMEOUT_S` (10), `MVIBE_STATS_MMHMEM_BIN` (defaults to `MVIBE_ECON_MMHMEM_BIN` when set). A separate binary key is needed because setting `MVIBE_ECON_MMHMEM_BIN` alone turns economics on (proxy-econ-client Req 1.1), and this feature must not change that default. `--stats` CLI flag mirrors `--econ`: fail fast when the configuration is incomplete. Compose enables `MVIBE_STATS_USAGE`, `MVIBE_STATS_MMHMEM_BIN`, the bind mount and `AGENT_MEM_*` by default; economics stays opt-in as today.

### Build vs adopt
- No new dependency: stdlib `json`, `asyncio.subprocess`, existing FastAPI, existing stdio client.

## Risks & Mitigations
- Mistral changes where `usage` appears in streams — observer takes the last `usage` seen in any chunk; absence means no record, never an error.
- Breadcrumb noise in mmhmem — throttled batches, discard returned state.
- Root-owned bind mount on first start — startup warning naming the path and `mkdir -p`/`chown` fix; documented one-time step.
- `/stats` subprocess cost — bounded timeout; loopback-only by default; no caching needed for an operator endpoint.
- Conflicts with `upstream-rate-limit-queue` edits in `streaming.py`/`proxy.py` — implement after that feature's validation gate (spec.json `implementation_after`).

## References
- D:\Source\mmhmem (v0.5.2): `src/cli/stats.rs`, `src/cli/stats_client.rs`, `src/services/stats.rs`, `src/mcp/params.rs`, `docs/usage.md`, `docs/configuration.md`.
- `docs/token-economics.md`, `.kiro/specs/proxy-econ-client/design.md` (deferred `GET /stats`).
- Compose bind mount semantics: relative paths resolve against the project directory (compose file location).
