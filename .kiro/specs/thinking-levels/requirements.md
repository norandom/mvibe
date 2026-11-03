# Requirements Document

## Project Description (Input)

Vibe (the reference client in `../mistral-vibe`) lets the user pick a per-model thinking level (`/thinking`, config `thinking` / `thinking_levels`, levels `off/low/medium/high/xhigh/max` plus model-specific words, sent to the provider as written). Through mvibe that selection does not exist today: the `vibe-oidc` OpenCode provider lists every model with `variants: []` (`packages/opencode-vibe-oidc/src/models.ts:59-61`, "No fabricated variants"), and the loopback proxy's Anthropic and Ollama adapters silently drop the client's thinking fields (`thinking` / `think`), so only OpenAI-dialect clients can set `reasoning_effort` by hand. The proxy and plugin should offer the same selectable thinking levels Vibe offers, using the Mistral wire field `reasoning_effort`, without breaking clients that send no level.

## Introduction

The Mistral chat-completions API takes a top-level `reasoning_effort` (`none`, `low`, `medium`, `high`, `xhigh`, `max`; `../mistral-vibe` `completion_profile/reasoning.py:95-113` maps `off` to `none` and sends other levels as written; `adapters/mistral.py:148-151` sets the SDK kwarg from it). mistral-large-4 accepts only `low`, `high`, `max` and rejects `none`; mistral-medium-3.5 accepts `high` and can turn reasoning off; mistral-large-2512/latest rejects every effort (`../mistral-vibe` `completion_profile/known_models.py:77-101`). This feature adds curated thinking-level variants to the `vibe-oidc` provider models and maps the Anthropic/Ollama dialect thinking fields to `reasoning_effort` in the proxy adapters. It changes only request bodies; responses stay byte-exact and no new diagnostics fields are added in v1.

## Boundary Context

