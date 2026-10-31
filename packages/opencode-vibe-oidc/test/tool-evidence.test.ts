import { describe, expect, test } from "bun:test";
import vector from "./tool-compatibility/vectors/report-v1.json" with { type: "json" };
import conformance from "./tool-compatibility/vectors/evidence-v1.json" with { type: "json" };
import { fixtureHash } from "./tool-compatibility/cases.js";
import { assembleReport, createEvidenceGate } from "./tool-compatibility/evidence.js";
import { parseGateResult } from "./tool-compatibility/contracts.js";
import type { EvidenceContext } from "./tool-compatibility/evidence.js";
import type { CaseSpec, RunReport } from "./tool-compatibility/contracts.js";
import { reconcileTurn } from "./tool-compatibility/inventory.js";

// Synthetic graphs exercise gate logic only. They never demonstrate host execution.
function sample() {
  const r: any = structuredClone(vector.report);
  const spec = structuredClone(vector.caseSpec) as CaseSpec;
  const definition = {description:"Owned synthetic read", inputSchema:{type:"object"}};
  const schemaHash = fixtureHash(definition), originHash = "b".repeat(64);
  for (const t of [r.rows[0].tool,...r.inventories[0].registered,...r.inventories[0].offered]) Object.assign(t,{schemaHash,originHash});
  for (const item of [...r.events,...r.proofs]) if ("toolId" in item) Object.assign(item,{toolSchemaHash:schemaHash,originHash});
  const scope = structuredClone(r.scopes[0]);
  const turn = (requestId: string) => reconcileTurn({scope,sessionId:"session-1",requestId,requestKind:"primary",
    registry:[{registryId:"read",declaredName:"read",definition,provenance:{kind:"native",observationId:"registry-1",configurationHash:originHash}}],mcp:[],
    offerings:[{registryId:"read",offeredName:"read",definition}],absences:[],assignments:[{scopeHash:scope.hash,tool:r.rows[0].tool,caseSpec:spec}]});
  const context: EvidenceContext = {mode:"offline",scopes:[scope],caseManifestHash:r.caseManifestHash,criteriaMappingHash:r.criteriaMappingHash,
    limits:vector.runSpec.limits,turns:[turn("request-1"),turn("request-2")]};
  const cases = [spec];
  const gate = () => createEvidenceGate(context);
  const validate = () => gate().validate(r as RunReport,[scope.hash],cases);
  return {r,context,cases,validate,gate};
}

function negative(outcome:"denied"|"error"|"cancelled"|"unsupported") {
  const s=sample(), r=s.r;
  const required = outcome==="cancelled" ? ["definition","model_call","executor_started","state_observed"] : outcome==="unsupported" ? ["unsupported"] : ["definition","model_call",outcome,"state_observed","next_model_request","final_response"];
  const c:CaseSpec={...s.cases[0]!,positiveRequired:false,expectedOutcome:outcome,
    requiredObservations:required as CaseSpec["requiredObservations"],executorExpectation:outcome==="cancelled"?"observe_if_started":"must_not_start",
    ...(outcome==="cancelled"?{cancellation:{host:true,executor:true,provider:true,mcp:false,completionRace:"mark_unverified"}}:{})};
  s.cases[0]=c;
  const original=r.events;
  const state={...original[0],kind:"state_observed",observationHash:c.expectedObservationHash};
  const denial={...original[1],kind:outcome,observationHash:c.expectedCalls[0]!.observationHash};delete denial.wireCallId;
  if(outcome==="cancelled") {
    const life=(kind:string)=>{const e={...original[0],kind,call:original[1].call};delete e.toolId;delete e.toolSchemaHash;delete e.originHash;return e;};
    r.events=[original[0],original[1],original[2],life("host_cancelled"),life("executor_released"),life("provider_aborted"),state];
  } else if(outcome==="unsupported") r.events=[{...state,kind:"unsupported"}];
  else r.events=[original[0],original[1],denial,state,original[4],original[5]];
  r.events.forEach((e:any,i:number)=>e.sequence=i+1);
  const result:any={...r.caseResults[0],observedOutcome:outcome,eventReferences:r.events.map((e:any)=>e.sequence)};
  delete result.proofId;
  if(outcome==="cancelled")result.cancellation={hostEvent:4,executorEvent:5,providerEvent:6,stateEvent:7,commitState:"absent"};
  r.proofs=[];r.caseResults=[result];r.supportedToolIds=[];r.rows[0].offline=outcome==="unsupported"?{status:"UNSUPPORTED",reason:"unsupported_protocol"}:{status:"UNVERIFIED",reason:"not_exercised"};
  return s;
}

