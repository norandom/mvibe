# Research & Design Decisions — proxy-endpoint-compatibility

## Summary

- **Feature**: proxy-endpoint-compatibility (extension)
- **Discovery Scope**: Extension (light) — adapters translate at the edge; the raw `/v1/*` passthrough, auth, streaming ownership and diagnostics stay untouched.
- **Key Findings**:
  - Existing route core (`proxy.py` `acquire()` + `ProxyStreamOwner.response`) already owns auth snapshot, upstream send and stream retirement; adapters need a body transform on the way in and a byte-stream transform on the way out.
  - `/v1/models` path is shared by OpenAI and Anthropic dialects → switch response shape on the `anthropic-version` request header (Anthropic clients always send it), keeping OpenAI shape otherwise.
  - Ollama streaming is NDJSON (`{"done":false,...}` lines), Anthropic streaming is SSE with named event types — both need incremental translators over upstream SSE `data:` frames.
  - Privacy rule from steering: translated payloads are still request bodies — diagnostics stay allowlisted; only the model id may appear.

## Research Log

### Adapter surface verification (2026-11-02)

- **Sources**: Ollama API docs (api/tags, api/chat, api/generate, api/show, api/embed, api/version shapes; NDJSON streaming with `done`), Anthropic Messages API (`POST /v1/messages`, content blocks, tool_use/tool_result, SSE events message_start/content_block_*/message_delta/message_stop, `anthropic-version` header, `GET /v1/models` paginated `{data, has_more, first_id, last_id}`), OpenObserve not involved here.
- **Findings**: field mappings are stable and well-documented; no auth on either client dialect (matches the proxy's no-caller-auth model).
- **Implications**: pure-function translators in one new module are sufficient; no new dependencies.

## Design Decisions

### Decision: one `mvibe_proxy/compat.py` module of pure translators + thin routes in `proxy.py`
- **Alternatives**: separate apps/middleware per dialect (rejected: duplicates auth/stream ownership), or translate inside the catch-all (rejected: raw passthrough must stay byte-exact).
- **Selected**: `compat.py` holds `ollama_*`/`anthropic_*` request/response/stream translators (pure, unit-testable); `proxy.py` registers `/api/*` and `/v1/messages` routes that call the shared `acquire()` with a transformed body and wrap the owned response with a stream transform.
- **Trade-offs**: streaming transform needs a small buffer in the owned-response path — accepted, bounded by SSE line size.

### Decision: dialect switch on `anthropic-version` header for `GET /v1/models`
- Keeps one path for both dialects; OpenAI clients never send that header.

### Decision: error mapping without upstream payload leakage
- Adapter errors are fixed-shape `{error: ...}` (Ollama) / `{type:'error', error:{type,message}}` (Anthropic) with curated hints; upstream bodies never forwarded into adapter error shapes.

## Risks & Mitigations

- Stream translator state across chunk boundaries → buffer partial lines, flush on final chunk; tests pin frame sequences.
- Tool-call translation (Ollama `message.tool_calls`, Anthropic `tool_use` blocks) → map to/from OpenAI `tool_calls`; pinned fixtures.
- Scope creep into Ollama management endpoints → out of scope; unknown `/api/*` returns safe 404.

## References

- Ollama API: https://github.com/ollama/ollama/blob/main/docs/api.md
- Anthropic Messages API: https://docs.anthropic.com/en/api/messages
- Internal: `.kiro/specs/proxy-endpoint-compatibility/brief.md`, `requirements.md`, `design.md`; `proxy.py`, `mvibe_proxy/streaming.py`
