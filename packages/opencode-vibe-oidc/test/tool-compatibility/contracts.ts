/** Version 1 bounded evidence contracts. No captures, execution or claim authorization. */
export type DeepReadonly<T> = T extends (...args: any[]) => any ? T : T extends object ? { readonly [K in keyof T]: DeepReadonly<T[K]> } : T;

import type { LanguageModelV3 } from "@ai-sdk/provider";
import type { ProviderFetch } from "../../src/provider.js";
export type Mode = "offline" | "live";
export type ReasonCode = "not_exercised" | "missing_capability" | "host_disabled" |
  "unknown_tool" | "unsupported_protocol" | "incomplete_evidence" |
  "permission_denied" | "cancelled" | "executor_failed" | "cleanup_failed" |
  "invalid_report" | "scope_changed" | "inventory_gap" | "timeout" |
  "redaction_failed" | "hook_unavailable" | "model_declined" | "upstream_rejected";
export type ToolIdentity = DeepReadonly<{
  registryId: string; declaredName: string; offeredName?: string; namespace?: string;
  origin: "native" | "plugin" | "mcp" | "unknown";
  originHash?: string; schemaHash: string;
}>;
export type ScopeFingerprint = DeepReadonly<{
  hash: string; hostVersion: string; hostSha256: string; sdkVersion: string;
  providerBuildHash: string; profileHash: string; agent: string;
  model: string; protocolProfile: string; negotiatedMcpVersions: readonly string[];
}>;
export type ToolOutcome = DeepReadonly<
  | { status: "PASS"; proofIds: readonly string[] }
  | { status: "FAIL"; code: ReasonCode }
  | { status: "UNSUPPORTED" | "DISABLED" | "UNVERIFIED"; reason: ReasonCode }>;
export type CoverageRow = DeepReadonly<{
  tool: ToolIdentity; scopeHash: string;
  offline: ToolOutcome; live: ToolOutcome;
}>;
export type RunSpec = DeepReadonly<{
  mode: Mode; hostExecutable: string; bunExecutable: string;
  preparedPython: string; caseManifest: string; ownedProfile: string;
  model: string; reportDirectory: string;
  loginPolicy: "reuse-only" | "explicit-browser";
  limits: { startupMs: number; discoveryMs: number; cancellationMs: number;
    runMs: number; traceBytes: number; eventsPerCase: number; toolCallsPerCase: number };
}>;
export type CaseSpec = DeepReadonly<{
  id: string; toolIds: readonly string[]; prerequisites: readonly string[];
  fixtureKind: "filesystem" | "shell" | "web" | "question" | "delegation" |
    "state" | "mcp" | "plugin" | "media";
  expectedObservationHash: string; positiveRequired: boolean; timeoutMs: number;
  expectedCalls: readonly { toolId: string; fixtureNonceHash: string;
    inputHash: string; observationHash: string }[];
  expectedOutcome: "success" | "denied" | "error" | "cancelled" | "unsupported";
  requiredObservations: readonly ("definition" | "model_call" | "executor_started" |
    "executor_completed" | "next_model_request" | "final_response" |
    "denied" | "error" | "unsupported" | "state_observed")[];
  executorExpectation: "must_complete" | "must_not_start" | "observe_if_started";
  cancellation?: { host: boolean; executor: boolean; provider: boolean; mcp: boolean;
    completionRace: "fail_case" | "mark_unverified" };
}>;
export type InventorySnapshot = DeepReadonly<{
  scope: ScopeFingerprint; registered: readonly ToolIdentity[];
  offered: readonly ToolIdentity[]; classifiedAbsent: readonly CoverageRow[];
}>;
export type InventoryDiff = DeepReadonly<{ missingCases: readonly string[]; changedSchemas: readonly string[] }>;
export type TraceKind = "definition" | "model_call" | "executor_started" |
  "executor_completed" | "next_model_request" | "final_response" |
  "denied" | "error" | "unsupported" | "host_cancelled" | "executor_released" |
  "provider_aborted" | "fixture_cancelled" | "state_observed";
