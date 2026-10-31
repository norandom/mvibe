import { fixtureHash } from "./cases.js";
import { parseCaseSpec, parseInventorySnapshot, parseScopeFingerprint, parseToolIdentity } from "./contracts.js";
import type { CaseSpec, DeepReadonly, InventoryDiff, InventorySnapshot, ScopeFingerprint, ToolIdentity, ToolOutcome } from "./contracts.js";

/** Normalized observation inputs, NOT an invented host SDK. Adapters supply owned-safe
 * definitions and explicit observed links; raw host payloads must never be passed here. */
export type Definition = DeepReadonly<{ description: string; inputSchema: Record<string, unknown> }>;
export type Provenance = DeepReadonly<{ kind: ToolIdentity["origin"]; observationId: string; configurationHash?: string }>;
export type RegistryObservation = DeepReadonly<{ registryId: string; declaredName: string; namespace?: string; definition: Definition; provenance: Provenance }>;
export type McpDiscovery = DeepReadonly<{ serverHash: string; namespace: string; protocolVersion: string; capabilitiesHash: string; observationId: string; tools: readonly RegistryObservation[] }>;
export type Offering = DeepReadonly<{ offeredName: string; registryId?: string; namespace?: string; definition: Definition }>;
export type AbsenceObservation = DeepReadonly<{ registryId: string; scopeHash: string; kind: "policy_disabled" | "missing_capability"; observationId: string }>;
export type CaseAssignment = DeepReadonly<{ scopeHash: string; tool: ToolIdentity; caseSpec: CaseSpec }>;
export type TurnInventoryInput = DeepReadonly<{
  scope: ScopeFingerprint; sessionId: string; requestId: string; requestKind: "primary";
  registry: readonly RegistryObservation[]; mcp: readonly McpDiscovery[]; offerings: readonly Offering[];
  absences: readonly AbsenceObservation[]; assignments: readonly CaseAssignment[];
}>;
export type InventoryGap = DeepReadonly<{ code: "unmapped_offering" | "missing_case" | "unknown_origin" | "missing_mcp_discovery" | "new_tool" | "schema_changed" | "scope_changed" | "not_offered"; toolId?: string; offeredName?: string }>;
export type TurnInventory = DeepReadonly<{
  sessionId: string; requestId: string; snapshot: InventorySnapshot; diff: InventoryDiff;
  gaps: readonly InventoryGap[]; inventoryComplete: boolean;
  provenance: readonly { registryId: string; observation: Provenance }[];
  negotiatedMcp: readonly Omit<McpDiscovery,"tools">[];
  absenceObservations: readonly AbsenceObservation[];
  effectiveDefinitions: readonly { offeredName: string; registryId?: string; namespace?: string; schemaHash: string }[];
}>;
const invalid = (): never => { throw new Error("invalid_report"); };
function freeze<T>(value: T): T {
  if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
function exact(value: unknown, required: readonly string[], optional: readonly string[] = []): asserts value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value) || required.some(k => !Object.hasOwn(value,k)) || Object.keys(value).some(k => !required.includes(k) && !optional.includes(k))) invalid();
}
function id(value: unknown): void {
  parseToolIdentity({registryId:value, declaredName:value, origin:"unknown", schemaHash:"0".repeat(64)});
}
function hash(value: unknown): void { if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) invalid(); }
function list(value: unknown): asserts value is readonly unknown[] { if (!Array.isArray(value) || value.length > 4096) invalid(); }
function schemaHash(value: unknown): string {
  exact(value,["description","inputSchema"]);
  if (typeof value.description !== "string" || value.description.length > 65536 || !value.inputSchema || typeof value.inputSchema !== "object" || Array.isArray(value.inputSchema)) invalid();
  return fixtureHash(value);
}
function identity(entry: RegistryObservation): ToolIdentity {
  exact(entry,["registryId","declaredName","definition","provenance"],["namespace"]);
  exact(entry.provenance,["kind","observationId"],["configurationHash"]);
  id(entry.provenance.observationId);
  if (entry.provenance.configurationHash !== undefined) hash(entry.provenance.configurationHash);
  if (entry.provenance.kind !== "unknown" && !entry.provenance.configurationHash) invalid();
  return parseToolIdentity({registryId:entry.registryId, declaredName:entry.declaredName,
    ...(entry.namespace === undefined ? {} : {namespace:entry.namespace}), origin:entry.provenance.kind,
    ...(entry.provenance.configurationHash === undefined ? {} : {originHash:entry.provenance.configurationHash}), schemaHash:schemaHash(entry.definition)});
}

/** Fingerprint policy/capabilities/hooks/profile paths/auth contracts as hashes, plus
 * effective definitions/names. Call again on changes; no private contents persist. */
