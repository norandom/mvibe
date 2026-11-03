# Requirements Document

## Project Description (Input)

Agent users routing OpenCode, Claude Code or Ollama-dialect clients through the mvibe proxy resend the full conversation history on every turn. Captured traffic (258 request bodies, 2026-11-02) shows tool results make up 48.6% of request bytes, the `read` tool alone 80.7% of that, and each distinct read result is resent about 14 times. Today the proxy relays those bytes unchanged, so every resend costs frontier prompt tokens. The proxy should, when an operator opts in, replace *historical* file-read results with deterministic structural summaries obtained from an mmhmem daemon over its MCP interface, while clients stay unaware, responses stay byte-exact, the freshest results stay exact, and nothing ever breaks inference.

## Introduction

The proxy is the only component that sees every resend of a tool result across turns, which makes it the natural shunt point for token economics. It does not execute tools and does not own a worker model; it consumes mmhmem's new content-based `shunt_content` tool (companion spec `shunt-content-tool` in the mmhmem repository) as an external service. The feature is off by default and fail-open: any failure in the economics path yields the unmodified request.

Decision record 2026-11-03 (`.kiro/handover.md`): consume mmhmem over MCP, no implementation copy in mvibe; v1 scope is file-read results only, since shell, grep and glob results are small or rarely repeated and summarizing command output loses exit codes and error lines the model acts on.

## Boundary Context

- **In scope**: opt-in configuration; identification of file-read tool results inside chat-completion request bodies for all three dialects (OpenAI, Anthropic messages, Ollama); the "history only" rule (never the trailing, freshest results); deterministic replacement text with an explicit summary marker; a minimal MCP client to one mmhmem daemon; asynchronous warm-up so the request path never waits on mmhmem; bounded in-process memo for byte-identical repeats; allowlisted `econ` diagnostics; offline tests; operator documentation.
- **Out of scope**: summarizing shell, grep, glob, web or MCP tool results (a configurable allowlist exists, but only `read` is supported and tested in v1); any change to responses or streaming; executing tools; running or configuring the worker model; usage metering and budget caps (separate, unspecced); changes to mmhmem's path-based `smart_read`; the security-critical classification itself (owned by mmhmem, see adjacent expectations).
- **Adjacent expectations**: mmhmem exposes `shunt_content` with the contract in the companion spec (deterministic summaries for identical content, security-critical and below-threshold bypass decided server-side, hash-only audit, worker-unconfigured and timeout reported as tool errors). The proxy-observability event allowlist gains one event type; the shared auth library is untouched. Clients keep their own full history and may compact it themselves; the proxy never tells them what the model saw.

## Requirements

### Requirement 1: Opt-in configuration and safe defaults
**Objective:** As a proxy operator, I want token economics to be off unless I configure it, so that existing deployments behave exactly as in v1.0.0.

#### Acceptance Criteria
1. While no economics endpoint is configured, the proxy shall relay chat-completion request bodies exactly as it does today and shall make no outbound call other than the upstream inference call.
2. When an economics endpoint, token, minimum size, timeout and tool allowlist are configured through the environment, the proxy shall validate them at startup and shall refuse to enable economics with an actionable message if the endpoint or token is missing or malformed.
3. If the configured endpoint is not a loopback address, the proxy shall print a startup warning stating that tool-result contents will leave the proxy process to that address.
4. The proxy shall never place the economics token, the endpoint credentials, or any tool-result content in diagnostics, exported telemetry, or error responses.
5. Where an explicit command-line switch requests economics and the configuration is incomplete, the proxy shall exit with a non-zero status and a message naming the missing setting.

### Requirement 2: Identification of eligible file-read results
**Objective:** As an agent user, I want only historical file-read results to be candidates for summarization, so that the model always sees exact text when it is about to act on it.

