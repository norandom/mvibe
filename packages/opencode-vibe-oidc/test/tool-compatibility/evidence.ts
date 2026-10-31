import { parseRunReport, parseGateResult, parseScopeFingerprint, parseInventorySnapshot } from "./contracts.js";
import { fixtureHash, validateCaseSpec } from "./cases.js";
import type { CallCorrelation, CaseResult, CaseSpec, CoverageRow, EvidenceGate, GateResult, Mode, ReasonCode, RunReport, RunSpec, ScopeFingerprint, TraceEvent } from "./contracts.js";
import type { TurnInventory } from "./inventory.js";

export type EvidenceContext = {
  mode: Mode; scopes: readonly ScopeFingerprint[];
  caseManifestHash: string; criteriaMappingHash: string;
  limits: RunSpec["limits"]; turns: readonly TurnInventory[];
};
function reject(code: ReasonCode = "incomplete_evidence"): never { throw new Error(code); }
const same = (a: unknown, b: unknown, code: ReasonCode = "incomplete_evidence") => { if (fixtureHash(a) !== fixtureHash(b)) reject(code); };
const sorted = (xs: readonly string[]) => [...xs].sort();
const unique = (xs: readonly (string | number)[]) => { if (new Set(xs).size !== xs.length) reject("invalid_report"); };
const resultKey = (r: {scopeHash:string;caseId:string;toolId:string}) => `${r.scopeHash}:${r.caseId}:${r.toolId}`;
const unverified = () => ({status:"UNVERIFIED",reason:"not_exercised"} as const);

/** External expectations come from the controller BEFORE execution, never from report data.
 * This gate proves consistency of trusted observations, not the authenticity of arbitrary JSON.
 * The context is copied at construction to prevent later expectation mutation.
 */
export function createEvidenceGate(context: EvidenceContext): EvidenceGate {
  const expected = structuredClone(context);
  return Object.freeze({validate(report: RunReport, expectedScopes: readonly string[], expectedCases: readonly CaseSpec[]): GateResult {
    try {
      const r = parseRunReport(report);
      const evaluated = evaluate(r, expectedScopes, expectedCases, expected);
      const canonicalRows=(rows:readonly CoverageRow[])=>rows.map(row=>({...row,
        offline:row.offline.status==="PASS"?{...row.offline,proofIds:sorted(row.offline.proofIds)}:row.offline,
        live:row.live.status==="PASS"?{...row.live,proofIds:sorted(row.live.proofIds)}:row.live,
      })).sort((a,b)=>`${a.scopeHash}:${a.tool.registryId}`.localeCompare(`${b.scopeHash}:${b.tool.registryId}`));
      for(const row of r.rows)for(const mode of ["offline","live"] as const)if(row[mode].status==="PASS")unique(row[mode].proofIds);
      same(canonicalRows(r.rows), canonicalRows(evaluated.rows), "invalid_report");
      same(sorted(r.supportedToolIds), sorted(evaluated.claims.flatMap(c => c.supported).filter((id,i,a) => a.indexOf(id) === i)), "invalid_report");
      unique(r.supportedToolIds);
      return parseGateResult({claims:evaluated.claims,errors:[]});
    } catch (error) {
      const known: ReasonCode[] = ["invalid_report","incomplete_evidence","scope_changed","inventory_gap","cleanup_failed","executor_failed"];
      const code = error instanceof Error && known.includes(error.message as ReasonCode) ? error.message as ReasonCode : "invalid_report";
      return parseGateResult({claims:[],errors:[code]});
    }
  }});
}

type EvidenceInput = Omit<RunReport,"rows" | "supportedToolIds" | "inventoryComplete">;
/** Pure report assembly; callers own bounded artifact persistence. Never executes tools. */
export function assembleReport(input: EvidenceInput, context: EvidenceContext, expectedScopes: readonly string[], expectedCases: readonly CaseSpec[]): RunReport {
  // The first parse still enforces all event/proof/result joins before deriving claims.
  const r = parseRunReport({...input,rows:[],supportedToolIds:[],inventoryComplete:context.turns.every(t => t.inventoryComplete)});
  const evaluated = evaluate(r,expectedScopes,expectedCases,context);
  const report = parseRunReport({...r,rows:evaluated.rows,supportedToolIds:sorted([...new Set(evaluated.claims.flatMap(c => c.supported))])});
  const checked = createEvidenceGate(context).validate(report,expectedScopes,expectedCases);
  if (checked.errors.length) reject(checked.errors[0]);
  return report;
}

