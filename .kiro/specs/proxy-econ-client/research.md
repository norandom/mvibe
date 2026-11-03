# Research & Design Decisions — proxy-econ-client

## Summary
- **Feature**: `proxy-econ-client`
- **Discovery Scope**: Extension (light discovery) of the released v1.0.0 proxy; complex integration with an external daemon (mmhmem).
- **Key Findings**:
  - Tool results are 48.6% of captured request bytes; `read` is 80.7% of tool-result bytes; 387 distinct tool contents were resent 4867 times (mean 12.6, max 165). Savings come from resends, not from the first send.
  - mmhmem's cache is a SQLite table keyed by path, content hash and question fingerprint, not a ring buffer; its security bypass is a path regex; its shunt threshold is 150 lines. A content-based tool is required (companion spec).
  - The proxy already re-serializes chat-completion bodies (`attach_request_metadata`), so request bodies are not byte-exact today; responses are. The acquire closure runs once per request (`mvibe_proxy/streaming.py:150`).

## Research Log

### Where the bytes are (capture analysis, 2026-11-03)
- **Context**: Decide the shunt scope.
- **Sources Consulted**: `reports/http-capture/*/ *-request.json` (1127 captures; 258 with JSON bodies, 733 omitted for size, 136 unavailable without stream read). Script run with `uv run --offline python`.
- **Findings**:

  | Tool | Occurrences | Bytes | Share of tool bytes | Distinct contents |
  |---|---|---|---|---|
  | read | 1983 | 13,918,396 | 80.7% | 141 |
  | shell | 1823 | 1,345,312 | 7.8% | 145 |
  | glob | 382 | 662,315 | 3.8% | 22 |
  | grep | 424 | 483,013 | 2.8% | 27 |
  | others (skill, execute, websearch, webfetch, edit, mmhmem_*, write, question, compress) | | | < 5% combined | |

  OpenCode `read` output starts with `Read file <path>, lines a-b` followed by `N: text` lines. Tool name and arguments live in the preceding assistant message's `tool_calls`, matched by `tool_call_id` (the last assistant message may hold several calls; matching by position is wrong).
- **Implications**: v1 allowlist = `read` only. Shell results are mostly distinct (poor cache hit rate) and lossy to summarize. mmhmem's own tool results appear in captures and must be excluded to avoid summarizing a summary.

### Request path and hook point
- **Context**: Where to transform without touching responses.
- **Sources Consulted**: `proxy.py:193-232` (`make_acquire`), `mvibe_proxy/streaming.py:138-165`, `mvibe_auth/request_metadata.py:77-110`, `mvibe_proxy/compat.py:262-300`.
- **Findings**: All three dialects converge on `make_acquire(path='chat/completions')` with an upstream-shaped body; the Anthropic adapter converts `tool_result` blocks into `role: tool` messages (list contents become a JSON string). `attach_request_metadata` already parses and re-dumps the body. `acquire()` is awaited exactly once per request. `RequestDiagnostic.emit()` and `observability.allowlisted()` enforce a fixed field allowlist.
- **Implications**: Hook after `attach_request_metadata` on the parsed dict (one parse, one dump). Add an `econ` event type and its fields to the allowlist (revalidation trigger for proxy-observability).

### mmhmem MCP transport
- **Context**: Minimal client without adding an MCP SDK dependency.
- **Sources Consulted**: `../mmhmem/src/mcp/server.rs` (rmcp 3.8.0 `StreamableHttpService` at `/mcp`, bearer auth middleware), `../mmhmem/tests/mcp_tools.rs:42-72` (headers used by the integration tests), `../mmhmem/src/mcp/tools.rs` (tools return one text content block).
- **Findings**: POST JSON-RPC to `{url}` with `Authorization: Bearer`, `Accept: application/json, text/event-stream`; responses may be SSE frames (`data: {...}`) or JSON; the server issues `mcp-session-id` on `initialize`, and clients echo it. Sequence: `initialize` → `notifications/initialized` → `tools/call`.
- **Implications**: `mvibe_proxy/econ.py` carries a 60-line JSON-RPC client over the existing `httpx` dependency; it re-initializes on a 404/invalid-session response. No new runtime dependency.

### Provider prompt caching interplay
- **Context**: Replacing content changes the prompt prefix.
- **Sources Consulted**: `.kiro/handover.md` (Mistral `prompt_tokens_details.cached_tokens`), capture statistics above.
- **Findings**: Swapping raw → summary at position k invalidates the provider prefix cache from k onward for one turn. With a mean of 12.6 resends per result this is a net win only if the summary never changes afterwards.
- **Implications**: Requirement 3.3 (byte-identical replacement per process) and the in-process memo. The "history only + async warm" design adds one more raw send (turn N+1) before the summary appears (turn N+2), which costs one extra prefix-cache miss but removes all latency from the request path.

