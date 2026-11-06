# Implementation Plan — proxy-econ-client

All tasks are required unless marked `- [ ]*`. Sub-tasks are sized for 1–3 hours. File ownership follows the design's File Structure Plan. The companion mmhmem spec `shunt-content-tool` supplies the real endpoint; every task here is verifiable against the in-process fake, so the two specs can be implemented in separate sessions. Amendment 1 (2026-11-06): transport is the embedded `mmhmem stdio` child over pipes; see `docker-stdio-amendment.md`. Task-graph sanity review: `inline` (no fresh subagent in the authoring session), verdict PASS.

## Implementation Notes
- 2.3 review (2026-11-06) approved three non-blocking transport hardening riders, to be applied within task 2.4's dispatch: (a) bound/type-check the JSON-RPC `error.code` used in `McpError(f"rpc error {code}")` (unbounded server value); (b) in `_dispatch`, treat a message as a response only when `'method' not in message` (defends against server-originated requests with colliding ids); (c) add an interleaved+close test proving EOF fails ALL in-flight waiters (≥2 concurrent calls).
- Ollama dialect drops tool ids in compat translation — eligibility intentionally yields no candidates for Ollama-shaped histories (id-free path, task 2.1 note).
- Proxy's Anthropic dialect parses `tool_call_id` on tool_result blocks (not the SDK's `tool_use_id`); fixtures match compat.py.
- econ.py single-module size annotation in design.md updated 2026-11-06 after task 2.3 (supervision scope grew the transport section past the pre-amendment ≤500-line estimate).
- 2.5 → 3.1 handoff (2026-11-06): EconShunt.apply sets `diagnostic.econ = EconCounters` (dataclass object), but design's RequestDiagnostic.econ is typed `dict | None` — task 3.1 owns the reconciliation (either type the field as EconCounters|None and convert at emit time, or convert in apply; pick ONE and keep the event allowlist names `econ_*` consistent with WarmQueue's `{event, econ_outcome, econ_latency_ms, econ_in_bytes}`). est_saved_tokens may be negative in theory (summary longer than original); diagnostics must not assume non-negativity. Warm event names in observability `_ALLOWED_FIELDS` must match WarmQueue emission exactly.
- 2.4/2.5 riders: emit-sink try/except Exception added (never-raise discipline); optional logger.debug breadcrumb beside it is a sanctioned 3.1 rider.
- 3.2 routed finding (2026-11-06): Ollama dialect can never receive replaced content — Ollama's wire format carries no tool-call ids on tool results (compat._ollama_messages drops them by protocol), so correlation fails closed and every Ollama resend relays raw. Documented as a spec limitation under Requirement 2.4 (fail-closed by protocol, safe under the fail-open philosophy). A future compat.py change could only help if the client provides ids. Pinned by tests: Ollama bodies relay byte-identical, zero mmhmem calls.
- 3.2 deferred gates: `docker build` + `docker compose config` NOT RUN (no docker CLI in the implementing environment) — Dockerfile/compose verified by inspection only; run both when docker is available (tracked for final validation). MMHMEM_REF defaults to `main`, which does not yet contain shunt_content (companion spec unimplemented); comments document that operators must pin a ref containing it. glibc base-drift note: rust:1-slim and python:3.12-slim must stay glibc-compatible; verify on first real image build, document pinning in the operator guide (task 4.2).
- 4.1 deferred gate: `scripts/verify.ps1` NOT-RUN (mandatory -Bun/-HostBinary toolchain absent offline; running would write a spurious failed acceptance record). Its Python step (`uv run --locked --offline -- pytest -n 2 --dist=loadscope`) was run directly and is green. Run verify.ps1 with prepared toolchain at final validation.
- 4.1 reviewer suggestion (non-blocking): two recovery tests have a narrow wait-ordering race (`eventually(tools_calls==N)` then immediate memo assert — the fake records the call before the warm task writes the memo). Optional hardening rider for task 4.3: poll the memo entry directly instead.
- 4.2 verified fact + corrections applied (2026-11-06): mmhmem's local stdio profile reads `WORKER_API_URL`/`WORKER_MODEL`/`WORKER_API_KEY` (config.rs:45-49 → profile.rs from_env_local:75-77); `AGENT_MEM_WORKER_*` does not exist in mmhmem. Corrected docker-compose.yml comment + Amendment 1 §Configuration accordingly; `AGENT_MEM_WORKER_*` aliases are a candidate follow-up in the mmhmem companion spec. Also noted (code-level, non-blocking): warm-event `econ_in_bytes` counts characters, per-request byte fields count UTF-8 bytes.