function evaluate(r: RunReport, expectedScopes: readonly string[], expectedCases: readonly CaseSpec[], context: EvidenceContext) {
  unique(expectedScopes); unique(context.scopes.map(s => s.hash)); unique(expectedCases.map(c => c.id));
  same(sorted(r.scopes.map(s => s.hash)),sorted(expectedScopes),"scope_changed");
  same(sorted(context.scopes.map(s => s.hash)),sorted(expectedScopes),"scope_changed");
  if (!expectedScopes.length || r.mode !== context.mode) reject("scope_changed");
  if (r.caseManifestHash !== context.caseManifestHash || r.criteriaMappingHash !== context.criteriaMappingHash) reject("scope_changed");
  for(const s of context.scopes) same(r.scopes.find(x => x.hash === s.hash),parseScopeFingerprint(s),"scope_changed");
  const specs = expectedCases.map(c => validateCaseSpec(c,context.limits));
  const byCase = new Map(specs.map(c => [c.id,c]));
  const events = new Map(r.events.map(e => [e.sequence,e]));
  const proofs = new Map(r.proofs.map(p => [p.id,p]));
  const claimedEvents = new Set<number>(), usedProofs = new Set<string>(), expectedCallUses = new Set<string>();
  const get = (n:number): TraceEvent => events.get(n) ?? reject();
  const turns = context.turns;
  unique(turns.map(t => `${t.snapshot.scope.hash}:${t.sessionId}:${t.requestId}`));
  if (!turns.length) reject("inventory_gap");
  for (const turn of turns) {
    parseInventorySnapshot(turn.snapshot);
    same(turn.snapshot.scope,context.scopes.find(s => s.hash === turn.snapshot.scope.hash),"scope_changed");
  }
  const turnFor = (e: TraceEvent) => turns.find(t => t.snapshot.scope.hash === e.scopeHash && t.sessionId === e.sessionId && t.requestId === e.requestId) ?? reject("inventory_gap");
  for(const e of r.events) {
    if (!byCase.has(e.caseId) || e.requestKind !== "primary") reject();
    const turn = turnFor(e);
    if ("toolId" in e && e.kind !== "unsupported" && e.kind !== "state_observed") {
      const tool = turn.snapshot.offered.find(t => t.registryId === e.toolId);
      if (!tool || tool.schemaHash !== e.toolSchemaHash || tool.originHash !== e.originHash) reject("inventory_gap");
    }
  }
  if (!r.cleanupComplete) reject("cleanup_failed");
  // Account for every effective primary offering, including a continuation's new tools.
  const inventoryComplete = turns.every(t => t.inventoryComplete && t.gaps.length === 0 &&
    [...t.snapshot.registered,...t.snapshot.offered].every(tool=>specs.some(c=>c.toolIds.includes(tool.registryId))||t.snapshot.classifiedAbsent.some(row=>row.tool.registryId===tool.registryId)));
  if (r.inventoryComplete !== inventoryComplete) reject("inventory_gap");
  const inventories = context.scopes.map(s => {
    const scoped = turns.filter(t => t.snapshot.scope.hash === s.hash);
    const union = (kind:"registered"|"offered") => {
      const byId = new Map<string,typeof scoped[number]["snapshot"][typeof kind][number]>();
      for (const t of scoped) for (const tool of t.snapshot[kind]) {
        const prior = byId.get(tool.registryId);
        if (prior) same(prior,tool,"scope_changed");
        byId.set(tool.registryId,tool);
      }
      return [...byId.values()].sort((a,b) => a.registryId.localeCompare(b.registryId));
    };
    const registered = union("registered"), offered = union("offered");
    const supplied = r.inventories.find(i => i.scope.hash === s.hash) ?? reject("inventory_gap");
    const normalizeRegistry = (tools: readonly (typeof registered)[number][]) => tools.map(({offeredName:_,...t}) => t).sort((a,b)=>a.registryId.localeCompare(b.registryId));
    same(normalizeRegistry(supplied.registered),normalizeRegistry(registered),"inventory_gap");
    same([...supplied.offered].sort((a,b)=>a.registryId.localeCompare(b.registryId)),offered,"inventory_gap");
    const absent = scoped.flatMap(t=>t.snapshot.classifiedAbsent).filter(row=>!offered.some(t=>t.registryId===row.tool.registryId));
    const absentById = new Map(absent.map(row=>[row.tool.registryId,row]));
    same([...supplied.classifiedAbsent].sort((a,b)=>a.tool.registryId.localeCompare(b.tool.registryId)),[...absentById.values()].sort((a,b)=>a.tool.registryId.localeCompare(b.tool.registryId)),"inventory_gap");
    if (!scoped.length) reject("inventory_gap");
    return {scope:s,registered,offered,absent:absentById};
  });
  unique(r.inventories.map(i=>i.scope.hash));
  if(r.inventories.length!==inventories.length)reject("inventory_gap");

  const accepted = new Map<string,string[]>();
  const passedResults = new Set<CaseResult>();
  const countByCase = new Map<string,number>();
  for (const e of r.events) countByCase.set(`${e.scopeHash}:${e.caseId}`,(countByCase.get(`${e.scopeHash}:${e.caseId}`)??0)+1);
  if ([...countByCase.values()].some(n=>n>context.limits.eventsPerCase)) reject();
  const callIds = new Set<string>(), wireIds = new Set<string>(), continuationIds = new Set<string>();
  const referencedEvents = new Set<number>();
  for(const result of r.caseResults) {
    const spec = byCase.get(result.caseId) ?? reject();
    if(!spec.toolIds.includes(result.toolId)) reject();
    unique(result.eventReferences);
    const refs = result.eventReferences.map(get).sort((a,b)=>a.sequence-b.sequence);
    for(const e of refs)referencedEvents.add(e.sequence);
    const relevant = r.events.filter(e => e.caseId===result.caseId && e.scopeHash===result.scopeHash && e.sessionId===result.sessionId && e.chainId===result.chainId && (!("toolId" in e) || e.toolId===result.toolId));
    if(result.status === "FAIL") reject("executor_failed");
    if(result.status === "UNVERIFIED" && result.observedOutcome === "not_observed") {
      if(result.proofId || result.cancellation || refs.length)reject();
      continue;
    }
    if(result.observedOutcome!==spec.expectedOutcome)reject();
    const has = (kind:TraceEvent["kind"]) => refs.filter(e=>e.kind===kind);
    for(const kind of ["model_call","executor_started","executor_completed","next_model_request","denied","error"] as const)if(has(kind).length>1)reject();
    for(const kind of spec.requiredObservations)if(!has(kind).length)reject();
    if(spec.executorExpectation==="must_not_start" && relevant.some(e=>e.kind==="executor_started"||e.kind==="executor_completed"))reject();
    if(spec.executorExpectation==="must_complete" && (!has("executor_started").length||!has("executor_completed").length))reject();
    if(spec.expectedOutcome!=="cancelled" && (result.cancellation || relevant.some(e=>e.kind==="host_cancelled")))reject();
    const callEvents = refs.filter((e):e is TraceEvent & {call:CallCorrelation} => "call" in e && !!e.call);
    const firstCall = callEvents[0]?.call;
    if (firstCall) for(const e of callEvents)same(e.call,firstCall);
    const expectedCalls = spec.expectedCalls.filter(c=>c.toolId===result.toolId);
    const matchedCall = firstCall ? expectedCalls.find(c=>c.inputHash===firstCall.inputHash &&
      (firstCall.link==="host_exposed" ? firstCall.fixtureNonceHash===undefined||firstCall.fixtureNonceHash===c.fixtureNonceHash : firstCall.fixtureNonceHash===c.fixtureNonceHash)) : expectedCalls.length===1 ? expectedCalls[0] : undefined;
    if(!matchedCall)reject();
    const expectationKey = `${resultKey(result)}:${matchedCall.fixtureNonceHash}`;
    if(expectedCallUses.has(expectationKey))reject(); expectedCallUses.add(expectationKey);
    for(const e of refs)if(["executor_completed","next_model_request","denied","error"].includes(e.kind) && e.observationHash!==matchedCall.observationHash)reject();
    for(const e of has("state_observed"))if(e.observationHash!==spec.expectedObservationHash)reject();
    // Required terminal negative observations carry the authoritative fixture hash too.
    if(spec.expectedOutcome==="unsupported" && has("unsupported").some(e=>e.observationHash!==spec.expectedObservationHash))reject();
    for(const e of callEvents) {
      if(e.kind === "model_call") {
        const callKey = `${e.scopeHash}:${e.sessionId}:${e.requestId}:${e.call.hostCallId}`;
        const wireKey = `${e.scopeHash}:${e.sessionId}:${e.requestId}:${e.kind==="model_call" ? e.wireCallId : ""}`;
        if(callIds.has(callKey)||wireIds.has(wireKey)||e.wireCallId!==e.call.upstreamCallId)reject();
        callIds.add(callKey);wireIds.add(wireKey);
      }
      if(e.kind === "next_model_request") {
        const key=`${e.scopeHash}:${e.sessionId}:${e.requestId}:${e.wireCallId}`;
        if(continuationIds.has(key))reject();continuationIds.add(key);
      }
    }
    if (spec.positiveRequired) {
      if(relevant.some(e=>["denied","error","unsupported"].includes(e.kind)))reject();
      if(result.status!=="PASS" || !result.proofId)reject();
      const proof = proofs.get(result.proofId) ?? reject();
      if(usedProofs.has(proof.id)||proof.primaryRequestId===proof.nextRequestId)reject();usedProofs.add(proof.id);
      const ids=[proof.definitionEvent,proof.modelCallEvent,proof.executorStartEvent,proof.executorEndEvent,proof.nextRequestEvent,proof.finalResponseEvent];
      if(ids.some(n=>!result.eventReferences.includes(n)))reject();
      for(const n of [proof.modelCallEvent,proof.executorStartEvent,proof.executorEndEvent,proof.nextRequestEvent]) {
        if(claimedEvents.has(n))reject();claimedEvents.add(n);
      }
      const offered=turnFor(get(proof.modelCallEvent)).snapshot.offered.find(t=>t.registryId===result.toolId) ?? reject();
      const provenance=turnFor(get(proof.modelCallEvent)).provenance.find(p=>p.registryId===result.toolId)?.observation;
      if(offered.origin==="unknown"||!offered.originHash||!provenance||provenance.kind!==offered.origin||provenance.configurationHash!==offered.originHash)reject("inventory_gap");
      if(get(proof.executorEndEvent).observationHash!==matchedCall.observationHash || get(proof.nextRequestEvent).observationHash!==matchedCall.observationHash)reject();
      const key=`${result.scopeHash}:${result.toolId}`;
      accepted.set(key,[...(accepted.get(key)??[]),proof.id]);
    } else {
      if(result.proofId)reject();
      validateNegative(refs,spec,result);
    }
    if(result.status==="PASS")passedResults.add(result);
  }
  if(usedProofs.size!==r.proofs.length)reject();
  if(referencedEvents.size!==r.events.length)reject();
  // Every authoritative applicable case, including required negatives, is accounted for.
  for(const inv of inventories) for(const spec of specs) for(const id of spec.toolIds) {
    if(![...inv.registered,...inv.offered].some(t=>t.registryId===id))continue;
    const results=r.caseResults.filter(c=>c.scopeHash===inv.scope.hash&&c.caseId===spec.id&&c.toolId===id);
    if(results.length!==spec.expectedCalls.filter(c=>c.toolId===id).length)reject();
    if(!spec.positiveRequired && results.some(c=>!passedResults.has(c) && !(spec.cancellation?.completionRace==="mark_unverified"&&c.status==="UNVERIFIED"&&c.cancellation)))reject();
  }
  const rows: CoverageRow[] = [];
  const claims = inventories.map(inv=> {
    const supported: string[]=[];
    for(const tool of [...new Map([...inv.registered,...inv.offered].map(t=>[t.registryId,t])).values()]) {
      const applicable=specs.filter(c=>c.positiveRequired&&c.toolIds.includes(tool.registryId));
      const complete=applicable.length>0 && applicable.every(c=>r.caseResults.filter(x=>x.scopeHash===inv.scope.hash&&x.caseId===c.id&&x.toolId===tool.registryId).every(x=>passedResults.has(x)));
      const ids=accepted.get(`${inv.scope.hash}:${tool.registryId}`)??[];
      const provedUnsupported=r.caseResults.some(c=>c.scopeHash===inv.scope.hash&&c.toolId===tool.registryId&&c.observedOutcome==="unsupported"&&passedResults.has(c));
      const outcome=complete&&ids.length ? {status:"PASS" as const,proofIds:sorted(ids)} : inv.absent.get(tool.registryId)?.[r.mode]??
        (provedUnsupported?{status:"UNSUPPORTED" as const,reason:"unsupported_protocol" as const}:unverified());
      if(outcome.status==="PASS")supported.push(tool.registryId);
      rows.push({tool,scopeHash:inv.scope.hash,offline:r.mode==="offline"?outcome:unverified(),live:r.mode==="live"?outcome:unverified()});
    }
    const negativesComplete=specs.filter(c=>!c.positiveRequired).every(c=>r.caseResults.filter(x=>x.scopeHash===inv.scope.hash&&x.caseId===c.id).every(x=>passedResults.has(x)&&x.observedOutcome!=="unsupported"));
    const scopedInventoryComplete=turns.filter(t=>t.snapshot.scope.hash===inv.scope.hash).every(t=>t.inventoryComplete&&t.gaps.length===0);
    return {scopeHash:inv.scope.hash,mode:r.mode,supported:sorted(supported),complete:scopedInventoryComplete&&negativesComplete&&inv.offered.length>0&&inv.offered.every(t=>supported.includes(t.registryId))};
  });
  return {rows,claims};
}