### Consulted skills and guidance
- `kiro-spec-requirements`, `kiro-spec-design`, `kiro-spec-tasks` rules (EARS, boundary-first, task sizing). Steering: `product.md`, `tech.md`, `structure.md`, `authentication.md` (credentials bound to destinations, out of diagnostics).

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|---|---|---|---|---|
| Synchronous shunt on request path | Call mmhmem before forwarding each request | Summary available from first resend | Worker latency (seconds) on every turn with a new result; freshest result summarized → edit-after-read breaks | Rejected |
| History-only, async warm, in-process memo (selected) | Replace only non-trailing results, from memo; warm misses in background | Zero request-path latency; freshest results exact; deterministic replacement | One extra raw send per result; memo lost on restart (one raw send, then mmhmem cache hit) | Selected |
| mmhmem as inline API proxy | mmhmem rewrites bodies itself | Single component | Rejected by mmhmem steering (`tech.md:59`); OAuth/streaming ownership would move | Rejected |
| ACP transport to mmhmem | Editor-agent protocol | None for this path | No client→agent tool call shape | Rejected (handover 2026-11-03) |

## Design Decisions

### Decision: Consume mmhmem over MCP with a minimal in-house JSON-RPC client
- **Context**: No MCP SDK in the Python dependency set; mmhmem speaks Streamable HTTP.
- **Alternatives Considered**: 1. Add the official MCP Python SDK; 2. Minimal client over `httpx`.
- **Selected Approach**: Minimal client (initialize, initialized, tools/call; SSE-or-JSON response parsing; session header echo).
- **Rationale**: One tool, one endpoint, bounded surface; keeps `uv.lock` unchanged.
- **Trade-offs**: Must track rmcp transport behaviour (revalidation trigger).
- **Follow-up**: Verify against a running `mmhmem serve` during implementation; keep the fake endpoint in tests shaped like rmcp's SSE framing.

### Decision: Replace only history; never the trailing tool-result group
- **Context**: Edit-after-read requires exact text; mmhmem's own guidance says summaries are not exact evidence.
- **Selected Approach**: Trailing group (contiguous `role: tool` suffix) is always relayed raw; earlier results are candidates.
- **Rationale**: Preserves correctness of the agent loop; moves worker latency off the request path.
- **Trade-offs**: One additional raw send per result.

### Decision: Memo in process, mmhmem cache across processes
- **Context**: Requirement 3.3 determinism and 4.1 non-blocking.
- **Selected Approach**: Bounded LRU keyed by SHA-256 of the original content, storing summary / bypass / failure-with-timestamp; background warm tasks with a semaphore (default 2) and per-hash dedup.
- **Trade-offs**: Memory for summaries (bounded, default 2000 entries); restart costs one raw send per result.

### Decision: Marker text owned by the proxy, bare summary owned by mmhmem
- **Context**: The model must know it reads a summary; mmhmem must stay client-neutral.
- **Selected Approach**: Fixed first line `[mvibe econ] Summary of an earlier file read (<hint>, <N> lines, sha256:<12 hex>). The exact text was shown earlier in this conversation; read the file again for exact lines.` followed by the summary.
- **Rationale**: Mirrors mmhmem's own "*For edits, call smart_read with offset and limit.*" guidance in the proxy's voice.

### Decision: Security-critical classification stays in mmhmem
- **Context**: mmhmem owns shunt policy (steering: shunt logic lives server-side).
- **Selected Approach**: Proxy sends `source_hint` and content; mmhmem decides bypass (path regex on hint, content secret regex) and returns `bypassed`; proxy memoizes the bypass. Proxy keeps only cheap pre-filters (tool allowlist, size, string type).
- **Trade-offs**: Content leaves the proxy to the daemon before the bypass decision; mitigated by the loopback warning (1.3) and hash-only audit on the mmhmem side.

## Risks & Mitigations
- mmhmem worker pointed at the mvibe proxy (recursion / frontier cost) — worker requests carry no tool-result messages so the hook is inert; documented as unsupported for savings.
- Prefix-cache misses on raw→summary swap — bounded to one per result by determinism; documented.
- Observability allowlist drift — econ fields added to `_ALLOWED_FIELDS` with tests; proxy-observability revalidation trigger recorded.
- Client-side compaction based on returned `usage` — clients see smaller prompts and compact later; harmless, documented.

## References
- `.kiro/handover.md` decision record 2026-11-03 (amended for read-only scope).
- Companion spec: `../mmhmem/.kiro/specs/shunt-content-tool/`.
- MCP Streamable HTTP transport (2025-06-18 revision): https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
