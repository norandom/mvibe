# Research & Design Decisions — thinking-levels

## Summary
- **Feature**: `thinking-levels`
- **Discovery Scope**: Extension of the released v1.0.0 proxy + `opencode-vibe-oidc` plugin; reference behavior taken from the sibling Vibe CLI checkout `../mistral-vibe`.
- **Key Findings**:
  - Vibe offers selectable per-model thinking levels via config `thinking` / `thinking_levels` and the `/thinking` picker; standard levels are `off/low/medium/high/xhigh/max` (`../mistral-vibe/vibe/config_values.py:12-26`); a model's `thinking_levels` narrows the offered set and any word is sent to the provider as written.
  - On the Mistral wire the level is the top-level chat field **`reasoning_effort`**, `off → "none"`, other levels verbatim (`../mistral-vibe/mistralai_vibe_local_harness/vibe/completion_profile/reasoning.py:95-137`, `vibe/adapters/mistral.py:148-151`).
  - Known Mistral rows (`../mistral-vibe/.../completion_profile/known_models.py:77-101`): mistral-large-4 takes only `low/high/max` and **rejects `none`** (cannot turn off); mistral-medium-3.5 takes `high` and can turn off; mistral-large-2512/latest reject every effort; zai-glm-5-3 takes `low/high/max`.
  - Vibe sends earlier reasoning back only to models that take reasoning input (`takes_reasoning_input`, `reasoning.py:190-208`); mvibe's plugin currently replays thinking chunks unconditionally.
  - mvibe gap: `vibe-oidc` models list `variants: []` (`packages/opencode-vibe-oidc/src/models.ts:59-61`, live-verified via the models tool: all 18 models `variants: []`; the host's own `opencode/mistral-large-4` entry offers `none`/`high`); proxy Anthropic adapter drops `thinking` and Ollama adapter drops `think` (`mvibe_proxy/compat.py:297-327`, `:33-43`); the OpenAI dialect relays the body verbatim.

## Research Log

### Reference: how Vibe models thinking levels (2026-11-03)
- **Context**: Copy the level semantics, not the UI.
- **Sources Consulted**: `../mistral-vibe/vibe/config_values.py:12-26`; `vibe/plugins/builtins/vibe/skills/vibe/SKILL.md:312-313`; `../mistral-vibe/CHANGELOG.md:22-173` (level behaviors: off refused on always-reasoning models, xhigh offered only when listed, unknown words sent as written with a config warning, `minimal`→`low` on chat/responses, levels sent as picked never silently changed).
- **Findings**: `THINKING_LEVELS = ("off","low","medium","high","xhigh","max")`; default offered `(off, low, medium, high, max)`; per-model `thinking_levels` wins over catalogue/known table; `thinking` unset → model's lowest level (off where it can stop).
- **Implications**: the plugin's variant table mirrors the *known Mistral rows* only; no per-model free words in v1; unset level = no field sent (server default), matching "no variant selected adds nothing".

### Reference: wire format
- **Context**: Which field carries the level to api.mistral.ai.
- **Sources Consulted**: `../mistral-vibe/mistralai_vibe_local_harness/vibe/adapters/mistral.py:107-151`; `completion_profile/reasoning.py:93-137,232-236`.
- **Findings**: `kwargs["reasoning_effort"] = reasoning_wire_value(WireFormat.MISTRAL, thinking)`; mapping `off→"none"`, others identity; Ollama wire clamps xhigh/max→high (that direction does not apply to us — upstream is Mistral); Anthropic budget models use `THINKING_BUDGET_TOKENS = {low:4096, medium:16384, high:32000, xhigh:32000, max:32000}`.
- **Implications**: plugin variants set `settings: {"reasoningEffort": level}` (the host merges variant *settings* into request options on the openai-compatible SDK path and the SDK emits `reasoning_effort`; variant `body` overlays are dropped there — confirmed empirically against the pinned 2.2.5 host, see design); proxy adapters translate dialect fields to the same upstream field; budget thresholds 4096/16384/32000 reused for Anthropic `budget_tokens`.

### Reference: reasoning replay gating
- **Context**: Whether thinking chunks may be replayed for every model.
- **Sources Consulted**: `../mistral-vibe/.../reasoning.py:190-208` (`takes_reasoning_input`); mvibe `packages/opencode-vibe-oidc/src/reasoning.ts` (replays unconditionally).
- **Findings**: a model known to have no reasoning rejects reasoning input; one with unknown reasoning takes it only when the request asks it to reason.
- **Implications**: Requirement 3 conservatively gates replay on this adapter's curated support table and selected level. Unlike the native client's unknown-model fallback, an explicit effort alone does not establish replay support for an unlisted model in this feature.

### mvibe current state (2026-11-03)
- **Context**: Where levels are dropped today.
- **Sources Consulted**: `packages/opencode-vibe-oidc/src/models.ts:54-62` (`variants: []`, "No fabricated variants"); live `models` tool listing (vibe-oidc: all `variants: []`; opencode provider `mistral-large-4`: `["none","high"]`); `mvibe_proxy/compat.py:33-43` (`_ollama_options` drops `think`), `:297-327` (`anthropic_messages_request` has no `thinking` handling); `proxy.py:295-303` (models relayed verbatim); host contract `packages/opencode-vibe-oidc/test/tool-compatibility/contracts/opencode-2.2.5.json:24737-24760` (`Model.Variant = {id, settings?, headers?, body?}`).
- **Findings**: OpenAI-dialect `reasoning_effort` already passes through (body re-dumped by `attach_request_metadata` preserves fields); the pinned 2.2.5 host merges variant `settings` into the SDK hook options (observed: `reasoningEffort` key present when a variant is selected) and the installed SDK emits it as `reasoning_effort`; variant `body` overlays do NOT reach the request on this path (host-integration test); upstream `packages/core/src/variant.ts` spells effort variants as `settings: { reasoningEffort }` for `@opencode/ai/providers/mistral` and `openai-compatible`.
- **Implications**: proxy work is adapter mapping + passthrough tests; plugin work is the variants table + SDK/host proof + replay gating.

### Consulted skills and guidance
- `kiro-spec-requirements`, `kiro-spec-design`, `kiro-spec-tasks` rules (EARS, boundary-first, task sizing). Steering: `product.md`, `tech.md`, `structure.md`, `authentication.md` (no credentials in diagnostics; proxy executes no tools).

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|---|---|---|---|---|
| Variants with `settings.reasoningEffort` overlay (selected) | Plugin exposes curated variants; host merges the setting into request options; SDK emits `reasoning_effort` | Uses the host's native variant picker; same spelling as the host's own mistral/openai-compatible variants; level travels with model pick like Vibe | `body` overlays are dropped on this SDK path in 2.2.5 (verified); SDK must keep emitting the field | Selected |
| Plugin SDK `transformRequestBody` injecting effort from session state | Read selected level in the hook and inject | Full control | Host already merges variant settings; double mechanism; session-state coupling | Rejected |
| Proxy-only mapping, no plugin variants | Clients set `reasoning_effort` manually | No plugin change | OpenCode users of vibe-oidc have no picker; the reported gap stays | Rejected (kept as passthrough, Req 4) |
| Fabricate default five levels for every model | Variants for all models | Uniform UI | Wrong for large-2512 (rejects efforts) and large-4 (no none); violates no-fabrication policy | Rejected |

## Design Decisions
1. **Curated table, not fabricated variants**: `variants` become curated facts from the mistral-vibe known-rows; models without a row keep `variants: []`. The "No fabricated variants" comment in `models.ts` is amended to say variants come only from the curated table.
2. **Wire field**: upstream `reasoning_effort` only; `off` → `"none"`; dialect mappings live in `compat.py`.
3. **Unset = absent**: no variant selected → no `reasoning_effort` field, preserving today's behavior and prompt-cache prefixes.
4. **No new diagnostics fields** in v1 (avoids a proxy-observability revalidation trigger); the level name is not secret but is not needed in events yet.
5. **Replay gating** uses curated support: strip `reasoning_content` when the model has no curated reasoning row or the level is off. This deliberately does not infer unknown-model support from a requested effort.
