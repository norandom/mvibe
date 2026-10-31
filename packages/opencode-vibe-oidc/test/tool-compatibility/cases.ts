import manifest from "./manifests/default.json" with { type: "json" };
import { createHash } from "node:crypto";
import { parseCaseSpec, parseRunSpec } from "./contracts.js";
import type { CaseSpec, DeepReadonly, RunSpec, ToolOutcome } from "./contracts.js";

export type CaseTemplate = DeepReadonly<{
  recipeVersion: 1; objective: string; calls: readonly string[];
  spec: Omit<CaseSpec, "expectedObservationHash" | "expectedCalls">;
}>;
export type CaseManifest = DeepReadonly<{ schemaVersion: 1; limits: RunSpec["limits"]; cases: readonly CaseTemplate[] }>;
export type FixtureMaterial = DeepReadonly<{
  recipeVersion: 1; objective: string; observation: unknown;
  calls: readonly { toolId: string; nonce: string; input: unknown; observation: unknown }[];
}>;
export type PrerequisiteState = "available" | "disabled" | "unsupported" | "unverified";
const invalid = (): never => { throw new Error("invalid_report"); };

/** Bounded canonical JSON; used only with owned fixture material, never raw host captures. */
export function fixtureHash(value: unknown): string {
  let nodes = 0;
  const encode = (v: unknown, depth: number): string => {
    if (++nodes > 4096 || depth > 32) return invalid();
    if (v === null || typeof v === "boolean") return JSON.stringify(v);
    if (typeof v === "string") { if (v.length > 65536) return invalid(); return JSON.stringify(v); }
    if (typeof v === "number" && Number.isFinite(v)) return JSON.stringify(v);
    if (!v || typeof v !== "object") return invalid();
    if (Array.isArray(v)) {
      if (Object.getPrototypeOf(v) !== Array.prototype || Reflect.ownKeys(v).length !== v.length + 1) return invalid();
      return `[${Array.from({length:v.length}, (_, i) => {
        const d = Object.getOwnPropertyDescriptor(v, String(i));
        if (!d || !("value" in d) || !d.enumerable) return invalid();
        return encode(d.value, depth + 1);
      }).join(",")}]`;
    }
    if (Object.getPrototypeOf(v) !== Object.prototype || Reflect.ownKeys(v).some(k => typeof k !== "string" || !Object.getOwnPropertyDescriptor(v,k)?.enumerable)) return invalid();
    return `{${Object.keys(v).sort().map(k => {
      const d = Object.getOwnPropertyDescriptor(v, k)!;
      if (!("value" in d) || !d.enumerable) return invalid();
      return `${JSON.stringify(k)}:${encode(d.value, depth + 1)}`;
    }).join(",")}}`;
  };
  let encoded: string;
  try { encoded = encode(value, 0); } catch { return invalid(); }
  if (encoded.length > 262144) return invalid();
  return createHash("sha256").update(encoded).digest("hex");
}
const placeholderHash = "0".repeat(64);
function freeze<T>(value: T): T {
  if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
function exact(value: unknown, keys: readonly string[]): asserts value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype ||
      Reflect.ownKeys(value).length !== keys.length || keys.some(k => !Object.hasOwn(value, k))) invalid();
}

export function validateCaseSpec(value: unknown, limits: RunSpec["limits"]): CaseSpec {
  limits = validateLimits(limits);
  const c = parseCaseSpec(value);
  const unique = (xs: readonly string[]) => new Set(xs).size === xs.length;
  if (!c.toolIds.length || !unique(c.toolIds) || !unique(c.prerequisites) || !unique(c.requiredObservations) ||
      c.timeoutMs > limits.runMs || c.expectedCalls.length > limits.toolCallsPerCase ||
      c.requiredObservations.length > limits.eventsPerCase ||
      new Set(c.expectedCalls.map(call => call.fixtureNonceHash)).size !== c.expectedCalls.length ||
      c.positiveRequired !== (c.expectedOutcome === "success") ||
      c.expectedCalls.some(call => !c.toolIds.includes(call.toolId)) ||
      c.toolIds.some(id => !c.expectedCalls.some(call => call.toolId === id))) return invalid();
  const has = (...names: CaseSpec["requiredObservations"][number][]) => names.every(n => c.requiredObservations.includes(n));
  for (const negative of ["denied", "error", "unsupported"] as const)
    if (has(negative) && c.expectedOutcome !== negative) return invalid();
  if (has("executor_completed") && !has("executor_started")) return invalid();
  if (c.positiveRequired && (c.executorExpectation !== "must_complete" ||
      !has("definition", "model_call", "executor_started", "executor_completed", "next_model_request", "final_response"))) return invalid();
  if (c.executorExpectation === "must_not_start" && (has("executor_started") || has("executor_completed"))) return invalid();
  if (c.executorExpectation === "must_complete" && !has("executor_started", "executor_completed")) return invalid();
  if (c.expectedOutcome === "denied" && (c.executorExpectation !== "must_not_start" || !has("definition", "model_call", "denied", "state_observed", "next_model_request", "final_response"))) return invalid();
  if (c.expectedOutcome === "error" && (!has("definition", "model_call", "error", "next_model_request", "final_response") ||
      (c.executorExpectation === "must_not_start" && !has("state_observed")))) return invalid();
  if (c.expectedOutcome === "unsupported" && (c.executorExpectation !== "must_not_start" || !has("unsupported"))) return invalid();
  if (c.expectedOutcome === "cancelled") {
    if (!c.cancellation?.host || !c.cancellation.provider || !has("definition", "model_call", "state_observed") ||
        c.timeoutMs < limits.cancellationMs ||
        c.executorExpectation === "must_complete" || (c.cancellation.executor && !has("executor_started")) ||
        (c.cancellation.mcp && (c.fixtureKind !== "mcp" || !c.cancellation.executor))) return invalid();
  } else if (c.cancellation) return invalid();
  const extraCancelEvents = c.cancellation ? 1 + Number(c.cancellation.executor) + Number(c.cancellation.provider) + Number(c.cancellation.mcp) : 0;
  const perCallEvents = c.requiredObservations.filter(k => ["model_call", "executor_started", "executor_completed", "next_model_request", "denied", "error"].includes(k)).length;
  if (c.requiredObservations.length + (c.expectedCalls.length - 1) * perCallEvents + extraCancelEvents > limits.eventsPerCase) return invalid();
  return c;
}