function validateNegative(refs: readonly TraceEvent[], spec: CaseSpec, result: CaseResult): void {
  const ofKind=(kind:TraceEvent["kind"])=>refs.filter(e=>e.kind===kind);
  if(spec.expectedOutcome!=="cancelled") {
    if(result.status!=="PASS")reject();
    const order=["definition","model_call",spec.expectedOutcome,"next_model_request","final_response"] as const;
    let previous=0;
    for(const kind of order){const e=ofKind(kind as TraceEvent["kind"])[0];if(e){if(e.sequence<=previous)reject();previous=e.sequence;}}
    const model=ofKind("model_call")[0], next=ofKind("next_model_request")[0], final=ofKind("final_response")[0];
    if(model&&next&&(model.requestId===next.requestId||final?.requestId!==next.requestId))reject();
    if(model)for(const e of refs)if(["definition","executor_started","executor_completed","denied","error"].includes(e.kind)&&e.requestId!==model.requestId)reject();
    const terminal=ofKind(spec.expectedOutcome as TraceEvent["kind"])[0];
    if(terminal&&ofKind("state_observed").some(e=>e.sequence<=terminal.sequence))reject();
    const started=ofKind("executor_started")[0],completed=ofKind("executor_completed")[0];
    if(started&&model&&started.sequence<=model.sequence)reject();
    if(started&&terminal&&started.sequence>=terminal.sequence)reject();
    if(completed&&(!started||completed.sequence<=started.sequence))reject();
    if(completed&&next&&completed.sequence>=next.sequence)reject();
    return;
  }
  const cp=result.cancellation, expected=spec.cancellation;
  if(!cp||!expected)reject();
  const indexed=new Map(refs.map(e=>[e.sequence,e]));
  const observations=[["hostEvent","host_cancelled",expected.host],["executorEvent","executor_released",expected.executor],["providerEvent","provider_aborted",expected.provider],["mcpEvent","fixture_cancelled",expected.mcp]] as const;
  for(const [field,kind,required] of observations){const n=cp[field];if(required&&(n===undefined||indexed.get(n)?.kind!==kind))reject();if(n!==undefined&&indexed.get(n)?.kind!==kind)reject();}
  const host=indexed.get(cp.hostEvent), state=indexed.get(cp.stateEvent);
  if(!host||state?.kind!=="state_observed"||state.sequence<=host.sequence||state.observationHash!==spec.expectedObservationHash)reject();
  const definition=ofKind("definition")[0],model=ofKind("model_call")[0];
  if(!definition||!model||definition.sequence>=model.sequence||model.sequence>=host.sequence)reject();
  if(definition.requestId!==model.requestId||model.requestId!==host.requestId)reject();
  if(cp.commitState==="unknown")reject();
  const completed=ofKind("executor_completed");
  if(cp.commitState==="observed") {
    if(!completed.length||completed.some(e=>e.sequence>=host.sequence)||spec.cancellation?.completionRace!=="mark_unverified"||result.status!=="UNVERIFIED")reject();
  } else if(completed.length||result.status!=="PASS")reject();
  for(const [field,,required]of observations)if(required&&field!=="hostEvent"&&(cp[field]!<=host.sequence||cp[field]!>=state.sequence))reject();
  if(ofKind("executor_started").some(e=>e.sequence>=host.sequence||e.sequence<=model.sequence||e.requestId!==host.requestId))reject();
}
