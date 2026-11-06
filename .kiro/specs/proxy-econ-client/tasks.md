# Implementation Plan — proxy-econ-client

All tasks are required unless marked `- [ ]*`. Sub-tasks are sized for 1–3 hours. File ownership follows the design's File Structure Plan. The companion mmhmem spec `shunt-content-tool` supplies the real endpoint; every task here is verifiable against the in-process fake, so the two specs can be implemented in separate sessions. Amendment 1 (2026-11-06): transport is the embedded `mmhmem stdio` child over pipes; see `docker-stdio-amendment.md`. Task-graph sanity review: `inline` (no fresh subagent in the authoring session), verdict PASS.

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

- [ ] 2.2 (P) Bounded memo of outcomes
  - Least-recently-used store keyed by content hash holding summary, bypass or failure outcomes with timestamps and a back-off check
  - Observable completion: tests show eviction at the configured maximum, LRU touch on read, byte-identical summaries across repeated reads, and back-off expiry
  - _Boundary: Memo_
  - _Requirements: 3.3, 4.3, 4.4_

- [ ] 2.3 (P) Minimal stdio JSON-RPC client with child supervision
  - Spawn `[bin, "stdio"]` with injectable reader/writer streams; newline-delimited JSON-RPC initialize, initialized and tools/call; responses matched by id; sanitized errors without content; on child exit fail in-flight calls fast and restart with capped exponential back-off (1 s → 30 s); graceful shutdown closes stdin and waits before terminate
  - Observable completion: tests against the injected-stream fake show a successful round trip, correct handshake ordering, tool errors and protocol errors surfacing as one error type whose message contains neither content nor paths, EOF mid-request surfacing as termination with a scheduled restart, and the spawn path never executing in tests
  - _Boundary: StdioMcpClient_
  - _Requirements: 4.2, 4.3, 4.6, 1.3_

- [ ] 2.4 Background warm-up queue
  - Schedule one fetch per unknown hash under a concurrency bound; store the outcome in the memo; emit one allowlisted warm event per completion or failure; bounded shutdown
  - Observable completion: tests show deduplicated in-flight fetches, the concurrency bound respected, failure memoized with back-off then retried after expiry, and shutdown within the bound while a fetch hangs
  - _Depends: 2.2, 2.3_
  - _Boundary: WarmQueue_
  - _Requirements: 3.4, 4.2, 4.3, 5.4_

- [ ] 2.5 Request-path rewrite with marker and counters
  - For each eligible result, replace the content with the memoized rendered text or leave it raw and schedule warm-up; keep role, call identifier and position; compute eligible, replaced, bypassed, pending, bytes and the labelled token estimate; return nothing when no result is eligible
  - Observable completion: tests show replaced content equal to marker plus summary, all other message fields and messages byte-identical, raw relay with a scheduled warm-up on first sight, and no counters when nothing is eligible
  - _Depends: 2.1, 2.2, 2.4_
  - _Boundary: EconShunt_
  - _Requirements: 3.1, 3.2, 3.4, 5.3_

## 3. Integration: proxy wiring and diagnostics

- [ ] 3.1 Diagnostics allowlist and econ event fields
  - Add the econ counters and warm-event fields to the per-request diagnostic and to the export allowlist; emit the warm event through the same stdout and export channels
  - Observable completion: diagnostics tests show the fields present only when economics acted, absent otherwise, and the export allowlist rejecting any non-listed key; the existing observability tests pass unchanged
  - _Boundary: RequestDiagnostic, observability allowlist_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 1.4_

- [ ] 3.2 Proxy wiring: switch, lifespan, child and Docker
  - Add the command-line switch that fails fast on incomplete configuration; create the economics component in the application lifespan, spawn and supervise the stdio child at startup, and stop it on shutdown; call the rewrite after metadata attachment inside a guard that relays the original body on any exception; skip bodies without tool results; extend the Dockerfile with the pinned mmhmem build stage (`ARG MMHMEM_REF`) and wire `MVIBE_ECON_*`/`AGENT_MEM_*` env and mmhmem volumes into docker-compose.yml
  - Observable completion: full-app tests show the switch exiting non-zero with the missing key named, the upstream request carrying replaced content for all three dialects after warm-up, byte-identical upstream responses, unchanged metadata, raw relay when the component raises, and no child spawned when economics is disabled; the Docker image builds with the mmhmem stage and `docker compose config` validates the new env/volume wiring
  - _Depends: 2.5, 3.1_
  - _Requirements: 1.1, 1.5, 2.4, 3.5, 3.6, 4.1, 4.5, 4.6_

## 4. Validation and guidance

- [ ] 4.1 Fail-open and determinism end to end
  - Drive the full app against the fake in error, hang, malformed and bypass modes and assert raw relay, memoized outcomes, back-off and recovery; assert byte-identical replacement across many requests in one process; assert the token and content never appear in any emitted record
  - Observable completion: the econ test module passes offline, and the whole suite passes with the locked offline runner and the verification gate script
  - _Requirements: 4.3, 4.4, 3.3, 1.4, 6.1, 6.2, 6.4_

- [ ] 4.2 Operator guide
  - Write the token-economics guide covering configuration keys and defaults, the Docker deployment (pinned mmhmem build arg, child environment, volumes, non-Docker fallback via a binary on PATH), the history-only rule, the marker text, the in-container trust boundary incl. worker egress, prompt-cache interplay, reading econ events, and the manual live check procedure; add the pointer section to the proxy guide and the release notes entry
  - Observable completion: the guide exists, is linked from the proxy guide, and the documented environment example starts the proxy with economics enabled against the embedded child in the live check procedure
  - _Requirements: 6.3_

- [ ]* 4.3 Performance check of the request-path rewrite
  - Measure added latency of the rewrite on a 250 KB synthetic body with 40 eligible results on the reference workstation and record it in the spec's implementation log
  - Observable completion: measured figure recorded; below 5 ms or a follow-up noted
  - _Requirements: 4.1_