function validateLimits(value: unknown): RunSpec["limits"] {
  const limits = parseRunSpec({mode:"offline", hostExecutable:"owned-host", bunExecutable:"owned-bun", preparedPython:"owned-python", caseManifest:"owned-manifest", ownedProfile:"owned-profile", model:"fixture", reportDirectory:"owned-report", loginPolicy:"reuse-only", limits:value}).limits;
  if ([limits.startupMs, limits.discoveryMs, limits.cancellationMs].some(ms => ms > limits.runMs)) return invalid();
  return limits;
}

export function parseCaseManifest(value: unknown): CaseManifest {
  fixtureHash(value); // Reject accessor-backed/non-JSON inputs before reading any fields.
  exact(value, ["schemaVersion", "limits", "cases"]);
  if (value.schemaVersion !== 1 || !Array.isArray(value.cases) || !value.cases.length || value.cases.length > 4096) return invalid();
  const limits = validateLimits(value.limits);
  const ids = new Set<string>();
  const cases = value.cases.map((entry: unknown) => {
    exact(entry, ["recipeVersion", "objective", "calls", "spec"]);
    if (entry.recipeVersion !== 1 || typeof entry.objective !== "string" || !/^[a-z][a-z0-9_.-]{0,127}$/.test(entry.objective) || !Array.isArray(entry.calls)) return invalid();
    const spec = entry.spec as Record<string, unknown>;
    if (!spec || typeof spec !== "object" || Object.hasOwn(spec, "expectedCalls") || Object.hasOwn(spec, "expectedObservationHash")) return invalid();
    const parsed = validateCaseSpec({...spec, expectedObservationHash:placeholderHash,
      expectedCalls:entry.calls.map((toolId, i) => ({toolId, fixtureNonceHash:fixtureHash(i), inputHash:placeholderHash, observationHash:placeholderHash}))}, limits);
    if (ids.has(parsed.id)) return invalid(); ids.add(parsed.id);
    return structuredClone(entry) as unknown as CaseTemplate;
  });
  if (cases.reduce((sum, c) => sum + c.spec.timeoutMs, limits.startupMs + limits.discoveryMs + limits.cancellationMs) > limits.runMs) return invalid();
  return freeze({schemaVersion:1, limits, cases});
}

/** Trusted owned fixture recipes supply concrete per-run inputs/results BEFORE execution.
 * The recipe version/objective binds their identity. Neither a report nor an executor result
 * may be used as material. No fixture executor or evidence authorization lives here.
 */
export function materializeCase(template: CaseTemplate, material: FixtureMaterial, limits: RunSpec["limits"]): CaseSpec {
  fixtureHash(material);
  exact(material, ["recipeVersion", "objective", "observation", "calls"]);
  if (material.recipeVersion !== template.recipeVersion || material.objective !== template.objective || material.calls.length !== template.calls.length) return invalid();
  const nonces = new Set<string>();
  const expectedCalls = material.calls.map((call, i) => {
    exact(call, ["toolId", "nonce", "input", "observation"]);
    if (call.toolId !== template.calls[i] || typeof call.nonce !== "string" || !/^[A-Za-z0-9_-]{32,128}$/.test(call.nonce) || nonces.has(call.nonce)) return invalid();
    nonces.add(call.nonce);
    return {toolId:call.toolId, fixtureNonceHash:fixtureHash(call.nonce), inputHash:fixtureHash(call.input), observationHash:fixtureHash(call.observation)};
  });
  return validateCaseSpec({...template.spec, expectedCalls, expectedObservationHash:fixtureHash(material.observation)}, limits);
}

/** Classification only: even an applicable case has no execution evidence yet. */
export function caseApplicability(template: CaseTemplate, states: Readonly<Record<string, PrerequisiteState>>): DeepReadonly<{ applicable: boolean; missing: readonly string[]; outcome: ToolOutcome }> {
  const missing = template.spec.prerequisites.filter(p => states[p] !== "available");
  if (!missing.length) return freeze({applicable:true, missing, outcome:{status:"UNVERIFIED", reason:"not_exercised"}});
  const known = new Set(["available", "disabled", "unsupported", "unverified"]);
  if (missing.some(p => states[p] !== undefined && !known.has(states[p]!))) return invalid();
  const allDisabled = missing.every(p => states[p] === "disabled");
  const allProved = missing.every(p => states[p] === "disabled" || states[p] === "unsupported");
  const outcome: ToolOutcome = allDisabled ? {status:"DISABLED", reason:"host_disabled"} : allProved ? {status:"UNSUPPORTED", reason:"missing_capability"} : {status:"UNVERIFIED", reason:"missing_capability"};
  return freeze({applicable:false, missing, outcome});
}

export const DEFAULT_CASE_MANIFEST = () => parseCaseManifest(manifest);