function parallel() {
  const s=sample(),r=s.r, first=r.proofs[0], original=structuredClone(r.events);
  r.events[3].sequence=7;r.events[4].sequence=8;r.events[5].sequence=10;
  Object.assign(first,{executorEndEvent:7,nextRequestEvent:8,finalResponseEvent:10});
  r.caseResults[0].eventReferences=[1,2,3,7,8,10];
  const call={...original[1].call,hostCallId:"call-2",upstreamCallId:"upstream-2",inputHash:"c".repeat(64)};
  const extra=[{...original[1],sequence:4,call,wireCallId:"upstream-2"},{...original[2],sequence:5,call},
    {...original[3],sequence:6,call,observationHash:"d".repeat(64)},{...original[4],sequence:9,call,wireCallId:"upstream-2",observationHash:"d".repeat(64)}];
  r.events.push(...extra);r.events.sort((a:any,b:any)=>a.sequence-b.sequence);
  r.proofs.push({...first,id:"proof-2",hostCallId:"call-2",upstreamCallId:"upstream-2",continuationWireCallId:"upstream-2",modelCallEvent:4,executorStartEvent:5,executorEndEvent:6,nextRequestEvent:9});
  r.caseResults.push({...r.caseResults[0],proofId:"proof-2",eventReferences:[1,4,5,6,9,10]});
  r.rows[0].offline.proofIds.push("proof-2");
  s.cases[0]={...s.cases[0]!,expectedCalls:[...s.cases[0]!.expectedCalls,{toolId:"read",fixtureNonceHash:"e".repeat(64),inputHash:"c".repeat(64),observationHash:"d".repeat(64)}]};
  return s;
}

