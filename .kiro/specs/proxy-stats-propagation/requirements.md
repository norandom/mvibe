# Requirements Document

## Project Description (Input)
Add stats propagation to the mvibe proxy: report the proxy's actual token usage to mmhmem so `mmhmem stats` shows real spend next to the estimated savings the proxy already records through `shunt_content`; expose a `GET /stats` endpoint on the proxy that returns mmhmem's statistics plus the proxy's own counters; and persist mmhmem's state in `./mmhmem-data` relative to the directory the Docker Compose file lives in, so statistics survive container restarts. Implementation follows the `upstream-rate-limit-queue` feature, which edits the same proxy files.

## Introduction
The proxy's token-economics child (`mmhmem stdio`) already records estimated savings for project `mvibe` under harness `mvibe-proxy` every time the proxy calls `shunt_content`. Two things are missing. First, mmhmem can show real frontier spend next to those estimates (`frontier_prompt_tokens_client_reported`, `frontier_completion_tokens_client_reported`), but only harnesses report it today; the proxy, which sees every upstream `usage` object, never does. Second, in the Docker deployment the child's state lives in the container's writable layer: the named volume is commented out, so every `docker compose up` starts with empty statistics, and there is no way to read them through the proxy. This feature observes upstream usage passively, propagates it to mmhmem, exposes a stats endpoint on the proxy, and persists the child's state in a bind-mounted directory next to the compose file.

## Boundary Context
- **In scope**: Passive observation of `usage` in upstream chat-completion and embeddings responses (raw `/v1` and adapter routes); in-process proxy counters; throttled propagation of cumulative usage to mmhmem as client-reported usage; a `GET /stats` endpoint returning proxy counters and mmhmem's own statistics; the Docker Compose persistence layout for mmhmem state; a startup line printing the stats URL.
- **Out of scope**: Changing what mmhmem counts or how `mmhmem stats` aggregates (owned by mmhmem); the economics replacement logic and its `econ_*` events; authentication; caller authentication for `/stats` (the proxy has none, by existing decision); budgets, quotas or 429s derived from spend; a stats endpoint in the OpenCode plugin; cross-process aggregation between a proxy and OpenCode sessions (mmhmem does that by project).
- **Adjacent expectations**: mmhmem's `session_resume_or_handoff` accepts `client_usage` as cumulative totals per project, harness, task and source and counts the latest report per task and source on the UTC day of the report; `mmhmem stats --json` reads the store read-only and the audit log, and prints a plain-text notice instead of JSON when nothing is recorded yet. The `upstream-rate-limit-queue` feature lands first and defines the per-request rate-limit record this feature aggregates.

## Requirements

### Requirement 1: Passive upstream usage observation
**Objective:** As an operator, I want the proxy to know how many tokens each upstream response consumed, so that spend can be counted without changing what clients receive.

#### Acceptance Criteria
1. When an upstream chat-completion response completes, whether streamed or not, and carries `usage` with `prompt_tokens` and `completion_tokens`, the proxy shall record those counts together with the request's model identifier.
2. When an upstream embeddings response completes and carries `usage.prompt_tokens`, the proxy shall record that count with the request's model identifier.
3. The proxy shall record usage from every route that reaches the upstream, including the Ollama and Anthropic adapter routes, using the upstream numbers rather than the translated response.
4. While observing a response, the proxy shall not alter, delay or buffer beyond a fixed bound the bytes forwarded to the client; a response larger than the bound is forwarded unchanged and simply yields no usage record.
5. If the observed body is not parseable or carries no `usage`, the proxy shall forward the response unchanged and record nothing for it.
6. The proxy shall retain from an observed response only the numeric usage fields and the model identifier, never message content.
7. When usage was recorded for a request, the proxy's per-request diagnostic shall carry the prompt and completion token counts as numeric fields under the existing allowlist discipline.

### Requirement 2: Propagation to mmhmem as client-reported usage
**Objective:** As an operator reading `mmhmem stats`, I want the proxy's real spend shown next to its estimated savings, so that I can judge the economics with measured numbers.