export function inventoryScope(metadata: Omit<ScopeFingerprint,"hash">, state: {
  policyHash: string; capabilitiesHash: string; hooksHash: string; profilePathsHash: string; authContractHash: string; definitionsHash: string;
}): ScopeFingerprint {
  fixtureHash({metadata,state});
  exact(state,["policyHash","capabilitiesHash","hooksHash","profilePathsHash","authContractHash","definitionsHash"]);
  Object.values(state).forEach(hash);
  const scope = parseScopeFingerprint({...metadata,hash:fixtureHash({metadata,state})});
  if (new Set(scope.negotiatedMcpVersions).size !== scope.negotiatedMcpVersions.length) invalid();
  return scope;
}

/** Pure manifest accounting only. This never accepts or creates execution credit. */
export function reconcile(snapshot: InventorySnapshot, cases: readonly CaseSpec[]): InventoryDiff {
  snapshot = parseInventorySnapshot(snapshot);
  const parsed = cases.map(parseCaseSpec);
  return freeze({missingCases:[...new Set([...snapshot.registered,...snapshot.offered].filter(t => !parsed.some(c => c.toolIds.includes(t.registryId))).map(t => t.registryId))].sort(),
    changedSchemas:[...new Set(snapshot.offered.filter(t => snapshot.registered.some(r => r.registryId === t.registryId && r.schemaHash !== t.schemaHash)).map(t => t.registryId))].sort()});
}
export const InventoryCoverage = Object.freeze({reconcile});

/** Invoke for EVERY primary request, retaining the previous observation for drift.
 * Links are explicit registry IDs plus namespaces; names never imply an executor. */
