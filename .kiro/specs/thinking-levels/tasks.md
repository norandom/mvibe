# Implementation Plan — thinking-levels

All tasks are required unless marked `- [x]*`. Sub-tasks are sized for 1–3 hours. File ownership follows the design's Boundary Commitments. Reference for semantics: `../mistral-vibe` (read-only). Task-graph sanity review: inline, verdict PASS.

## 1. Plugin: curated levels table and variants

- [x] 1.1 New `src/thinking.ts` with the curated table and helpers
  - `KNOWN_THINKING_LEVELS` rows with aliases: large-4 group (`mistral-large-4`, `mistral-large-4-0`, `mistral-large-4-0-8`) → levels `low, high, max`, cannot turn off; medium group (`mistral-medium-3.5`, `mistral-medium-3-5`, `mistral-medium-2604`, `mistral-medium-latest`, `magistral-medium-latest`, `mistral-vibe-cli-latest`, `mistral-vibe-cli-with-tools`) → `high` + off; glm group (`zai-glm-5-3`, `zai-glm-5`, `zai-glm-latest`) → `low, high, max`
  - `variantsFor(modelId)` → `Model.Variant[]` with only `id` + `settings: { reasoningEffort }` (`none` for off); `reasoningSupported(modelId, level?)` helper
  - Comment citing `../mistral-vibe/.../completion_profile/known_models.py:77-101` and the wire mapping
  - Observable completion: `test/thinking.test.ts` asserts the exact table and variant shapes; unknown IDs → `[]`
  - _Requirements: 1.1–1.5_

- [x] 1.2 Attach variants in `src/models.ts`
  - In `mapModel`, replace `variants: []` with `variants: variantsFor(id)`; amend the comment to "variants come only from the curated thinking table; never fabricated"
  - `modelMetadata` boundary unchanged (no variants/headers/settings from operators)
  - Observable completion: `test/models.test.ts` additions pass; operator metadata still cannot inject variants
  - _Depends: 1.1_
  - _Requirements: 1.1–1.5_

## 2. Plugin: wire proof and replay gating

- [x] 2.1 (P) SDK passthrough regression for `reasoning_effort`
  - Installed-SDK test (pattern of `test/reasoning.test.ts`): body with `reasoning_effort` arrives at the fake transport unchanged for `generate` and `stream`
  - If the SDK strips it, extend `transformRequestBody` minimally to re-add it and record the deviation
  - Observable completion: both modes green; no variant selected → no field added
  - _Requirements: 2.1–2.3_

- [x] 2.2 (P) Host applies variant `settings` overlay (v2 merges `settings.reasoningEffort`, not `body`, on the openai-compatible SDK path — verified against pinned host 2.2.5)
  - Host-integration test with the pinned 2.2.5 host: select a variant, assert the outgoing request carries `reasoning_effort`
  - Document the exact variant-selection syntax the host uses (config/CLI) for the docs task
  - Observable completion: test green against `test/host-integration.ts` harness
  - _Requirements: 2.2_

- [x] 2.3 Gate thinking-chunk replay in `src/reasoning.ts`
  - `mistralRequestBody(body, { reasoningSupported })` — strip assistant `reasoning_content` when the model/level has no reasoning support; unchanged otherwise
  - Wire the selected model/level from `src/index.ts` into the SDK hook
  - Observable completion: both arms plus tool-call history green, mirroring `test/reasoning.test.ts`
  - _Depends: 1.1_
  - _Requirements: 3.1–3.3_

## 3. Proxy: passthrough and dialect mappings

- [x] 3.1 (P) OpenAI-dialect `reasoning_effort` passthrough regression
  - `tests/test_proxy.py`: `/v1/chat/completions` with top-level `reasoning_effort` (and one arbitrary extra field) forwarded unchanged, stream and non-stream; response byte-exact
  - Observable completion: tests green with no production-code change (or a minimal fix if the re-dump drops fields)
  - _Requirements: 4.1–4.3_

- [x] 3.2 (P) Anthropic adapter maps `thinking`
  - In `anthropic_messages_request`: `{"type":"disabled"}` → `reasoning_effort: "none"`; `{"type":"enabled"}` → budget thresholds 4096/16384/32000 → `low`/`medium`/`high` (default `high`), non-standard `effort` string as written; absent/malformed → no field
  - Observable completion: `tests/test_compat.py` arms for each case plus malformed; upstream 4xx relayed unchanged
  - _Requirements: 5.1–5.5_

- [x] 3.3 (P) Ollama adapter maps `think`
  - In `_ollama_options` / `ollama_chat_request` / `ollama_generate_request`: `false` → `"none"`; `low|medium|high` as written; `true` → no field; other values ignored
  - Observable completion: unit tests for both adapter paths
  - _Requirements: 6.1–6.3_

## 4. Docs and validation

- [x] 4.1 Documentation
  - `docs/opencode-provider.md`: offered levels per `vibe-oidc` model, how to pick a variant (exact syntax verified in 2.2), `none` rejected by large-4 note
  - `docs/proxy-guide.md`: `reasoning_effort` (OpenAI), `thinking` (Anthropic), `think` (Ollama); release-notes entry
  - Observable completion: docs render, links valid; check script passes if one exists
  - _Requirements: 7.2_

- [x] 4.2 Full offline gate
  - `bun --no-install test`, `bun run typecheck && bun run build` in `packages/opencode-vibe-oidc`; `uv run --locked --offline -- pytest -q`; then `scripts/verify.ps1` full gate
  - Observable completion: all green; pre-existing tests unchanged apart from intended additions
  - _Depends: 1.1–3.3_
  - _Requirements: 7.1, 7.3_

- [ ]* 4.3 Live check (optional, needs account)
  - Real OpenCode 2.2.5 session on `vibe-oidc/mistral-large-4` with variant `high` and `low`; confirm 200s and thinking chunks in the response; `none` expected to 4xx on large-4 (document the observed status)
  - Observable completion: live result recorded in `docs/live-results.md`
  - _Requirements: 2.1–2.3_
