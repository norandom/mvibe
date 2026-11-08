# Amendment 1 — Docker-embedded stdio child (2026-11-06)

User-directed design change, decided in conversation on 2026-11-06. This amendment
replaces the transport and deployment assumptions of the approved 2026-11-03 design.
Eligibility, memo semantics, marker text, counters, diagnostics, fail-open core and
the offline gate are unchanged.

## Decisions

- **D1 — Deployment target**: the proxy is deployed from Docker (`docker-compose.yml`
  is the operator entry point), not as a host process.
- **D2 — mmhmem is embedded**: the mmhmem binary from the CI-published release
  pinned by `MMHMEM_VERSION` (see Amendment 2 below) is embedded
  into the proxy image and runs as a **supervised child process in the same container**,
  launched as `mmhmem stdio` (local profile).
- **D3 — Transport**: MCP JSON-RPC over **stdio pipes** (newline-delimited JSON, one
  JSON-RPC message per line — the framing rmcp's io transport uses). Replaces
  Streamable HTTP + `httpx` + bearer + `mcp-session-id` + SSE parsing + 404 re-init.
- **D4 — Rejected alternatives** (with reasons, so they are not re-litigated):
  - *gRPC*: mmhmem has no gRPC surface; its steering mandates protocol-native MCP;
    econ calls are background-only (one per distinct content hash), so gRPC's
    benefits cannot materialize.
  - *In-process FFI / "callable interface"*: mmhmem is a daemon, not a library;
    PyO3/C-ABI restructuring is a cross-repo rewrite; a Rust panic or OOM would
    abort the proxy process, breaking the fail-open guarantee (Req 4.5); ABI and
    wheel-matrix coupling forever; contradicts the 2026-11-03 decision record in
    `.kiro/handover.md` ("consume mmhmem as an external service over its existing MCP").
  - *mTLS / TLS gateway*: dissolved — with stdio there is no network hop at all.
    Pipes are kernel-enforced process-local; no ports, no tokens on the wire.
  - *Remote mmhmem (HTTP daemon)*: dropped from v1. May return as a future
    amendment if a shared-daemon topology is ever needed.

## Evidence gathered

- mmhmem local profile (`src/config/profile.rs`): **zero mandatory env vars** for a
  spawned stdio child — token optional and unread (stdio opens no listener),
  confinement roots optional, per-OS user-level path defaults; worker settings from
  env merged with `.mmhmem.json` (project over global).
- Launch shape: `[<bin>, "stdio"]` (registration mmhmem itself writes in
  `src/setup/adopt_fixtures.rs`).
- Newline-delimited JSON framing pinned by mmhmem's own stdio test harness
  (`tests/audit_rotation.rs`: write line + `b"\n"`, `read_line`).
- `shunt_content` contract (companion spec) is transport-neutral: tool params
  `{project, agent_harness:'mvibe-proxy', content, kind:'file_read', source_hint?}`;
  result is one text block with a JSON object
  `{outcome: summarized|cached|bypassed, summary?, bypass_reason?, content_sha256, line_count, avoided_input_tokens_est, latency_ms?, worker_model?}`
  (absent fields omitted); tool errors carry sanitized messages.

## Deltas

### Configuration

- Removed keys: `MVIBE_ECON_URL`, `MVIBE_ECON_TOKEN`.
- Added key: `MVIBE_ECON_MMHMEM_BIN` (path to the mmhmem binary; **absent ⇒
  economics disabled**, preserving off-by-default). `--econ` without it exits 2
  naming the key. When economics is enabled the binary path must exist and be
  executable, else an actionable startup error.
- Unchanged keys: `MVIBE_ECON_PROJECT` (default `mvibe`), `MVIBE_ECON_TOOLS`
  (default `read`), `MVIBE_ECON_MIN_BYTES` (4096), `MVIBE_ECON_TIMEOUT_MS` (45000),
  `MVIBE_ECON_MEMO_MAX` (2000), `MVIBE_ECON_RETRY_S` (300), `MVIBE_ECON_CONCURRENCY` (2).
- Child environment: the child inherits the proxy process env; the operator
  configures mmhmem worker/data settings through the proxy container env.
  Correction (2026-11-06, task 4.2 review, verified against mmhmem
  `src/config.rs`/`profile.rs`): mmhmem's local profile reads
  `WORKER_API_URL`, `WORKER_MODEL`, `WORKER_API_KEY` (no `AGENT_MEM_WORKER_*`
  prefix; the prefix in the original draft of this amendment was wrong), plus
  `AGENT_MEM_DATA_DIR`, `AGENT_MEM_LOG_FILE`. `AGENT_MEM_WORKER_*` aliases are
  a candidate for the mmhmem companion spec. Worker-unconfigured children
  report tool errors → memoized failure + back-off (fail-open holds).

### Requirement 1.3 (replaced)

Old: warn when the configured endpoint is not loopback.
New: with a stdio child there is no endpoint address; tool-result contents **never
traverse a network** and never leave the container. The startup validation that
replaces the warning: binary path resolution (exists, executable) with an
actionable error, and the operator guide states the in-container trust boundary
(the child sees full tool-result contents by design).

### Transport (design §transport)

`McpClient` (httpx) → `StdioMcpClient`:

- Owns the child: spawn `[bin, "stdio"]` via `asyncio.subprocess`; stdin/stdout are
  the JSON-RPC channel; stderr is captured to the proxy log at debug level.
- Protocol: `initialize` → `notifications/initialized` → `tools/call shunt_content`;
  incrementing JSON-RPC ids; responses matched by id; one message per line.
- Supervision: on abnormal child exit, in-flight calls fail fast (→ `Outcome.failed`
  with back-off) and the child is restarted with capped exponential back-off
  (1 s → 30 s); idle children are left running. Shutdown: close stdin, wait ≤ 2 s,
  terminate.
- Constructor takes injectable reader/writer streams so tests drive an in-process
  fake without spawning anything; the spawn path is a thin adapter.
- Removed: bearer header, session header echo, SSE parsing, 404 re-init, 401 handling.
- Errors remain sanitized (`McpError`, no content, no paths).

### Testing

`FakeMmhmem` becomes an in-process stdio double (stream pair speaking the companion
contract). Modes: summarized, cached, bypassed, tool error, malformed line,
hang-beyond-timeout, child death (EOF mid-request). No httpx MockTransport.

### Deployment artifacts

- `Dockerfile`: single-stage — the embedded mmhmem is downloaded from
  mmhmem's CI-published release for `ARG MMHMEM_VERSION` (the same signed
  artifact set the installers ship; checksum-verified against the release
  `SHA256SUMS` at build time) and installed at `/usr/local/bin/mmhmem`.
  Amendment 2 (2026-11-08, user-directed): replaced the original
  pinned-ref clone-and-compile builder (`ARG MMHMEM_REF`) so the image
  consumes the CI-built release binary instead of compiling from source;
  `.dagger/ci.py check` (the single local + GitHub Actions container pipeline)
  builds this image and verifies the embedded `mmhmem --version` and `proxy.py`
  entrypoint. `MMHMEM_VERSION` must always reference an already-published
  mmhmem release (`0.5.2`, the first release containing `shunt_content`).
- `docker-compose.yml`: `MVIBE_ECON_MMHMEM_BIN` and `AGENT_MEM_*` env passthrough;
  volumes for mmhmem data/audit when economics is enabled; still no extra ports
  published (the child opens no listener).
- `docs/token-economics.md`: Docker-first operator guide incl. child env, trust
  boundary, fail-open behaviour, and the note that non-Docker installs need a
  separately installed mmhmem binary on PATH.

### Traceability touch-ups

Rows 1.3, 2.5 (unchanged), 4.2 ("call mmhmem's content tool once per distinct
content hash" — now over stdio), 4.6 ("no self-calls" unchanged; "targets the
configured URL" → "targets the spawned child only"), 6.1 (fake stdio double).
Revalidation trigger: "rmcp Streamable HTTP framing or session-header behaviour"
→ "rmcp stdio framing or the `stdio` subcommand contract".
