# Implementation log — proxy-endpoint-compatibility (2026-11-02)

Requirements, design and tasks approved same day (task-graph sanity review:
PASS, independent subagent). All offline tests run without network; live smoke
ran against the real proxy with the default existing-vibe credential.

| Task | What landed | Evidence |
| --- | --- | --- |
| 1.1, 2.1 | `mvibe_proxy/compat.py`: pure translators — Ollama version/tags/chat/generate/show/embed request+response; Anthropic messages request (system/blocks/tool_use/tool_result/tools conversion) + response (content blocks, stop_reason, usage) + paginated models list | `tests/test_compat.py` fixtures, 16 passed |
| 1.2, 2.2 | Stream translators: `OllamaStreamTranslator` (upstream SSE → NDJSON `done` frames), `AnthropicStreamTranslator` (→ message_start/content_block_*/message_delta/message_stop); `adapter_error` shapes with curated hints; `BufferJson` non-stream transform | pinned frame-sequence tests; duplicate-tail regression caught live and fixed (`_done_sent` guard, count assertion added) |
| 3.1 | `mvibe_proxy/streaming.py`: optional `transform` hook on `_OwnedResponse`/`_stream_body` (feed/finish); default None keeps raw passthrough byte-exact | all pre-existing streaming tests green unchanged |
| 3.2 | `proxy.py`: shared `make_acquire` factory; routes `GET /api/version`, `GET /api/tags`, `POST /api/chat`, `/api/generate`, `/api/show` (404 on unknown model before streaming), `/api/embed`, `POST /v1/messages`; `/api/{path}` catch-all → Ollama-shaped 404; `GET /v1/models` dialect switch on `anthropic-version` header; OpenAPI extras list all new operations | integration tests through mock upstream; `/openapi.json` paths asserted |
| 4.1 | `tests/test_compat.py` — 16 offline contract tests (translation fixtures, streaming sequences, error mapping without upstream leakage, dialect switch, 404/405/400, diagnostics correlation + no secrets) | 16 passed; full suite 1212 passed |
| 4.2 | `docs/proxy-guide.md` — adapter endpoint table + live smoke curl commands | exercised in 4.3 |
| 4.3 | Live smoke via `./start_proxy.ps1 --port 8799` (existing-vibe): `/api/version`, `/api/tags` (64 models), `/api/chat` → `"content": "OK"`, `/api/generate` → `"response": "OK"`, `/api/chat` stream NDJSON done-frames, `/v1/messages` → Anthropic `message` with `stop_reason: end_turn`, `/v1/messages` stream SSE event sequence, `/v1/models` both dialects (64 each), `/api/nope` → 404. Full gate `scripts/verify.ps1` | smoke log 2026-11-02; acceptance.json run id recorded in spec.json |

Notes:

- One real bug found by the live smoke: the Anthropic stream tail was emitted
  twice (once on `[DONE]`, once on flush) — fixed with a `_done_sent` guard and
  a regression assertion.
- Adapter bodies are request payloads: diagnostics stay allowlisted; only the
  model id is exported.
- Full gate run `0247e3c8b56a447ca3beeb90dacc0c92` (2026-11-02): 1212 Python +
  1050 Bun tests green; only the timing-flaky `bridge-process` step failed
  (criteria 12.9/12.10) — it passed on targeted re-run and in run
  `0a518ae9083146e3863c3ce150de4977` with the same tree, so flake, not regression.
  Pre-existing unverified live/documentary criteria 9.4-9.6 remain.