export type TraceBase = DeepReadonly<{
  sequence: number; runId: string; caseId: string; scopeHash: string;
  sessionId: string; chainId: string; requestId: string;
  requestKind: "primary" | "title" | "compaction" | "generate";
  payloadHash: string; observationHash?: string;
}>;
export type ToolTraceIdentity = DeepReadonly<{
  toolId: string; toolSchemaHash: string; originHash?: string;
}>;
export type CallCorrelation = DeepReadonly<{
  hostMessageId: string; hostCallId: string; upstreamCallId: string;
  inputHash: string; link: "host_exposed" | "unique_fixture_nonce";
  fixtureNonceHash?: string;
}>;
export type TraceEvent = DeepReadonly<TraceBase & (
  | ({ kind: "definition" | "unsupported" | "state_observed" } & ToolTraceIdentity)
  | { kind: "final_response" }
  | ({ kind: "model_call" | "next_model_request";
       call: CallCorrelation; wireCallId: string } & ToolTraceIdentity)
  | ({ kind: "executor_started" | "executor_completed" | "denied" | "error";
       call: CallCorrelation } & ToolTraceIdentity)
  | { kind: "host_cancelled" | "executor_released" | "provider_aborted" |
      "fixture_cancelled"; call?: CallCorrelation }
)>;
export type RoundTripProof = DeepReadonly<{
  id: string; caseId: string; scopeHash: string; sessionId: string;
  toolId: string; toolSchemaHash: string; originHash?: string; chainId: string;
  requestKind: "primary"; primaryRequestId: string; nextRequestId: string;
  hostMessageId: string; upstreamCallId: string; hostCallId: string;
  continuationWireCallId: string;
  definitionEvent: number; modelCallEvent: number;
  executorStartEvent: number; executorEndEvent: number;
  nextRequestEvent: number; finalResponseEvent: number;
}>;
export type CancelProof = DeepReadonly<{
  hostEvent: number; executorEvent?: number; providerEvent?: number; mcpEvent?: number;
  stateEvent: number; commitState: "absent" | "observed" | "unknown";
}>;
export type CaseResult = DeepReadonly<{
  caseId: string; scopeHash: string; toolId: string; sessionId: string; chainId: string;
  status: "PASS" | "FAIL" | "UNVERIFIED";
  observedOutcome: "success" | "denied" | "error" | "cancelled" | "unsupported" | "not_observed";
  eventReferences: readonly number[]; proofId?: string;
  cancellation?: CancelProof;
}>;
export type RunReport = DeepReadonly<{
  schemaVersion: 1; feature: "opencode-vibe-tool-compatibility";
  runId: string; mode: Mode; caseManifestHash: string; criteriaMappingHash: string;
  scopes: readonly ScopeFingerprint[]; inventories: readonly InventorySnapshot[];
  rows: readonly CoverageRow[]; events: readonly TraceEvent[];
  proofs: readonly RoundTripProof[]; caseResults: readonly CaseResult[]; inventoryComplete: boolean;
  supportedToolIds: readonly string[]; cleanupComplete: boolean;
}>;
export type ScopedClaim = DeepReadonly<{ scopeHash: string; mode: Mode; supported: readonly string[]; complete: boolean }>;
export type GateResult = DeepReadonly<{ claims: readonly ScopedClaim[]; errors: readonly ReasonCode[] }>;
export type HostController = DeepReadonly<{ run(spec: RunSpec, signal: AbortSignal): Promise<RunReport> }>;
export type InventoryCoverage = DeepReadonly<{
  reconcile(snapshot: InventorySnapshot, cases: readonly CaseSpec[]): InventoryDiff;
}>;
export type RoundTripRecorder = DeepReadonly<{
  finish(caseId: string): RoundTripProof | { status: "INCOMPLETE"; code: ReasonCode };
}>;
export type EvidenceGate = DeepReadonly<{ validate(report: RunReport, expectedScopes: readonly string[], expectedCases: readonly CaseSpec[]): GateResult }>;
export type ToolProtocolProfile = DeepReadonly<{
  id: string; evidenceRefs: readonly string[];
  callIds: "preserve" | "legacy-nine-alphanumeric";
  names: "preserve" | "mistral-constrained";
  errors: "preserve" | "explicit-content-envelope";
  supportedToolMimeTypes: readonly string[];
}>;
export type ToolProtocolAdapter = DeepReadonly<{
  wrap(factory: (fetch: ProviderFetch) => LanguageModelV3,
       authorizedFetch: ProviderFetch, profile: ToolProtocolProfile): LanguageModelV3;
}>;