## 1. Foundation: configuration, test double and single body parse

- [x] 1.1 Economics configuration with validation and warnings
  - Parse all `MVIBE_ECON_*` settings from the environment with the documented defaults; absent `MVIBE_ECON_MMHMEM_BIN` means disabled; a set binary path that is missing or not executable is a validation error naming the key
  - Drop hard-excluded tool names from the allowlist with one warning
  - Observable completion: unit tests show `None` when unset, a validated config when complete, an actionable error for each malformed key and for a bad binary path, and the warning text
  - _Requirements: 1.1, 1.2, 1.3, 2.5_

- [x] 1.2 Fake mmhmem stdio double and econ test fixtures
  - In-process fake speaking the companion contract over injected asyncio streams (no subprocess): initialize handshake, initialized notification, `tools/call` newline-delimited JSON-RPC responses with a text block holding one JSON object; modes for summarized, cached, bypassed, tool error, malformed line, hang beyond timeout, and stream close (child death)
  - Synthetic request-body fixtures for OpenAI, Anthropic-messages and Ollama dialects with multi-call assistant messages, historical and trailing read results, shell results, and an mmhmem tool result; no real capture content
  - Observable completion: fixtures load in the existing `harness()` pattern and the fake records every call it received
  - _Requirements: 6.1_

- [x] 1.3 Single body parse shared with account-attribution metadata
  - Parse the chat-completion body once in the acquire path and attach metadata on the parsed object, keeping the serialized bytes identical to today when economics is disabled
  - Observable completion: existing metadata and proxy tests pass unchanged, and a new test asserts byte equality of the forwarded body with economics disabled
  - _Requirements: 1.1, 3.6, 6.4_

## 2. Core: policy, state and transport

- [x] 2.1 (P) Eligibility of historical file-read results
  - Correlate each tool result to its tool name and arguments by call identifier; compute the trailing tool group; apply allowlist, string-type and minimum-size rules; extract the source hint from the read header or the path argument
  - Observable completion: tests cover multi-call correlation, both trailing-group shapes, case-insensitive tool names, size threshold, non-string content and the three hint sources, and the function never mutates its input
  - _Boundary: Eligibility_
  - _Requirements: 2.1, 2.2, 2.3, 2.6_

- [x] 2.2 (P) Bounded memo of outcomes
  - Least-recently-used store keyed by content hash holding summary, bypass or failure outcomes with timestamps and a back-off check
  - Observable completion: tests show eviction at the configured maximum, LRU touch on read, byte-identical summaries across repeated reads, and back-off expiry
  - _Boundary: Memo_
  - _Requirements: 3.3, 4.3, 4.4_

- [x] 2.3 (P) Minimal stdio JSON-RPC client with child supervision
  - Spawn `[bin, "stdio"]` with injectable reader/writer streams; newline-delimited JSON-RPC initialize, initialized and tools/call; responses matched by id; sanitized errors without content; on child exit fail in-flight calls fast and restart with capped exponential back-off (1 s → 30 s); graceful shutdown closes stdin and waits before terminate
  - Observable completion: tests against the injected-stream fake show a successful round trip, correct handshake ordering, tool errors and protocol errors surfacing as one error type whose message contains neither content nor paths, EOF mid-request surfacing as termination with a scheduled restart, and the spawn path never executing in tests
  - _Boundary: StdioMcpClient_
  - _Requirements: 4.2, 4.3, 4.6, 1.3_

