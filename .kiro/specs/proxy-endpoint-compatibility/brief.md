# Proxy endpoint compatibility: Ollama / Anthropic adapters

## User intent (2026-11-02)

Can the local proxy be seen as an Ollama-compatible endpoint, an OpenAI- and
an Anthropic-compatible one? Can OpenCode and Swagger be added?

## Current state (verified 2026-11-02)

- **OpenAI-compatible: yes, already.** `GET /v1/models`,
  `POST /v1/chat/completions` (incl. streaming SSE and tool-call payloads),
  `POST /v1/embeddings`; raw bodies are forwarded without local validation.
- **OpenCode as consumer: yes, already.** Point any OpenAI-compatible client at
  `baseURL http://127.0.0.1:8765/v1` with placeholder `apiKey: "local"`
  (e.g. `providers.mistral` in opencode.json; live-tested on OpenCode 2.2.5,
  see docs/proxy-guide.md). The direct `vibe-oidc` plugin bypasses the proxy.
- **Swagger / OpenAPI: yes, already.** FastAPI built-ins `/docs` (Swagger UI)
  and `/openapi.json`; now covered by a regression test in
  tests/test_proxy.py and re-verified live.
- **Ollama-compatible: no.** `/api/chat`, `/api/generate`, `/api/tags`,
  `/api/show`, `/api/version`, `/api/embed` are not implemented; only the
  three `/v1` paths are proxied (the catch-all returns 404 locally).
- **Anthropic-compatible: no.** `/v1/messages` with the Anthropic schema and
  Anthropic SSE event types (`message_start`, `content_block_delta`, ...) is
  not implemented.

## Intended direction

Add a translation layer in front of the existing upstream passthrough so local
tools that speak Ollama or Anthropic can use the same Vibe credential:

- Ollama adapter: `/api/tags` maps to `/v1/models`, `/api/chat` and
  `/api/generate` map to `/v1/chat/completions` with `messages`/`prompt`
  conversion, streaming `{"done":...}` frames mapped from SSE deltas, tool
  calls mapped to Ollama `tool_calls`, `/api/show` from model metadata.
- Anthropic adapter: `/v1/messages` maps to chat completions; request
  (`system`, `messages`, `tools`, `max_tokens` required) and response
  (`content` blocks, `stop_reason`, usage) converted both ways; SSE event
  stream translated to Anthropic event types.
- Keep raw `/v1/*` passthrough untouched; adapters are additive routes.
- Same auth flow as today (default `existing-vibe` per request, explicit
  `--auth-source oidc`), same no-caller-auth loopback default, same
  `--host`/`--port`/`--log` CLI, same safe-diagnostics rules: no credentials,
  prompts or tool arguments in logs; upstream statuses preserved.
- Offline contract tests with pinned Ollama/Anthropic fixtures per route and
  streaming shape; live smoke only for one small chat per protocol.

## Scope and acceptance direction

Requirements/design/tasks are future phases. Out of scope: Responses API,
search endpoints, tool/MCP execution (the agent executes tools), embeddings
for Ollama (`/api/embed`) unless cheap to map to `/v1/embeddings`.