export const CONTRACT_LIMITS = Object.freeze({ identifier: 256, array: 4096, events: 32768, artifact: 512, milliseconds: 3_600_000, traceBytes: 16_777_216 });
export const REASON_CODES = Object.freeze(["not_exercised", "missing_capability", "host_disabled", "unknown_tool", "unsupported_protocol", "incomplete_evidence", "permission_denied", "cancelled", "executor_failed", "cleanup_failed", "invalid_report", "scope_changed", "inventory_gap", "timeout", "redaction_failed", "hook_unavailable", "model_declined", "upstream_rejected"] as const);
type Check = (value: unknown) => boolean;
type Fields = Record<string, Check>;
const fail = (): never => { throw new Error("invalid_report"); };
const identifier: Check = v => typeof v === "string" && v.length <= CONTRACT_LIMITS.identifier && /^[A-Za-z0-9][A-Za-z0-9_.:@/+~-]*$/.test(v) && !v.includes("://") && !v.includes("..") && !/^[A-Za-z]:\//.test(v);
const hash: Check = v => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const boolean: Check = v => typeof v === "boolean";
const integer = (max: number, min = 1): Check => v => typeof v === "number" && Number.isSafeInteger(v) && v >= min && v <= max;
const oneOf = (...values: readonly unknown[]): Check => v => values.includes(v);
const list = (check: Check, max: number = CONTRACT_LIMITS.array): Check => v => {
  if (!Array.isArray(v) || Object.getPrototypeOf(v) !== Array.prototype || v.length > max || Reflect.ownKeys(v).length !== v.length + 1) return false;
  for (let i = 0; i < v.length; i++) {
    const descriptor = Object.getOwnPropertyDescriptor(v, String(i));
    if (!descriptor || !("value" in descriptor) || !descriptor.enumerable || !check(descriptor.value)) return false;
  }
  return true;
};
const object = (fields: Fields, optional: Fields = {}): Check => v => {
  if (!v || typeof v !== "object" || Array.isArray(v) || Object.getPrototypeOf(v) !== Object.prototype) return false;
  const descriptors = Object.getOwnPropertyDescriptors(v);
  if (Reflect.ownKeys(v).length !== Object.keys(descriptors).length) return false;
  return Object.keys(fields).every(k => Object.hasOwn(descriptors, k)) && Object.entries(descriptors).every(([k, d]) =>
    "value" in d && d.enumerable && (fields[k] ?? optional[k])?.(d.value) === true);
};
const reason = oneOf(...REASON_CODES);
const mode = oneOf("offline", "live");
const sequence = integer(CONTRACT_LIMITS.events);
const strings = list(identifier);
const artifact: Check = v => typeof v === "string" && v.length <= CONTRACT_LIMITS.artifact && /^(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(v) && !v.split("/").some(p => p === "." || p === "..");
// Prepared executable/profile paths are controller input, never persisted report captures.
// eslint-disable-next-line no-control-regex -- Reject control bytes in executable paths.
const preparedPath: Check = v => typeof v === "string" && v.length > 0 && v.length <= 4096 && !/[\x00-\x1f\x7f]/.test(v) && !v.includes("://");
const tool = object({ registryId: identifier, declaredName: identifier, origin: oneOf("native", "plugin", "mcp", "unknown"), schemaHash: hash }, { offeredName: identifier, namespace: identifier, originHash: hash });
const scope = object({ hash, hostVersion: identifier, hostSha256: hash, sdkVersion: identifier, providerBuildHash: hash, profileHash: hash, agent: identifier, model: identifier, protocolProfile: identifier, negotiatedMcpVersions: strings });
const outcome: Check = v => object({status: oneOf("PASS"), proofIds: v => list(identifier)(v) && (v as unknown[]).length > 0})(v) || object({ status: oneOf("FAIL"), code: reason })(v) || object({ status: oneOf("UNSUPPORTED", "DISABLED", "UNVERIFIED"), reason })(v);
const row = object({ tool, scopeHash: hash, offline: outcome, live: outcome });
const limits = object({ startupMs: integer(CONTRACT_LIMITS.milliseconds), discoveryMs: integer(CONTRACT_LIMITS.milliseconds), cancellationMs: integer(CONTRACT_LIMITS.milliseconds), runMs: integer(CONTRACT_LIMITS.milliseconds), traceBytes: integer(CONTRACT_LIMITS.traceBytes), eventsPerCase: sequence, toolCallsPerCase: integer(4096) });
const runSpec = object({mode, hostExecutable: preparedPath, bunExecutable: preparedPath, preparedPython: preparedPath, caseManifest: preparedPath, ownedProfile: preparedPath, model: identifier, reportDirectory: preparedPath, loginPolicy: oneOf("reuse-only", "explicit-browser"), limits});
const observations = ["definition", "model_call", "executor_started", "executor_completed", "next_model_request", "final_response", "denied", "error", "unsupported", "state_observed"] as const;
const cancellation = object({host: boolean, executor: boolean, provider: boolean, mcp: boolean, completionRace: oneOf("fail_case", "mark_unverified")});
const caseSpec = object({id: identifier, toolIds: strings, prerequisites: strings, fixtureKind: oneOf("filesystem", "shell", "web", "question", "delegation", "state", "mcp", "plugin", "media"), expectedObservationHash: hash, positiveRequired: boolean, timeoutMs: integer(CONTRACT_LIMITS.milliseconds), expectedCalls: list(object({toolId: identifier, fixtureNonceHash: hash, inputHash: hash, observationHash: hash})), expectedOutcome: oneOf("success", "denied", "error", "cancelled", "unsupported"), requiredObservations: list(oneOf(...observations)), executorExpectation: oneOf("must_complete", "must_not_start", "observe_if_started")}, {cancellation});
const inventory = object({scope, registered: list(tool), offered: list(tool), classifiedAbsent: list(row)});
const callBase = {hostMessageId: identifier, hostCallId: identifier, upstreamCallId: identifier, inputHash: hash};
const call: Check = v => object({...callBase, link: oneOf("host_exposed")}, {fixtureNonceHash: hash})(v) || object({...callBase, link: oneOf("unique_fixture_nonce"), fixtureNonceHash: hash})(v);
const traceBase = {sequence, runId: identifier, caseId: identifier, scopeHash: hash, sessionId: identifier, chainId: identifier, requestId: identifier, requestKind: oneOf("primary", "title", "compaction", "generate"), payloadHash: hash};
const toolTrace = {toolId: identifier, toolSchemaHash: hash};
const traceOptional = {observationHash: hash, originHash: hash};
const event: Check = v => object({...traceBase, ...toolTrace, kind: oneOf("definition", "unsupported", "state_observed")}, traceOptional)(v) ||
  object({...traceBase, kind: oneOf("final_response")}, {observationHash: hash})(v) ||
  object({...traceBase, ...toolTrace, kind: oneOf("model_call", "next_model_request"), call, wireCallId: identifier}, traceOptional)(v) ||
  object({...traceBase, ...toolTrace, kind: oneOf("executor_started", "executor_completed", "denied", "error"), call}, traceOptional)(v) ||
  object({...traceBase, kind: oneOf("host_cancelled", "executor_released", "provider_aborted", "fixture_cancelled")}, {observationHash: hash, call})(v);
const proof = object({id: identifier, caseId: identifier, scopeHash: hash, sessionId: identifier, ...toolTrace, chainId: identifier, requestKind: oneOf("primary"), primaryRequestId: identifier, nextRequestId: identifier, hostMessageId: identifier, upstreamCallId: identifier, hostCallId: identifier, continuationWireCallId: identifier, definitionEvent: sequence, modelCallEvent: sequence, executorStartEvent: sequence, executorEndEvent: sequence, nextRequestEvent: sequence, finalResponseEvent: sequence}, {originHash: hash});
const cancelProof = object({hostEvent: sequence, stateEvent: sequence, commitState: oneOf("absent", "observed", "unknown")}, {executorEvent: sequence, providerEvent: sequence, mcpEvent: sequence});
const result = object({caseId: identifier, scopeHash: hash, toolId: identifier, sessionId: identifier, chainId: identifier, status: oneOf("PASS", "FAIL", "UNVERIFIED"), observedOutcome: oneOf("success", "denied", "error", "cancelled", "unsupported", "not_observed"), eventReferences: list(sequence)}, {proofId: identifier, cancellation: cancelProof});
const report = object({schemaVersion: oneOf(1), feature: oneOf("opencode-vibe-tool-compatibility"), runId: identifier, mode, caseManifestHash: hash, criteriaMappingHash: hash, scopes: list(scope), inventories: list(inventory), rows: list(row), events: list(event, CONTRACT_LIMITS.events), proofs: list(proof), caseResults: list(result), inventoryComplete: boolean, supportedToolIds: strings, cleanupComplete: boolean});
const claim = object({scopeHash: hash, mode, supported: strings, complete: boolean});
const gateResult = object({claims: list(claim), errors: list(reason)});
const protocolProfile = object({id: identifier, evidenceRefs: list(artifact), callIds: oneOf("preserve", "legacy-nine-alphanumeric"), names: oneOf("preserve", "mistral-constrained"), errors: oneOf("preserve", "explicit-content-envelope"), supportedToolMimeTypes: list(v => typeof v === "string" && v.length <= 128 && /^[a-z0-9.+-]+\/[a-z0-9.+-]+$/.test(v))});

function immutable<T>(value: T): T {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) immutable(child);
    Object.freeze(value);
  }
  return value;
}
function parse<T>(check: Check, value: unknown): T {
  try { if (!check(value)) fail(); return immutable(structuredClone(value)) as T; }
  catch { return fail(); }
}

