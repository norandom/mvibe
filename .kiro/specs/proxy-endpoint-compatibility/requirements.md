# Requirements Document

## Project Description (Input)

Local tools and agents speak different API dialects. The proxy currently exposes only OpenAI-compatible `/v1/*`. Add Ollama-compatible (`/api/*`) and Anthropic-compatible (`/v1/messages`) adapter endpoints that translate to the existing upstream passthrough, so Ollama/Anthropic clients work against the same Vibe credential with the same auth flow, diagnostics and privacy rules. See brief.md.

## Introduction

The adapter layer is additive: raw `/v1/models`, `/v1/chat/completions` and `/v1/embeddings` passthrough stays untouched. Adapters translate at the edge and reuse the same per-request credential resolution, streaming ownership and safe diagnostics. No caller authentication is added; the loopback default and `--host` warning behavior are unchanged.

## Boundary Context

- **In scope**: Ollama `/api/version`, `/api/tags`, `/api/chat`, `/api/generate`, `/api/show`, `/api/embed`; Anthropic `/v1/messages` (non-streaming and SSE streaming) and `/v1/models` listing; request/response/stream translation both ways; error mapping; OpenAPI documentation of the new operations; offline contract tests and a live smoke procedure.
- **Out of scope**: Responses API, search, tool/MCP execution (the agent executes tools), pull/manage operations (`/api/pull`, `/api/delete`, `/api/copy`, `/api/create`, `/api/blobs`), Ollama `/api/ps`/`/api/ollama` internals beyond version, changes to auth or credential storage, caller authentication.
- **Adjacent expectations**: upstream Mistral API remains the only inference backend; the direct OpenCode plugin is unaffected; diagnostics keep the allowlisted schema (adapter routes emit the same event shape with `phase`/`outcome`).

## Requirements

### Requirement 1: Ollama discovery endpoints

**Objective:** As an Ollama client, I want `/api/version` and `/api/tags` to work, so that model listing tools see the account's models.

#### Acceptance Criteria

1. When a client requests `GET /api/version`, the proxy shall respond with an Ollama-shaped version object.
2. When a client requests `GET /api/tags`, the proxy shall fetch `/v1/models` upstream and respond with an Ollama-shaped `models` array mapping each upstream model to `name`/`model` plus size/modified metadata where derivable.
3. If the upstream model listing fails, the proxy shall return an Ollama-shaped error object with a safe message and the corresponding HTTP status.

### Requirement 2: Ollama chat and generate

**Objective:** As an Ollama client, I want `POST /api/chat` and `POST /api/generate` to run against the Vibe credential, including streaming and tool calls.

#### Acceptance Criteria

1. When a client posts to `/api/chat`, the proxy shall translate `messages`, `options`, `tools` and `stream` into an upstream chat-completion request and return the upstream result in Ollama response shape (`message`, `done`, usage fields) when `stream` is false.
2. When `stream` is true, the proxy shall translate upstream SSE deltas into Ollama NDJSON frames (`done: false` … `done: true`), preserving tool-call deltas.
3. When a client posts to `/api/generate`, the proxy shall wrap `prompt` (plus `system`) as a chat conversation and translate the response to the Ollama generate shape (`response`, `done`).
4. If the upstream returns an error status, the proxy shall map it to an Ollama-shaped error body without leaking upstream payloads.

### Requirement 3: Ollama show and embed

**Objective:** As an Ollama client, I want `/api/show` and `/api/embed` to answer from upstream data.

#### Acceptance Criteria

1. When a client posts to `/api/show`, the proxy shall return an Ollama-shaped model info object derived from the upstream model entry.
2. When a client posts to `/api/embed`, the proxy shall map `input` to an upstream embeddings request and return an Ollama-shaped `embeddings` array.
3. When the requested model is unknown upstream, the proxy shall return a safe not-found error in the adapter's shape.

### Requirement 4: Anthropic messages endpoint

**Objective:** As an Anthropic client, I want `POST /v1/messages` to work, non-streaming and streaming, including system prompt and tool use.

#### Acceptance Criteria

1. When a client posts to `/v1/messages`, the proxy shall translate `system`, `messages`, `tools`, `tool_choice`, `max_tokens` and `stream` into an upstream chat-completion request.
2. When `stream` is false, the proxy shall return an Anthropic-shaped message object (`id`, `type`, `role`, `content` blocks, `stop_reason`, `usage`).
3. When `stream` is true, the proxy shall translate upstream SSE deltas into Anthropic SSE event types (`message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`).
4. When a client requests `GET /v1/models` in Anthropic dialect (paginated `data` list), the proxy shall serve the upstream model list in Anthropic list shape; the OpenAI-shaped `/v1/models` behavior is unchanged.
5. If the upstream returns an error status, the proxy shall map it to an Anthropic-shaped error object with a safe message and matching status class.

### Requirement 5: Shared proxy behavior and safety

**Objective:** As an operator, I want adapter routes to behave like the rest of the proxy: same auth, same diagnostics, same privacy, same bind defaults.

#### Acceptance Criteria

1. Adapter routes shall use the same per-request credential resolution as `/v1/*` (default existing-vibe; explicit `--auth-source oidc`), with no caller authentication added.
2. Adapter routes shall emit the same allowlisted diagnostic events (correlation ID, phase, outcome, status, hint) and shall be exportable like other routes.
3. Adapter routes shall never log or export request bodies, translated or original.
4. Unknown `/api/*` paths shall return a safe 404 in the adapter's error shape; wrong methods shall return 405.
5. The proxy shall keep binding to `127.0.0.1` by default, with the same `--host 0.0.0.0` warning.

### Requirement 6: Documentation and verification

**Objective:** As an operator or client developer, I want the new endpoints documented and verifiable.

#### Acceptance Criteria

1. The OpenAPI schema (`/openapi.json`) and Swagger UI (`/docs`) shall list the new operations with adapter-shaped request/response schemas.
2. Offline contract tests shall pin translation fixtures for each adapter route, including streaming frame sequences and error mapping, without network access.
3. The operator guide shall include a live smoke procedure (start proxy, curl each new endpoint with a small prompt) and expected success shapes.