- [x] 2.4 Background warm-up queue
  - Schedule one fetch per unknown hash under a concurrency bound; store the outcome in the memo; emit one allowlisted warm event per completion or failure; bounded shutdown
  - Observable completion: tests show deduplicated in-flight fetches, the concurrency bound respected, failure memoized with back-off then retried after expiry, and shutdown within the bound while a fetch hangs
  - _Depends: 2.2, 2.3_
  - _Boundary: WarmQueue_
  - _Requirements: 3.4, 4.2, 4.3, 5.4_

- [x] 2.5 Request-path rewrite with marker and counters
  - For each eligible result, replace the content with the memoized rendered text or leave it raw and schedule warm-up; keep role, call identifier and position; compute eligible, replaced, bypassed, pending, bytes and the labelled token estimate; return nothing when no result is eligible
  - Observable completion: tests show replaced content equal to marker plus summary, all other message fields and messages byte-identical, raw relay with a scheduled warm-up on first sight, and no counters when nothing is eligible
  - _Depends: 2.1, 2.2, 2.4_
  - _Boundary: EconShunt_
  - _Requirements: 3.1, 3.2, 3.4, 5.3_

## 3. Integration: proxy wiring and diagnostics

- [x] 3.1 Diagnostics allowlist and econ event fields
  - Add the econ counters and warm-event fields to the per-request diagnostic and to the export allowlist; emit the warm event through the same stdout and export channels
  - Observable completion: diagnostics tests show the fields present only when economics acted, absent otherwise, and the export allowlist rejecting any non-listed key; the existing observability tests pass unchanged
  - _Boundary: RequestDiagnostic, observability allowlist_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 1.4_

- [x] 3.2 Proxy wiring: switch, lifespan, child and Docker
  - Add the command-line switch that fails fast on incomplete configuration; create the economics component in the application lifespan, spawn and supervise the stdio child at startup, and stop it on shutdown; call the rewrite after metadata attachment inside a guard that relays the original body on any exception; skip bodies without tool results; extend the Dockerfile with the pinned mmhmem build stage (`ARG MMHMEM_REF`) and wire `MVIBE_ECON_*`/`AGENT_MEM_*` env and mmhmem volumes into docker-compose.yml
  - Observable completion: full-app tests show the switch exiting non-zero with the missing key named, the upstream request carrying replaced content for all three dialects after warm-up, byte-identical upstream responses, unchanged metadata, raw relay when the component raises, and no child spawned when economics is disabled; the Docker image builds with the mmhmem stage and `docker compose config` validates the new env/volume wiring
  - _Depends: 2.5, 3.1_
  - _Requirements: 1.1, 1.5, 2.4, 3.5, 3.6, 4.1, 4.5, 4.6_

## 4. Validation and guidance

- [x] 4.1 Fail-open and determinism end to end
  - Drive the full app against the fake in error, hang, malformed and bypass modes and assert raw relay, memoized outcomes, back-off and recovery; assert byte-identical replacement across many requests in one process; assert the token and content never appear in any emitted record
  - Observable completion: the econ test module passes offline, and the whole suite passes with the locked offline runner and the verification gate script
  - _Requirements: 4.3, 4.4, 3.3, 1.4, 6.1, 6.2, 6.4_

- [x] 4.2 Operator guide
  - Write the token-economics guide covering configuration keys and defaults, the Docker deployment (pinned mmhmem build arg, child environment, volumes, non-Docker fallback via a binary on PATH), the history-only rule, the marker text, the in-container trust boundary incl. worker egress, prompt-cache interplay, reading econ events, and the manual live check procedure; add the pointer section to the proxy guide and the release notes entry
  - Observable completion: the guide exists, is linked from the proxy guide, and the documented environment example starts the proxy with economics enabled against the embedded child in the live check procedure
  - _Requirements: 6.3_

- [ ]* 4.3 Performance check of the request-path rewrite
  - Measure added latency of the rewrite on a 250 KB synthetic body with 40 eligible results on the reference workstation and record it in the spec's implementation log
  - Observable completion: measured figure recorded; below 5 ms or a follow-up noted
  - _Requirements: 4.1_