#### Acceptance Criteria
1. Where usage propagation is enabled and the mmhmem binary is configured, the proxy shall report its cumulative prompt and completion token totals to mmhmem as client-reported usage for the configured project, under harness `mvibe-proxy`, with one source label per model and a task identifier unique to the running proxy process.
2. The proxy shall send at most one batch of reports per configured interval (default 60 seconds) and one final batch at shutdown, bounded so shutdown is never delayed by more than 2 seconds.
3. The proxy shall report totals cumulatively for the lifetime of the process, so that mmhmem's latest-report-per-task semantics count each process exactly once per source and a restarted proxy begins a new task without erasing earlier totals.
4. If a report fails, the proxy shall continue serving inference unaffected, count the failure, and log one warning per failure streak without credentials or content.
5. If usage propagation is explicitly enabled but the mmhmem binary is not configured, the proxy shall refuse to start with a message naming both settings.
6. Where usage propagation is enabled, the proxy shall start the mmhmem stdio child even when economics is disabled, and shall share one child when both are enabled.
7. The proxy shall not enable economics as a side effect of enabling usage propagation; economics keeps its existing opt-in switch.

### Requirement 3: Stats endpoint on the proxy
**Objective:** As an operator, I want one URL on the proxy that shows mmhmem's statistics and the proxy's own counters, so that I can check savings and spend without entering the container.

#### Acceptance Criteria
1. When a client requests `GET /stats`, the proxy shall return a JSON document containing the proxy's request counts by outcome, cumulative usage totals (overall and per model), aggregated economics counters, and aggregated rate-limit queue counters when that feature is present, together with the process start time and the time of the last successful usage report.
2. Where the mmhmem binary is configured, the `/stats` response shall include mmhmem's statistics as produced by its JSON statistics command for the configured data directory, obtained within a bounded time (default 10 seconds).
3. If mmhmem's statistics are unavailable (binary not configured, command failure, timeout, or no statistics recorded yet), the `/stats` response shall still return the proxy counters and shall mark the mmhmem section unavailable with a short sanitized reason.
4. The `/stats` response shall contain no credentials, prompts, message content or environment values other than the configured project name and data directory path.
5. The proxy shall serve `/stats` locally without forwarding to the upstream and without applying upstream pacing or economics.
6. When the proxy starts serving, it shall print one line with the stats URL for the bound host and port.
7. `GET /stats` shall be documented in the proxy's OpenAPI description like the other local endpoints.

### Requirement 4: Persistent mmhmem state next to the compose file
**Objective:** As an operator running the proxy with Docker Compose, I want mmhmem's statistics and caches to survive restarts, so that `mmhmem stats` accumulates history.

#### Acceptance Criteria
1. The Compose definition shall bind-mount `./mmhmem-data`, resolved relative to the compose file, at the child's data directory and shall set the child's data directory and audit log location to it by default.
2. When the proxy container is recreated, mmhmem's statistics and caches shall remain available from the bind-mounted directory.
3. If the configured data directory is set but not writable by the proxy user at startup, the proxy shall log one warning naming the directory and the fix, and shall keep serving inference.
4. The repository shall contain an empty `mmhmem-data/` directory placeholder and ignore everything else inside it in version control, so a fresh checkout already provides the directory owned by the operator.
5. The operator shall be able to read the statistics from the host with `docker compose exec proxy mmhmem stats` and through `GET /stats`.
6. The existing Compose invariants (pinned mmhmem release, checksum verification, single CI pipeline) shall remain satisfied.

### Requirement 5: Compatibility and safety
**Objective:** As a maintainer, I want the stats work to leave existing contracts intact.

#### Acceptance Criteria
1. The proxy shall deliver Requirements 1 to 4 without changes to authentication, the helper protocol, the economics replacement contract or the `econ_*` event fields.
2. When usage propagation and the mmhmem binary are both unconfigured, the proxy shall behave exactly as today apart from the new `/stats` endpoint and the startup URL line.
3. Any new diagnostic or export field shall be numeric or a fixed enumeration and shall be added to the export allowlist by name.