export const parseToolIdentity = (value: unknown): ToolIdentity => parse(tool, value);
export const parseScopeFingerprint = (value: unknown): ScopeFingerprint => parse(scope, value);
export const parseToolOutcome = (value: unknown): ToolOutcome => parse(outcome, value);
export const parseCoverageRow = (value: unknown): CoverageRow => parse(row, value);
export const parseRunSpec = (value: unknown): RunSpec => parse(runSpec, value);
export const parseCaseSpec = (value: unknown): CaseSpec => parse(caseSpec, value);
export const parseInventorySnapshot = (value: unknown): InventorySnapshot => parse(inventory, value);
export const parseInventoryDiff = (value: unknown): InventoryDiff => parse(object({missingCases: strings, changedSchemas: strings}), value);
export const parseCallCorrelation = (value: unknown): CallCorrelation => parse(call, value);
export const parseTraceEvent = (value: unknown): TraceEvent => parse(event, value);
export const parseRoundTripProof = (value: unknown): RoundTripProof => parse(proof, value);
export const parseCancelProof = (value: unknown): CancelProof => parse(cancelProof, value);
export const parseCaseResult = (value: unknown): CaseResult => parse(result, value);
export const parseScopedClaim = (value: unknown): ScopedClaim => parse(claim, value);
export const parseGateResult = (value: unknown): GateResult => parse(gateResult, value);
export const parseToolProtocolProfile = (value: unknown): ToolProtocolProfile => parse(protocolProfile, value);