#### Acceptance Criteria
1. When a chat-completion request contains tool-result messages, the proxy shall determine each result's originating tool name by matching the result's call identifier against the preceding assistant tool calls in the same request.
2. The proxy shall treat a tool result as eligible only if its tool name is in the configured allowlist (default: the file-read tool only, matched case-insensitively), its content is a plain string, and its size is at or above the configured minimum.
3. The proxy shall never treat a result in the trailing group of the conversation as eligible, where the trailing group is the contiguous run of tool-result messages at the end of the message list.
4. When a request arrives through the Anthropic-messages or Ollama adapter, the proxy shall apply the same eligibility rules to the translated upstream-shaped body, so all three dialects benefit equally.
5. The proxy shall never treat results that originate from mmhmem's own tools, from editing or writing tools, or from shell execution as eligible in v1, even if an operator adds them to the allowlist, and shall log one startup warning naming the ignored entries.
6. When the first line of an eligible result names the file and line range that was read, the proxy shall extract that as a source hint; otherwise it shall use the file path argument of the originating tool call if present; otherwise no hint.

### Requirement 3: Deterministic, client-transparent replacement
**Objective:** As an agent user, I want summaries to be stable and clearly marked, so that the provider prompt cache keeps working and the model knows it is reading a summary.

#### Acceptance Criteria
1. When a summary for an eligible result is available in the proxy's memo, the proxy shall replace only that result's content, keeping the message role, call identifier, position and every other field unchanged.
2. The replacement content shall begin with a fixed marker line stating that it is a proxy-generated summary of an earlier file read, naming the source hint when known, the original line count and a short content hash, and stating that the exact text can be obtained by reading the file again.
3. While the proxy process runs, the proxy shall produce byte-identical replacement content for the same original content on every request.
4. If a result is eligible but no summary is memoized yet, the proxy shall relay the original content unchanged in this request and shall schedule a background warm-up for it.
5. The proxy shall relay every response, streamed or not, byte-exact as today; economics never touches the response path.
6. When economics is active, the metadata attached to the request for account attribution shall remain exactly as without economics.

### Requirement 4: Non-blocking mmhmem interaction and fail-open behavior
**Objective:** As an agent user, I want zero added latency on my turns and no new failure mode, so that enabling economics can never make inference slower or break it.

#### Acceptance Criteria
1. The proxy shall never await an mmhmem call on the request path; summaries are only ever taken from the in-process memo.
2. When a background warm-up runs, the proxy shall call mmhmem's content tool once per distinct content hash, with bounded concurrency, and shall record the returned summary or bypass decision in the memo.
3. If mmhmem is unreachable, times out, returns an error, or reports a bypass, the proxy shall keep relaying the original content, shall record the outcome in the memo so it is not retried on every request, and shall retry a failed hash no sooner than a configured back-off.
4. The memo shall be bounded in entries and shall evict least-recently-used entries; eviction shall only cause one further raw relay before re-warm, never an error.
5. If the economics component raises any exception, the proxy shall log an allowlisted diagnostic and relay the original request unchanged.
6. The proxy shall never call its own routes to reach mmhmem, and shall not apply economics to requests that contain no tool-result messages.

### Requirement 5: Honest diagnostics
**Objective:** As an operator, I want to see what economics did per request, so that I can judge savings from recorded events rather than from the fact that a daemon is configured.

#### Acceptance Criteria
1. When economics processes a request, the proxy shall emit one allowlisted diagnostic event carrying the request identifier, the model, the counts of eligible, replaced, bypassed and pending results, the bytes before and after replacement, and an estimated avoided-token figure labelled as an estimate.
2. The diagnostic shall be emitted through the existing opt-in diagnostics and export channels and shall never include content, hints, file paths or tokens.
3. If no result was eligible, the proxy shall emit no economics event for that request.
4. When a background warm-up completes or fails, the proxy shall emit one allowlisted event with the outcome and latency, without content.

### Requirement 6: Offline verification and operator guidance
**Objective:** As a maintainer, I want the feature verified without a live mmhmem, worker or Mistral, so that the gate stays offline and free.

#### Acceptance Criteria
1. When the test suite runs, economics behavior shall be verified against an in-process fake mmhmem endpoint and the existing proxy harness, without network access, credentials or a worker model.
2. The tests shall cover: off by default, configuration validation, eligibility including the trailing-group rule and all three dialects, byte-identical replacement, raw relay on first sight with warm-up scheduled, fail-open on error and timeout, memo bounds and back-off, diagnostic allowlisting, and response byte-exactness.
3. The documentation shall describe the configuration, the history-only rule, the summary marker, the trust and privacy implications of pointing the endpoint off-loopback, the interaction with provider prompt caching, and how to read the economics events.
4. The pre-existing test suite shall pass unchanged.