- **In scope**: a curated per-model thinking-level table in the OpenCode plugin (mirroring mistral-vibe's known-model rows for Mistral models); `Model.Info.variants` entries whose `settings.reasoningEffort` overlay becomes `reasoning_effort`; proof that `reasoning_effort` survives the installed AI SDK serialization on the direct route; gating of assistant thinking-chunk replay on models that take reasoning input; proxy passthrough of `reasoning_effort` on the OpenAI dialect; Anthropic `thinking` and Ollama `think` mapping in `mvibe_proxy/compat.py`; offline tests; docs.
- **Out of scope**: changing the upstream `/v1/models` payload; inventing levels for models without a known row (unknown models keep `variants: []`); operator-configurable custom levels in v1; token-budget thinking (`thinking.budget_tokens` mapping is reduced to level thresholds, not exact budgets); usage metering; any response/streaming change; any change to the proxy-econ-client spec.
- **Adjacent expectations**: the pinned OpenCode 2.2.5 host applies a selected `Model.Variant`'s `settings` overlay to the SDK request options (`test/tool-compatibility/contracts/opencode-2.2.5.json`, `Model.Variant` at line 24737); the installed `@ai-sdk/openai-compatible` 2.0.88 must pass the field through (to be proven by regression, like the reasoning fix in `test/reasoning.test.ts`); mistral-vibe sends earlier reasoning back only to models that take reasoning input (`takes_reasoning_input`), which the plugin's unconditional thinking-chunk replay currently ignores.

## Requirements

### Requirement 1: Curated thinking-level variants on vibe-oidc models
**Objective:** As an OpenCode user, I want each reasoning-capable `vibe-oidc` model to offer exactly the thinking levels the Mistral API accepts for it, so picking a level in the host sends a request the model takes.

#### Acceptance Criteria
1. When the plugin maps an upstream model, the proxy shall attach `variants` from a curated table keyed by upstream model ID: `mistral-large-4` (and `mistral-large-4-0-8`/`-0` aliases) → `low`, `high`, `max`; `mistral-medium-3.5`/`-3-5`/`mistral-medium-2604`/`mistral-medium-latest` (and magistral/vibe-cli aliases) → `high` plus an `off` variant; `zai-glm-5-3`/`zai-glm-5`/`zai-glm-latest` → `low`, `high`, `max`; models with no known reasoning row (e.g. `mistral-large-2512`, `mistral-large-latest`, small/codestral/voxtral/ministral/deepseek) → `variants: []`.
2. Each variant shall carry exactly `settings: {"reasoningEffort": "<level>"}`, with the `off` variant sending `"none"`; no headers, body or other fields shall be fabricated. (Amended during implementation: OpenCode 2.2.5 merges variant `settings` into the request options for `@ai-sdk/openai-compatible` providers, whose SDK emits the Mistral wire field `reasoning_effort`; variant `body` overlays are dropped on this SDK path — verified against the pinned host, see tasks 2.2.)
3. The curated table shall live in one new plugin module with a source comment naming the mistral-vibe reference rows; adding a model requires a row, never a default level.
4. Operator `modelMetadata` shall remain unable to inject variants, headers or SDK overlays; only the previously allowed metadata fields may cross that boundary.
5. Models absent from the authenticated upstream inventory shall still never appear, regardless of the table.

### Requirement 2: reasoning_effort survives to the wire on the direct route
**Objective:** As a maintainer, I want proof that a selected variant's `reasoning_effort` reaches the Mistral API unchanged through the installed SDK, so the picker works end to end.

#### Acceptance Criteria
1. An installed-SDK regression (same pattern as `test/reasoning.test.ts`) shall show a body containing `reasoning_effort` arriving at the fake transport byte-identical, for both `generate` and `stream`.
2. A host-integration test with the pinned 2.2.5 host shall show a selected variant's `settings` overlay reaching the outgoing request as `reasoning_effort` (host merges the setting into request options; the SDK emits the wire field).
3. When no variant is selected, no `reasoning_effort` field shall be added by the plugin.

### Requirement 3: Thinking-chunk replay is gated by model support
**Objective:** As a plugin user, I want assistant thinking chunks replayed only for models that take reasoning input, matching mistral-vibe's `takes_reasoning_input`, so switching to a non-reasoning model does not fail.

#### Acceptance Criteria
1. When the selected model/level has no reasoning support (no variants, or level `off`/`none`), `mistralRequestBody` shall strip assistant `reasoning_content` instead of replaying it as thinking chunks.
2. When reasoning is active, the existing thinking-chunk replay behavior shall be unchanged.
3. Tests shall cover both arms plus a tool-call history, mirroring the existing reasoning regression.

### Requirement 4: Proxy passthrough of reasoning_effort (OpenAI dialect)
**Objective:** As an OpenAI-dialect client of the proxy, I want my top-level `reasoning_effort` forwarded verbatim, as today.

#### Acceptance Criteria
1. The proxy shall forward any top-level string field `reasoning_effort` in `/v1/chat/completions` unchanged, for stream and non-stream.
2. A regression test shall assert the forwarded body is field-identical to the received body when no adapter rewrite is needed (metadata attachment already re-dumps JSON; unknown fields must survive it).
3. Responses shall remain byte-exact passthrough; this requirement touches no response path.

### Requirement 5: Anthropic adapter maps `thinking` to `reasoning_effort`
**Objective:** As an Anthropic-dialect client of the proxy, I want my `thinking` parameter translated to the upstream `reasoning_effort` instead of being dropped.

#### Acceptance Criteria
1. When the Anthropic messages body contains `thinking: {"type": "disabled"}`, the upstream body shall carry `reasoning_effort: "none"`.
2. When it contains `thinking: {"type": "enabled"}` without `effort`, the upstream level shall be derived from `budget_tokens` using the thresholds 4096/16384/32000 (low/medium/high), defaulting to `high` when absent.
3. When it contains a non-standard `effort` string, the level shall be sent as written.
4. When `thinking` is absent or malformed, no `reasoning_effort` shall be added and the request shall otherwise succeed.
5. The mapping shall be covered by unit tests in `tests/test_compat.py` style; upstream errors for unsupported levels (e.g. `none` on mistral-large-4) shall be relayed, not rewritten.

### Requirement 6: Ollama adapter maps `think` to `reasoning_effort`
**Objective:** As an Ollama-dialect client of the proxy, I want my `think` parameter translated instead of dropped.

#### Acceptance Criteria
1. `think: false` shall map to `reasoning_effort: "none"`; `think: "low"`, `"medium"`, `"high"` shall map as written; `think: true` shall add no field (server default); any other value shall be ignored without failing the request.
2. The mapping applies to both `/api/chat` and `/api/generate` adapter paths.
3. Unit tests shall cover each arm; responses are untouched.

### Requirement 7: Offline verification and docs
**Objective:** As a maintainer, I want the feature verified offline against the pinned host/SDK pair, and operators able to discover the levels.

#### Acceptance Criteria
1. `bun --no-install test` in `packages/opencode-vibe-oidc` and `uv run --locked --offline -- pytest -q` shall pass with the new tests.
2. `docs/opencode-provider.md` shall document the offered levels per model and how to pick one (host variant picker), and `docs/proxy-guide.md` shall document `reasoning_effort` (OpenAI), `thinking` (Anthropic) and `think` (Ollama) support.
3. The pre-existing suites shall pass unchanged apart from intended adapter-field additions.