export function reconcileTurn(input: TurnInventoryInput, previous?: TurnInventory): TurnInventory {
  fixtureHash(input); // descriptor-safe bounded JSON before reading caller fields
  exact(input,["scope","sessionId","requestId","requestKind","registry","mcp","offerings","absences","assignments"]);
  const scope = parseScopeFingerprint(input.scope);
  id(input.sessionId); id(input.requestId);
  if (input.requestKind !== "primary") invalid();
  for (const values of [input.registry,input.mcp,input.offerings,input.absences,input.assignments]) list(values);
  if (previous) {
    fixtureHash(previous);
    if (previous.sessionId !== input.sessionId || previous.requestId === input.requestId) invalid();
    const priorScope = parseScopeFingerprint(previous.snapshot.scope);
    // A hash identifies one complete configuration. Reject a contradictory identity
    // before evaluating hash-bound case assignments or policy/capability observations.
    if (priorScope.hash === scope.hash && fixtureHash(priorScope) !== fixtureHash(scope)) invalid();
  }
  const entries = [...input.registry];
  const servers = new Set<string>();
  for (const server of input.mcp) {
    exact(server,["serverHash","namespace","protocolVersion","capabilitiesHash","observationId","tools"]);
    hash(server.serverHash); hash(server.capabilitiesHash); id(server.namespace); id(server.protocolVersion); id(server.observationId); list(server.tools);
    if (servers.has(server.namespace) || !scope.negotiatedMcpVersions.includes(server.protocolVersion)) invalid();
    servers.add(server.namespace);
    for (const tool of server.tools) {
      if (tool.provenance.kind !== "mcp" || tool.provenance.configurationHash !== server.serverHash || tool.namespace !== server.namespace) invalid();
      entries.push(tool);
    }
  }
  if (entries.length > 4096) invalid();
  const registered: ToolIdentity[] = [], provenance: {registryId:string; observation:Provenance}[] = [];
  const byId = new Map<string,ToolIdentity>();
  for (const entry of entries) {
    const tool = identity(entry), prior = byId.get(tool.registryId);
    if (prior) { if (fixtureHash(prior) !== fixtureHash(tool)) invalid(); continue; }
    byId.set(tool.registryId,tool); registered.push(tool); provenance.push({registryId:tool.registryId,observation:structuredClone(entry.provenance)});
  }
  const gaps: InventoryGap[] = [], offered: ToolIdentity[] = [], offeredIds = new Set<string>(), names = new Set<string>();
  const effectiveDefinitions: {offeredName:string; registryId?:string; namespace?:string; schemaHash:string}[] = [];
  for (const offering of input.offerings) {
    exact(offering,["offeredName","definition"],["registryId","namespace"]); id(offering.offeredName);
    if (offering.namespace !== undefined) id(offering.namespace);
    if (offering.registryId !== undefined) id(offering.registryId);
    const fingerprint = schemaHash(offering.definition);
    effectiveDefinitions.push({offeredName:offering.offeredName,schemaHash:fingerprint,
      ...(offering.registryId === undefined ? {} : {registryId:offering.registryId}),
      ...(offering.namespace === undefined ? {} : {namespace:offering.namespace})});
    if (names.has(offering.offeredName)) invalid(); names.add(offering.offeredName);
    const tool = offering.registryId === undefined ? undefined : byId.get(offering.registryId);
    if (!tool) { gaps.push({code:"unmapped_offering",offeredName:offering.offeredName}); continue; }
    if (tool.namespace !== offering.namespace || offeredIds.has(tool.registryId)) invalid();
    offeredIds.add(tool.registryId);
    offered.push(parseToolIdentity({...tool,offeredName:offering.offeredName,schemaHash:fingerprint}));
    if (fingerprint !== tool.schemaHash) gaps.push({code:"schema_changed",toolId:tool.registryId});
  }
  const absent = new Map<string,AbsenceObservation>();
  for (const observation of input.absences) {
    exact(observation,["registryId","scopeHash","kind","observationId"]); id(observation.observationId);
    if (!byId.has(observation.registryId) || absent.has(observation.registryId) || offeredIds.has(observation.registryId) || observation.scopeHash !== scope.hash || !["policy_disabled","missing_capability"].includes(observation.kind)) invalid();
    absent.set(observation.registryId,observation);
  }
  const assignments = input.assignments.map(a => {
    exact(a,["scopeHash","tool","caseSpec"]); hash(a.scopeHash);
    const tool = parseToolIdentity(a.tool), caseSpec = parseCaseSpec(a.caseSpec);
    if (!caseSpec.toolIds.includes(tool.registryId)) invalid();
    return {scopeHash:a.scopeHash,tool,caseSpec};
  });
  const classifiedAbsent = registered.filter(t => !offeredIds.has(t.registryId)).map(tool => {
    const observation = absent.get(tool.registryId);
    const outcome: ToolOutcome = !observation ? {status:"UNVERIFIED",reason:"not_exercised"} : observation.kind === "policy_disabled" ? {status:"DISABLED",reason:"host_disabled"} : {status:"UNSUPPORTED",reason:"missing_capability"};
    if (!observation) gaps.push({code:"not_offered",toolId:tool.registryId});
    return {tool,scopeHash:scope.hash,offline:outcome,live:outcome};
  });
  const missingCases: string[] = [], changedSchemas: string[] = [];
  for (const tool of registered) {
    if (tool.origin === "unknown") gaps.push({code:"unknown_origin",toolId:tool.registryId});
    if (tool.origin === "mcp" && !input.mcp.some(server => server.tools.some(t => t.registryId === tool.registryId))) gaps.push({code:"missing_mcp_discovery",toolId:tool.registryId});
    const effective = offered.find(t => t.registryId === tool.registryId) ?? tool;
    if (!absent.has(tool.registryId) && !assignments.some(a => a.scopeHash === scope.hash && fixtureHash(a.tool) === fixtureHash(effective))) {
      missingCases.push(tool.registryId); gaps.push({code:"missing_case",toolId:tool.registryId});
    }
    const prior = previous?.snapshot.registered.find(t => t.registryId === tool.registryId);
    if (previous && (!prior || (offeredIds.has(tool.registryId) && !previous.snapshot.offered.some(t => t.registryId === tool.registryId)))) gaps.push({code:"new_tool",toolId:tool.registryId});
    const priorOffering = previous?.snapshot.offered.find(t => t.registryId === tool.registryId);
    if ((prior && prior.schemaHash !== tool.schemaHash) || (priorOffering && priorOffering.schemaHash !== effective.schemaHash) || effective.schemaHash !== tool.schemaHash) changedSchemas.push(tool.registryId);
  }
  for (const toolId of changedSchemas) if (!gaps.some(g => g.code === "schema_changed" && g.toolId === toolId)) gaps.push({code:"schema_changed",toolId});
  const negotiatedMcp = input.mcp.map(({tools:_,...negotiated}) => structuredClone(negotiated));
  if (previous && (previous.snapshot.scope.hash !== scope.hash ||
    previous.snapshot.registered.length !== registered.length || previous.snapshot.offered.length !== offered.length ||
    fixtureHash(previous.negotiatedMcp) !== fixtureHash(negotiatedMcp) ||
    registered.some(t => { const prior = previous.snapshot.registered.find(p => p.registryId === t.registryId); return prior && fixtureHash(prior) !== fixtureHash(t); }) ||
    offered.some(t => { const prior = previous.snapshot.offered.find(p => p.registryId === t.registryId); return prior && fixtureHash(prior) !== fixtureHash(t); }))) gaps.push({code:"scope_changed"});
  const snapshot = parseInventorySnapshot({scope,registered,offered,classifiedAbsent});
  return freeze({sessionId:input.sessionId,requestId:input.requestId,snapshot,diff:{missingCases:missingCases.sort(),changedSchemas:changedSchemas.sort()},gaps,inventoryComplete:gaps.length === 0,provenance,negotiatedMcp,absenceObservations:structuredClone(input.absences),effectiveDefinitions});
}