describe("scoped evidence gate (synthetic graphs only)", () => {
  for (const v of conformance.vectors) test(`shared Python/TS vector: ${v.name}`, () => {
    expect(conformance.synthetic).toBe(true);
    const context = v.context as EvidenceContext;
    const cases = v.expectedCases as readonly CaseSpec[];
    const report = v.report as RunReport;
    expect(createEvidenceGate(context).validate(report, v.expectedScopes, cases)).toEqual(parseGateResult(v.expectedResult));
    if (!v.expectedResult.errors.length) {
      const {rows:_, supportedToolIds:__, inventoryComplete:___, ...input} = report;
      expect(assembleReport(input, context, v.expectedScopes, cases)).toEqual(report);
    }
  });
  test("valid graph credits offline read only", () => {
    const s = sample();
    expect(s.validate()).toEqual({errors:[],claims:[{scopeHash:s.context.scopes[0]!.hash,mode:"offline",supported:["read"],complete:true}]});
  });
  test("pure assembly derives coverage and returns immutable runtime-validated output",()=>{
    const s=sample();const {rows:_,supportedToolIds:__,inventoryComplete:___,...input}=s.r;
    const r=assembleReport(input,s.context,[s.context.scopes[0]!.hash],s.cases);
    expect(r).toEqual(s.r);expect(Object.isFrozen(r)).toBe(true);
  });
  for(const outcome of ["denied","error","cancelled","unsupported"] as const)test(`expected ${outcome} earns no positive credit`,()=>{
    const s=negative(outcome);const result=s.validate();
    expect(result.errors).toEqual([]);expect(result.claims[0]).toEqual({scopeHash:s.context.scopes[0]!.hash,mode:"offline",supported:[],complete:false});
  });
  const cancelMutations:Record<string,(s:ReturnType<typeof negative>)=>void>={
    "missing provider abort":s=>{delete s.r.caseResults[0].cancellation.providerEvent;},
    "missing executor release":s=>{delete s.r.caseResults[0].cancellation.executorEvent;},
    "missing state evidence":s=>{s.r.caseResults[0].eventReferences.pop();},
    "unknown commit state":s=>{s.r.caseResults[0].cancellation.commitState="unknown";},
    "observed state without completion":s=>{s.r.caseResults[0].cancellation.commitState="observed";s.r.caseResults[0].status="UNVERIFIED";},
    "state before host cancellation":s=>{s.r.caseResults[0].cancellation.stateEvent=1;},
    "release before cancellation":s=>{s.r.events[4].sequence=3;s.r.events[2].sequence=5;},
    "crossed lifecycle call":s=>{s.r.events[5].call={...s.r.events[5].call,hostCallId:"crossed"};},
    "lifecycle outside result":s=>{s.r.caseResults[0].eventReferences=s.r.caseResults[0].eventReferences.filter((n:number)=>n!==6);},
    "forged state hash":s=>{s.r.events[6].observationHash="c".repeat(64);},
    "state observed before executor release":s=>{s.r.events[4].sequence=7;s.r.events[6].sequence=5;s.r.caseResults[0].cancellation.executorEvent=7;s.r.caseResults[0].cancellation.stateEvent=5;},
  };
  for(const [name,mutate]of Object.entries(cancelMutations))test(`cancellation rejects ${name}`,()=>{const s=negative("cancelled");mutate(s);expect(s.validate().errors.length).toBeGreaterThan(0);});
  test("UNVERIFIED explicit non-execution is inventory complete but no compatibility credit",()=>{
    const s=sample();s.r.events=[];s.r.proofs=[];s.r.caseResults[0]={...s.r.caseResults[0],status:"UNVERIFIED",observedOutcome:"not_observed",eventReferences:[]};delete s.r.caseResults[0].proofId;
    s.r.rows[0].offline={status:"UNVERIFIED",reason:"not_exercised"};s.r.supportedToolIds=[];
    expect(s.validate()).toEqual({errors:[],claims:[{scopeHash:s.context.scopes[0]!.hash,mode:"offline",supported:[],complete:false}]});
  });
  test("context is snapshotted before validation",()=>{
    const s=sample(),gate=s.gate();s.context.scopes=[{...s.context.scopes[0]!,model:"stale"}];
    expect(gate.validate(s.r,[s.r.scopes[0].hash],s.cases).errors).toEqual([]);
  });
  test("parallel reversed completion keeps separate proofs and shares enclosing response",()=>{
    const s=parallel();expect(s.validate().errors).toEqual([]);expect(s.validate().claims[0]?.supported).toEqual(["read"]);
  });
  test("parallel result observation cannot be crossed",()=>{
    const s=parallel();s.r.events.find((e:any)=>e.sequence===9).observationHash="a".repeat(64);expect(s.validate().errors.length).toBeGreaterThan(0);
  });
  test("parallel wire mappings are one-to-one",()=>{
    const s=parallel();s.r.events.find((e:any)=>e.sequence===9).wireCallId="upstream-1";s.r.proofs[1].continuationWireCallId="upstream-1";
    expect(s.validate().errors.length).toBeGreaterThan(0);
  });
  test("completion race remains UNVERIFIED when the manifest permits it",()=>{
    const s=negative("cancelled");
    for(const e of s.r.events)if(e.sequence>=4)e.sequence++;
    const completion={...s.r.events[2],kind:"executor_completed",sequence:4,observationHash:s.cases[0]!.expectedCalls[0]!.observationHash};s.r.events.splice(3,0,completion);
    s.r.caseResults[0].status="UNVERIFIED";s.r.caseResults[0].eventReferences=s.r.events.map((e:any)=>e.sequence);
    s.r.caseResults[0].cancellation={hostEvent:5,executorEvent:6,providerEvent:7,stateEvent:8,commitState:"observed"};
    expect(s.validate().errors).toEqual([]);expect(s.validate().claims[0]?.complete).toBe(false);
    s.r.caseResults[0].status="PASS";expect(s.validate().errors.length).toBeGreaterThan(0);
    s.r.caseResults[0].status="UNVERIFIED";s.cases[0]={...s.cases[0]!,cancellation:{...s.cases[0]!.cancellation!,completionRace:"fail_case"}};
    expect(s.validate().errors.length).toBeGreaterThan(0);
  });
  test("MCP cancellation requires a distinct fixture receipt",()=>{
    const s=negative("cancelled");s.cases[0]={...s.cases[0]!,fixtureKind:"mcp",cancellation:{...s.cases[0]!.cancellation!,mcp:true}};
    expect(s.validate().errors.length).toBeGreaterThan(0);
    s.r.events[6].sequence=8;const receipt={...s.r.events[5],sequence:7,kind:"fixture_cancelled"};s.r.events.splice(6,0,receipt);
    s.r.caseResults[0].eventReferences=s.r.events.map((e:any)=>e.sequence);Object.assign(s.r.caseResults[0].cancellation,{mcpEvent:7,stateEvent:8});
    expect(s.validate().errors).toEqual([]);
  });
  test("live evidence requires an explicit independent expected mode",()=>{
    const s=sample();s.context.mode="live";s.r.mode="live";s.r.rows[0].live=s.r.rows[0].offline;s.r.rows[0].offline={status:"UNVERIFIED",reason:"not_exercised"};
    expect(s.validate().errors).toEqual([]);expect(s.validate().claims[0]?.mode).toBe("live");
  });
  test("executed subset excludes an offered unexercised tool even with complete inventory",()=>{
    const s=sample();const tool={...s.r.rows[0].tool,registryId:"write",declaredName:"write",offeredName:"write"};
    const c:CaseSpec={...s.cases[0]!,id:"write-positive",toolIds:["write"],expectedCalls:s.cases[0]!.expectedCalls.map(call=>({...call,toolId:"write"}))};
    s.cases.push(c);s.r.inventories[0].registered.push(tool);s.r.inventories[0].offered.push(tool);
    s.r.rows.push({tool,scopeHash:s.r.scopes[0].hash,offline:{status:"UNVERIFIED",reason:"not_exercised"},live:{status:"UNVERIFIED",reason:"not_exercised"}});
    s.r.caseResults.push({...s.r.caseResults[0],caseId:c.id,toolId:"write",status:"UNVERIFIED",observedOutcome:"not_observed",eventReferences:[]});delete s.r.caseResults[1].proofId;
    s.context.turns=s.context.turns.map(turn=>{
      const t:any=structuredClone(turn);t.snapshot.registered.push({...tool,offeredName:undefined});delete t.snapshot.registered[1].offeredName;t.snapshot.offered.push(tool);
      t.provenance.push({registryId:"write",observation:{...t.provenance[0].observation}});t.effectiveDefinitions.push({...t.effectiveDefinitions[0],registryId:"write",offeredName:"write"});return t;
    });
    expect(s.validate()).toEqual({errors:[],claims:[{scopeHash:s.r.scopes[0].hash,mode:"offline",supported:["read"],complete:false}]});
  });
  test("inventory gap remains separate from the supported subset",()=>{
    const s=sample();s.context.turns=s.context.turns.map(t=>({...t,inventoryComplete:false,gaps:[{code:"new_tool",toolId:"read"}]}));s.r.inventoryComplete=false;
    expect(s.validate()).toEqual({errors:[],claims:[{scopeHash:s.r.scopes[0].hash,mode:"offline",supported:["read"],complete:false}]});
    s.r.inventoryComplete=true;expect(s.validate().errors).toEqual(["inventory_gap"]);
  });
  test("negative PASS cannot supply a positive coverage row",()=>{
    const s=negative("denied");s.r.rows[0].offline={status:"PASS",proofIds:["proof-1"]};
    expect(s.validate().errors.length).toBeGreaterThan(0);
  });
  test("denied tool never starts even with executor events omitted from result refs",()=>{
    const s=negative("denied");const e={...s.r.events[1],sequence:7,kind:"executor_started"};delete e.wireCallId;s.r.events.push(e);
    expect(s.validate().errors.length).toBeGreaterThan(0);
  });
  test("denial side-effect state must be inspected after the denied operation",()=>{
    const s=negative("denied");s.r.events[2].sequence=4;s.r.events[3].sequence=3;expect(s.validate().errors.length).toBeGreaterThan(0);
  });
  test("executed error requires completed execution before its continuation",()=>{
    const s=negative("error");s.cases[0]={...s.cases[0]!,executorExpectation:"must_complete",requiredObservations:[...s.cases[0]!.requiredObservations,"executor_started","executor_completed"]};
    for(const e of s.r.events)if(e.sequence>=3)e.sequence+=2;
    const start={...s.r.events[1],sequence:3,kind:"executor_started"};delete start.wireCallId;
    const complete={...start,sequence:4,kind:"executor_completed",observationHash:s.cases[0]!.expectedCalls[0]!.observationHash};
    s.r.events.splice(2,0,start,complete);s.r.caseResults[0].eventReferences=s.r.events.map((e:any)=>e.sequence);
    expect(s.validate().errors).toEqual([]);
    s.r.events[3].sequence=9;s.r.caseResults[0].eventReferences=s.r.events.map((e:any)=>e.sequence);
    expect(s.validate().errors.length).toBeGreaterThan(0);
  });
  const mutations: Record<string,(s:ReturnType<typeof sample>)=>void> = {
    "missing executor":s => {s.r.events.splice(3,1);},
    "missing continuation":s => {s.r.events.splice(4,1);},
    "crossed result":s => {s.r.events[3].call.hostCallId="other";},
    "wrong output observation":s => {s.r.events[3].observationHash="c".repeat(64);},
    "wrong next observation":s => {s.r.events[4].observationHash="c".repeat(64);},
    "authoritative input mismatch":s => {for(const e of s.r.events) if(e.call)e.call.inputHash="c".repeat(64);},
    "manifest nonce mismatch":s => {for(const e of s.r.events) if(e.call)Object.assign(e.call,{link:"unique_fixture_nonce",fixtureNonceHash:"c".repeat(64)});},
    "manifest changed":s => {s.r.caseManifestHash="c".repeat(64);},
    "criteria changed":s => {s.r.criteriaMappingHash="c".repeat(64);},
    "stale model with same scope hash":s => {s.r.scopes[0].model="other";s.r.inventories[0].scope.model="other";},
    "stale mode":s => {s.r.mode="live";s.r.rows[0].live=s.r.rows[0].offline;s.r.rows[0].offline={status:"UNVERIFIED",reason:"not_exercised"};},
    "unknown case":s => {s.cases.splice(0);},
    "missing required negative":s => {s.cases.push({...s.cases[0]!,id:"required-denial",positiveRequired:false,expectedOutcome:"denied",executorExpectation:"must_not_start",requiredObservations:["definition","model_call","denied","state_observed","next_model_request","final_response"]});},
    "reused result events":s => {s.r.proofs.push({...s.r.proofs[0],id:"proof-2"});},
    "reused case proof":s => {s.r.caseResults.push({...s.r.caseResults[0]});},
    "unlinked result references":s => {s.r.caseResults[0].eventReferences=[1,2,6];},
    "unknown provenance":s => {for(const t of [s.r.rows[0].tool,...s.r.inventories[0].registered,...s.r.inventories[0].offered])t.origin="unknown";},
    "false subset":s => {s.r.supportedToolIds=[];},
    "cleanup boolean cannot cover missing stages":s => {s.r.events.pop();s.r.cleanupComplete=true;},
    "cleanup failed":s => {s.r.cleanupComplete=false;},
    "tool no longer offered":s => {s.r.inventories[0].offered=[];},
    "unobserved primary offering":s => {s.context.turns=s.context.turns.slice(0,1);},
    "duplicate positive references":s => {s.r.rows[0].offline.proofIds.push("proof-1");},
    "same primary and next request":s => {s.r.proofs[0].nextRequestId="request-1";s.r.events[4].requestId="request-1";s.r.events[5].requestId="request-1";},
    "wire id crossed":s => {s.r.events[1].wireCallId="other";},
    "negative outcome masquerades as success":s => {s.r.caseResults[0].observedOutcome="denied";},
    "missing result":s => {s.r.caseResults=[];},
  };
  for(const [name,mutate] of Object.entries(mutations))test(name,()=>{
    const s=sample();mutate(s);const result=s.validate();expect(result.errors.length).toBeGreaterThan(0);expect(result.claims).toEqual([]);
  });
});