export function parseRunReport(value: unknown): RunReport {
  const parsed = parse<RunReport>(report, value);
  validateReportLinks(parsed);
  return parsed;
}

/** Structural identity joins only. Manifest authority and support claims are checked by EvidenceGate. */
function validateReportLinks(r: RunReport): void {
  const unique = <T>(values: readonly T[], key: (item: T) => string | number): Map<string | number, T> => {
    const entries = new Map(values.map(item => [key(item), item]));
    if (entries.size !== values.length) fail();
    return entries;
  };
  const scopes = unique(r.scopes, s => s.hash);
  const events = unique(r.events, e => e.sequence);
  const proofs = unique(r.proofs, p => p.id);
  const same = (a: unknown, b: unknown): void => { if (a !== b) fail(); };
  const sameTool = (a: ToolIdentity, b: ToolIdentity): void => {
    for (const k of ["registryId", "declaredName", "offeredName", "namespace", "origin", "originHash", "schemaHash"] as const) same(a[k], b[k]);
  };
  const sameCall = (a: CallCorrelation, b: CallCorrelation): boolean =>
    (["hostMessageId", "hostCallId", "upstreamCallId", "inputHash", "link", "fixtureNonceHash"] as const).every(k => a[k] === b[k]);
  const toolKeys = new Map<string, ToolIdentity>();
  for (const inv of r.inventories) {
    const registeredScope = scopes.get(inv.scope.hash); if (!registeredScope) return fail();
    for (const k of Object.keys(registeredScope) as (keyof ScopeFingerprint)[])
      same(JSON.stringify(registeredScope[k]), JSON.stringify(inv.scope[k]));
    for (const t of [...inv.registered, ...inv.offered]) {
      const key = `${inv.scope.hash}:${t.registryId}`;
      const prior = toolKeys.get(key);
      // Registry entries may precede effective offering. A missing offered name
      // contributes no offering assertion; two observed names cannot disagree.
      if (prior) sameTool({...prior, offeredName: prior.offeredName ?? t.offeredName} as ToolIdentity,
        {...t, offeredName: t.offeredName ?? prior.offeredName} as ToolIdentity);
      toolKeys.set(key, prior?.offeredName !== undefined && t.offeredName === undefined ? prior : t);
    }
  }
  const checkTool = (scopeHash: string, toolId: string, schemaHash?: string, originHash?: string): void => {
    if (!scopes.has(scopeHash)) fail();
    const t = toolKeys.get(`${scopeHash}:${toolId}`); if (!t) return fail();
    if (schemaHash !== undefined) { same(t.schemaHash, schemaHash); same(t.originHash, originHash); }
  };
  for (const e of r.events) {
    same(e.runId, r.runId); if (!scopes.has(e.scopeHash)) fail();
    if ("toolId" in e) checkTool(e.scopeHash, e.toolId, e.toolSchemaHash, e.originHash);
  }
  const linkedEvent = (n: number, owner: {caseId: string; scopeHash: string; sessionId: string; chainId: string}): TraceEvent => {
    const e = events.get(n); if (!e) return fail();
    for (const k of ["caseId", "scopeHash", "sessionId", "chainId"] as const) same(e[k], owner[k]);
    return e;
  };
  for (const p of r.proofs) {
    checkTool(p.scopeHash, p.toolId, p.toolSchemaHash, p.originHash);
    const links = [[p.definitionEvent, "definition"], [p.modelCallEvent, "model_call"], [p.executorStartEvent, "executor_started"], [p.executorEndEvent, "executor_completed"], [p.nextRequestEvent, "next_model_request"], [p.finalResponseEvent, "final_response"]] as const;
    let previous = 0; let correlation: CallCorrelation | undefined;
    for (const [n, kind] of links) {
      if (n <= previous) fail(); previous = n;
      const e = linkedEvent(n, p); same(e.kind, kind); same(e.requestKind, p.requestKind);
      same(e.requestId, n <= p.executorEndEvent ? p.primaryRequestId : p.nextRequestId);
      if ("toolId" in e) { same(e.toolId, p.toolId); same(e.toolSchemaHash, p.toolSchemaHash); same(e.originHash, p.originHash); }
      if ("call" in e && e.call) {
        same(e.call.hostMessageId, p.hostMessageId); same(e.call.hostCallId, p.hostCallId); same(e.call.upstreamCallId, p.upstreamCallId);
        if (correlation) for (const k of ["inputHash", "link", "fixtureNonceHash"] as const) same(e.call[k], correlation[k]);
        correlation = e.call;
      }
      if (e.kind === "next_model_request") same(e.wireCallId, p.continuationWireCallId);
    }
  }
  for (const c of r.caseResults) {
    checkTool(c.scopeHash, c.toolId);
    for (const n of c.eventReferences) { const e = linkedEvent(n, c); if ("toolId" in e) same(e.toolId, c.toolId); }
    if (c.proofId) {
      const p = proofs.get(c.proofId); if (!p) return fail();
      for (const k of ["caseId", "scopeHash", "toolId", "sessionId", "chainId"] as const) same(p[k], c[k]);
    }
    if (c.cancellation) {
      const cp = c.cancellation;
      const host = linkedEvent(cp.hostEvent, c);
      let cancellationCall: CallCorrelation | undefined;
      const knownCalls = c.eventReferences.flatMap(n => {
        const e = linkedEvent(n, c);
        return e.kind === "model_call" || e.kind === "executor_started" || e.kind === "executor_completed" || e.kind === "next_model_request" || e.kind === "denied" || e.kind === "error" ? [{call:e.call, requestId:e.requestId, requestKind:e.requestKind}] : [];
      });
      for (const [n, kind] of [[cp.hostEvent, "host_cancelled"], [cp.executorEvent, "executor_released"], [cp.providerEvent, "provider_aborted"], [cp.mcpEvent, "fixture_cancelled"], [cp.stateEvent, "state_observed"]] as const)
        if (n !== undefined) {
          const e = linkedEvent(n, c); same(e.kind, kind); if ("toolId" in e) same(e.toolId, c.toolId);
          // Cancellation lifecycle observations refer to the host's cancelled
          // request. State observations can follow in their own request context.
          if (kind !== "state_observed") { same(e.requestId, host.requestId); same(e.requestKind, host.requestKind); }
          if ("call" in e && e.call) {
            if (cancellationCall && !sameCall(cancellationCall, e.call)) fail();
            const currentCall = e.call;
            if (knownCalls.length && !knownCalls.some(known => sameCall(known.call, currentCall) && known.requestId === e.requestId && known.requestKind === e.requestKind)) fail();
            cancellationCall = currentCall;
          }
        }
    }
  }
  for (const row of [...r.rows, ...r.inventories.flatMap(i => i.classifiedAbsent)]) {
    checkTool(row.scopeHash, row.tool.registryId, row.tool.schemaHash, row.tool.originHash);
    sameTool(toolKeys.get(`${row.scopeHash}:${row.tool.registryId}`)!, row.tool);
    for (const m of ["offline", "live"] as const) if (row[m].status === "PASS") {
      if (m !== r.mode) fail();
      for (const id of row[m].proofIds) { const p = proofs.get(id); if (!p) return fail(); same(p.scopeHash, row.scopeHash); same(p.toolId, row.tool.registryId); }
    }
  }
  for (const id of r.supportedToolIds) if (!r.rows.some(row => row.tool.registryId === id)) fail();
}
