import { Model } from "@opencode/plugin";

// Curated thinking levels per upstream Mistral model, mirroring the known-model
// rows of the Vibe reference (../mistral-vibe
// mistralai_vibe_local_harness/vibe/completion_profile/known_models.py:77-101):
// the Mistral API takes only low/high/max for Mistral Large 4 and rejects
// "none" (reasoning cannot be turned off); medium-3.5 takes high and can turn
// off; large-2512/latest reject every reasoning effort; GLM 5.3 takes
// low/high/max. On the Mistral wire a level travels as the top-level
// `reasoning_effort` field, with off sent as "none"
// (../mistral-vibe .../completion_profile/reasoning.py:95-137,
// .../adapters/mistral.py:148-151). Standard level names:
// ../mistral-vibe/vibe/config_values.py:12.

type Row = { levels: readonly string[]; canTurnOff: boolean };

const ROWS: ReadonlyArray<readonly [readonly string[], Row]> = [
  [["mistral-large-4", "mistral-large-4-0", "mistral-large-4-0-8"], { levels: ["low", "high", "max"], canTurnOff: false }],
  [["mistral-medium-3.5", "mistral-medium-3-5", "mistral-medium-2604", "mistral-medium-latest", "magistral-medium-latest", "mistral-vibe-cli-latest", "mistral-vibe-cli-with-tools"], { levels: ["high"], canTurnOff: true }],
  [["mistral-large-2512", "mistral-large-latest"], { levels: [], canTurnOff: false }],
  [["zai-glm-5-3", "zai-glm-5", "zai-glm-latest"], { levels: ["low", "high", "max"], canTurnOff: false }],
];

const TABLE = new Map<string, Row>(ROWS.flatMap(([ids, row]) => ids.map(id => [id, row] as const)));

// The wire value of the off level (../mistral-vibe REASONING_WIRE_VALUES).
export const WIRE_OFF = "none";

/** Strip a provider prefix: the request body may carry "vibe-oidc/<id>". */
function baseId(model: string): string {
  const slash = model.lastIndexOf("/");
  return slash === -1 ? model : model.slice(slash + 1);
}

export function thinkingRow(modelId: string): Row | undefined {
  return TABLE.get(baseId(modelId));
}

/**
 * Variants offered for a model. Only curated rows produce any; every other
 * model keeps `variants: []` — nothing is fabricated. Each variant carries
 * only `settings: { reasoningEffort }`, the overlay OpenCode v2 merges into
 * the request options for `@ai-sdk/openai-compatible` providers (its own
 * mistral provider uses the same spelling; `body` overlays are dropped on
 * this SDK path). The host's SDK then emits the Mistral wire field
 * `reasoning_effort`; `off` goes out as "none". Model refs select a variant
 * as `provider/model#variant`.
 */
export function variantsFor(modelId: string): Model.Variant[] {
  const row = TABLE.get(baseId(modelId));
  if (!row || row.levels.length === 0) return [];
  const levels = row.canTurnOff ? [...row.levels, "off"] : row.levels;
  return levels.map(level => ({
    id: Model.VariantID.make(level),
    settings: { reasoningEffort: level === "off" ? WIRE_OFF : level },
  }));
}

/**
 * Whether earlier assistant reasoning may be replayed for this model and
 * level, following the Vibe reference's `takes_reasoning_input`: a model
 * known to reason takes it. Unknown models have no curated replay support;
 * requesting an effort alone does not establish that they accept thinking chunks.
 */
export function reasoningSupported(modelId: string, level?: unknown): boolean {
  const wire = typeof level === "string" && level.length > 0 ? level : undefined;
  if (wire === WIRE_OFF || wire === "off") return false;
  const row = TABLE.get(baseId(modelId));
  if (row) return row.levels.length > 0;
  return false;
}
