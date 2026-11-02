# Implementation Plan — proxy-endpoint-compatibility

## 1. Core: Ollama translators in `mvibe_proxy/compat.py`

- [x] 1.1 Ollama request/response translators (pure functions)
  - `ollama_version()`, `ollama_tags(upstream_models)`, `ollama_chat_request(body)` (messages, options→temperature/top_p/max_tokens, tools→function tools, stream), `ollama_chat_response(upstream, model)`, `ollama_generate_request/response` (prompt+system wrap), `ollama_show(model_entry)`, `ollama_embed_request/response`; defensive validation → raises `CompatError` on invalid shape
  - Observable completion: `tests/test_compat.py` fixtures assert exact translated upstream bodies and client-facing bodies for chat/generate/show/embed/tags
  - _Boundary: compat.py (Ollama)_
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.3, 3.1, 3.2, 3.3_
- [x] 1.2 Ollama streaming translator
  - `OllamaStreamTranslator`: fed upstream SSE `data:` payloads, buffers partial lines, yields NDJSON `{"done":false,...}` frames per delta (content and tool_calls) and a final `{"done":true,...}` with usage; `flush()` emits the final frame at stream end
  - Observable completion: pinned 3-chunk fixture (text delta, tool_calls delta, final) produces the exact NDJSON sequence
  - _Depends: 1.1_
  - _Boundary: compat.py (Ollama)_
  - _Requirements: 2.2, 2.4_

## 2. Core: Anthropic translators in `mvibe_proxy/compat.py`

- [x] 2.1 Anthropic messages request/response translators
  - `anthropic_messages_request(body)` (system→leading system message; text/tool_use/tool_result blocks→OpenAI messages incl. tool_calls/tool role; tools→function tools; max_tokens required), `anthropic_messages_response(upstream)` (content blocks, stop_reason map, usage map), `anthropic_models_list(upstream)` paginated shape
  - Observable completion: fixtures for system+text+tool_use+tool_result conversation and for the response/stop_reason/usage mapping; models list shape pinned
  - _Boundary: compat.py (Anthropic)_
  - _Requirements: 4.1, 4.2, 4.4_
- [x] 2.2 Anthropic streaming translator + error mapping
  - `AnthropicStreamTranslator`: upstream SSE → `message_start`, `content_block_start/delta/stop`, `message_delta`, `message_stop` events; `adapter_error(dialect, status, hint)` for Ollama `{error}` and Anthropic `{type:'error',error:{...}}` shapes with curated hints only
  - Observable completion: pinned full SSE event sequence fixture; error mapping table tested for 400/401/403/422/429/500
  - _Depends: 2.1_
  - _Boundary: compat.py (Anthropic)_
  - _Requirements: 4.3, 4.5, 2.4, 1.3, 3.3_

## 3. Integration: stream hook + routes

- [x] 3.1 Optional stream transform in `mvibe_proxy/streaming.py`
  - `_OwnedResponse`/`_stream_body` accept `transform=None`; when set, each upstream byte chunk passes through the stateful translator (bounded single-partial-line buffer) before send; default None keeps raw passthrough byte-exact
  - Observable completion: existing streaming tests unchanged and green; new test shows transformed bytes on the wire when a transform is attached
  - _Boundary: ProxyStreamOwner_
  - _Requirements: 2.2, 4.3_
- [x] 3.2 Adapter routes in `proxy.py`
  - Register `GET /api/version`, `GET /api/tags`, `POST /api/chat`, `POST /api/generate`, `POST /api/show`, `POST /api/embed`, `POST /v1/messages`; each uses the shared acquire path (same per-request auth, diagnostics event, X-Request-ID) with translated body and transformed response; `GET /v1/models` returns the Anthropic paginated shape when an `anthropic-version` header is present, OpenAI shape otherwise; unknown `/api/*` → 404 adapter-shaped error, wrong methods → 405; OpenAPI extras document the new operations
  - Observable completion: offline integration tests drive every route through the mock upstream and assert upstream body + client shape + one correlated diagnostic event; `/openapi.json` lists the new paths
  - _Depends: 1.1, 1.2, 2.1, 2.2, 3.1_
  - _Boundary: proxy.py routes_
  - _Requirements: 1.1, 2.1, 2.3, 3.1, 3.2, 4.1, 4.4, 5.1, 5.2, 5.3, 5.4, 6.1_

## 4. Validation

- [x] 4.1 Offline contract suite `tests/test_compat.py`
  - Translator fixtures, integration tests per route, streaming frame sequences, error mapping, dialect switch, diagnostics correlation, no-secrets assertions; all offline via mock transport
  - Observable completion: `uv run --locked --offline -- pytest tests/test_compat.py -q` passes; full suite still green
  - _Depends: 3.2_
  - _Requirements: 6.2, 5.2, 5.3_
- [x] 4.2 Operator smoke section in `docs/proxy-guide.md`
  - curl commands for /api/version, /api/tags, /api/chat, /api/generate (stream and not), /api/show, /api/embed, /v1/messages (anthropic-version header, stream and not), /v1/models both dialects, with expected success shapes
  - Observable completion: doc renders; commands executed live during development
  - _Depends: 3.2_
  - _Requirements: 6.3_
- [x] 4.3 Live smoke + full gate
  - Start the real proxy via `./start_proxy.ps1 --port 8799`, run the smoke curls (small "Reply with exactly OK." prompts), confirm shapes; then `scripts/verify.ps1` full offline gate
  - Observable completion: smoke outputs recorded (smoke log 2026-11-02); verify.ps1 run `0247e3c8b56a447ca3beeb90dacc0c92` wrote acceptance.json — all steps pass except the timing-flaky `bridge-process` (passed on targeted re-run and in run `0a518ae9…` with the same tree)
  - _Depends: 4.1, 4.2_
  - _Requirements: 6.2, 6.3, 5.5_
