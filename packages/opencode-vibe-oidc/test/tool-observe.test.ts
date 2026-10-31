import { expect, test } from "bun:test";
import vectors from "./tool-compatibility/vectors/evidence-v1.json" with {type:"json"};
import { createRoundTripRecorder, inspectOwnedRequest, tapOwnedResponse, hashOwnedFixture } from "./tool-compatibility/observe.js";
import { createEvidenceGate } from "./tool-compatibility/evidence.js";
import type { EvidenceContext } from "./tool-compatibility/evidence.js";
import type { CaseSpec, RunReport } from "./tool-compatibility/contracts.js";
import { parseCapabilityReport } from "./tool-compatibility/capability.js";
import { fixtureHash } from "./tool-compatibility/cases.js";

function sample(index=0) {
  const v=structuredClone(vectors.vectors[index]!);
  let cancelled=0;
  const recorder=createRoundTripRecorder({runId:v.report.runId,cases:v.expectedCases as CaseSpec[],limits:v.context.limits,cancelOwnedTurn:()=>{cancelled++;}});
  return {v,recorder,cancelled:()=>cancelled};
}
test("recorder emits immutable complete proof accepted by the frozen independent gate",()=>{
  const {v,recorder}=sample();
  for(const e of v.report.events)recorder.record(e);
  const proof=recorder.finish(v.expectedCases[0]!.id);
  expect("status" in proof).toBe(false);expect(Object.isFrozen(proof)).toBe(true);if("status" in proof)throw new Error(proof.code);
  const report={...v.report,events:recorder.events(),proofs:[proof],caseResults:v.report.caseResults.map(r=>({...r,proofId:proof.id})),rows:v.report.rows.map(r=>({...r,offline:{status:"PASS",proofIds:[proof.id]}}))};
  expect(createEvidenceGate(v.context as EvidenceContext).validate(report as RunReport,v.expectedScopes,v.expectedCases as CaseSpec[]).errors).toEqual([]);
});
test("crossed IDs, private fields and oversized capture cancel the owned turn once",()=>{
  for(const mutate of [(e:any)=>{e.raw="private-secret";},(e:any)=>{e.call.hostCallId="crossed";}]) {
    const s=sample();let rejected=false;
    for(const source of s.v.report.events){const e:any=structuredClone(source);if(e.kind==="executor_completed")mutate(e);try{s.recorder.record(e);}catch{rejected=true;}}
    expect(s.recorder.finish(s.v.expectedCases[0]!.id)).toHaveProperty("status","INCOMPLETE");
    expect(rejected || s.cancelled()>0).toBe(true);
  }
  const s=sample();const recorder=createRoundTripRecorder({runId:s.v.report.runId,cases:s.v.expectedCases as CaseSpec[],limits:{...s.v.context.limits,traceBytes:1},cancelOwnedTurn:()=>{}});
  expect(()=>recorder.record(s.v.report.events[0])).toThrow("incomplete_evidence");expect(recorder.events()).toEqual([]);
});
test("bounded request inspection accepts only the preapproved exact fixture shape",async()=>{
  let cancelled=0;const fail=()=>{cancelled++;};
  const request=new Request("https://owned.test",{method:"POST",body:JSON.stringify({input:{nested:["λ",true,2]}})});
  const expected={input:{nested:["λ",true,2]}};
  expect(await inspectOwnedRequest(request,expected,1024,fail)).toMatch(/^[a-f0-9]{64}$/);
  expect(await request.json()).toEqual(expected);
  await expect(inspectOwnedRequest(new Request("https://owned.test",{method:"POST",body:'{"secret":"private"}'}),expected,1024,fail)).rejects.toThrow("redaction_failed");
  await expect(inspectOwnedRequest(new Request("https://owned.test",{method:"POST",body:"x".repeat(30)}),expected,10,fail)).rejects.toThrow("incomplete_evidence");expect(cancelled).toBe(2);
});
test("response tap is pull driven, byte exact, preserves headers and forwards cancellation",async()=>{
  let pulls=0,cancelled=0;const observed:number[]=[];
  const source=new ReadableStream<Uint8Array>({pull(c){pulls++;c.enqueue(new Uint8Array([0,255,pulls]));},cancel(){cancelled++;}},{highWaterMark:0});
  const tapped=tapOwnedResponse(new Response(source,{status:202,headers:{"x-owned":"kept"}}),{maxBytes:30,onChunk:(b:Uint8Array)=>{observed.push(b.length);},cancelOwnedTurn:()=>{}});
  expect(pulls).toBe(0);expect(tapped.status).toBe(202);expect(tapped.headers.get("x-owned")).toBe("kept");
  const reader=tapped.body!.getReader();expect((await reader.read()).value).toEqual(new Uint8Array([0,255,1]));expect(pulls).toBe(1);await reader.cancel();expect(cancelled).toBe(1);expect(observed).toEqual([3]);
});

test("parallel out-of-order completion has distinct immutable proofs accepted by the gate",()=>{
  const s=sample(vectors.vectors.findIndex(v=>v.name==="parallel"));
  for(const e of s.v.report.events)s.recorder.record(e);
  const proofs=s.recorder.finishAll(s.v.expectedCases[0]!.id);expect(Array.isArray(proofs)).toBe(true);if(!Array.isArray(proofs))throw new Error("incomplete");
  const originals=s.v.report.proofs;
  const mapping=new Map(originals.map(p=>[p.id,proofs.find(p2=>p2.hostCallId===p.hostCallId)!.id]));
  const report={...s.v.report,events:s.recorder.events(),proofs,caseResults:s.v.report.caseResults.map(r=>({...r,...("proofId" in r?{proofId:mapping.get(r.proofId)}:{})})),
    rows:s.v.report.rows.map(r=>({...r,offline:{status:"PASS",proofIds:proofs.map(p=>p.id)}}))};
  expect(createEvidenceGate(s.v.context as EvidenceContext).validate(report as RunReport,s.v.expectedScopes,s.v.expectedCases as CaseSpec[]).errors).toEqual([]);
});
for(const mutation of ["missing executor","crossed parallel result","reused nonce","auxiliary final","wrong observation","wire collision","wrong scope"] as const)test(`proof fails closed: ${mutation}`,()=>{
  const index=mutation.includes("parallel")||mutation==="wire collision"?vectors.vectors.findIndex(v=>v.name==="parallel"):0;
  const s=sample(index);const events:any[]=structuredClone(s.v.report.events);
  if(mutation==="missing executor")events.splice(events.findIndex(e=>e.kind==="executor_completed"),1);
  if(mutation==="crossed parallel result")events.find(e=>e.kind==="next_model_request").call.hostCallId="crossed";
  if(mutation==="reused nonce")for(const e of events)if(e.call){e.call.link="unique_fixture_nonce";e.call.fixtureNonceHash="f".repeat(64);}
  if(mutation==="auxiliary final")events.find(e=>e.kind==="final_response").requestKind="title";
  if(mutation==="wrong observation")events.find(e=>e.kind==="executor_completed").observationHash="f".repeat(64);
  if(mutation==="wire collision"){const next=events.filter(e=>e.kind==="next_model_request");next[1].wireCallId=next[0].wireCallId;}
  if(mutation==="wrong scope")events.find(e=>e.kind==="executor_completed").scopeHash="f".repeat(64);
  for(const e of events)s.recorder.record(e);
  expect(s.recorder.finishAll(s.v.expectedCases[0]!.id)).toHaveProperty("status","INCOMPLETE");expect(s.cancelled()).toBe(1);
});
test("tap close, limits, abort and private parser failures retain fixed errors and cancel only once",async()=>{
  for(const mode of ["close","limit","abort","private"] as const){
    let cancelled=0,sourceCancelled=0,closed=0;const abort=new AbortController();
    const source=new ReadableStream<Uint8Array>({pull(c){if(mode==="close")c.close();else c.enqueue(new Uint8Array([1,2,3]));},cancel(){sourceCancelled++;}},{highWaterMark:0});
    const tapped=tapOwnedResponse(new Response(source),{maxBytes:mode==="limit"?2:10,signal:abort.signal,cancelOwnedTurn:()=>{cancelled++;},onClose:()=>{closed++;},onChunk:b=>{if(mode==="private")throw new Error("private-secret");b.fill(0);}});
    const reader=tapped.body!.getReader();if(mode==="abort")abort.abort("private-reason");
    if(mode==="close"){expect((await reader.read()).done).toBe(true);expect(closed).toBe(1);expect(cancelled).toBe(0);}
    else {await expect(reader.read()).rejects.toThrow(mode==="limit"?"incomplete_evidence":mode==="abort"?"cancelled":"redaction_failed");expect(cancelled).toBe(1);expect(sourceCancelled).toBe(1);}
  }
});
test("owned payload validation refuses accessors and unknown media fields before capture",()=>{
  let reads=0;const privateValue={get text(){reads++;return "private";}};
  expect(()=>hashOwnedFixture(privateValue,{text:"owned"})).toThrow("redaction_failed");expect(reads).toBe(0);
  const media={type:"image",mime:"image/png",sha256:"a".repeat(64),size:4};
  expect(hashOwnedFixture(media,media)).toMatch(/^[a-f0-9]{64}$/);
  expect(()=>hashOwnedFixture({...media,privateBase64:"private"},media)).toThrow("redaction_failed");
});
test("tap never changes executor bytes and forwards the original consumer cancellation reason",async()=>{
  let reason:unknown;const expected={owned:true};const bytes=new Uint8Array([0,255,27]);
  const source=new ReadableStream<Uint8Array>({pull(c){c.enqueue(bytes);},cancel(value){reason=value;}},{highWaterMark:0});
  const response=tapOwnedResponse(new Response(source),{maxBytes:100,onChunk:copy=>{copy.fill(13);},cancelOwnedTurn:()=>{}});
  const reader=response.body!.getReader();expect((await reader.read()).value).toEqual(bytes);expect(bytes).toEqual(new Uint8Array([0,255,27]));
  await reader.cancel(expected);expect(reason).toBe(expected);
});
test("unique observed nonce supports differing actual upstream and host call IDs without prefix inference",()=>{
  const s=sample();const nonce=s.v.expectedCases[0]!.expectedCalls[0]!.fixtureNonceHash;
  for(const original of s.v.report.events){const e:any=structuredClone(original);if(e.call){e.call.link="unique_fixture_nonce";e.call.fixtureNonceHash=nonce;e.call.hostCallId="actual-host-id";}s.recorder.record(e);}
  const proof=s.recorder.finish(s.v.expectedCases[0]!.id);expect(proof).toHaveProperty("hostCallId","actual-host-id");expect(proof).toHaveProperty("upstreamCallId","upstream-1");
});

// Synthetic joins only. Capability hashes are independent inputs to the recorder projection.
function recorderCapability() {
  const v=structuredClone(vectors.vectors[0]!);
  const proof=v.report.proofs[0]!,trace=v.report.events;
  proof.hostCallId=proof.upstreamCallId;
  for(const e of trace)if("call" in e&&e.call)e.call.hostCallId=proof.hostCallId;
  const h="a".repeat(64);
  const events=["allowed","denied"].flatMap(c=>["context","transport_request","transport_chunk","input_started","transport_end","input_ended","dispatch",c==="allowed"?"permission_allow":"permission_deny",c==="allowed"?"completed":"error","context","transport_request","continuation","transport_chunk","transport_end","final","state_unchanged"].map(kind=>({case:c,kind,link:"owned-"+c})));
  return {ready:true,status:"observed",code:null,stage:"decision",inventory:[{id:"read",name:"read",schemaHash:h}],
    offered:["allowed","allowed","denied","denied"].map(c=>({case:c,agent:"build",definitions:[{name:"read",schemaHash:proof.toolSchemaHash}]})),
    permissions:[{case:"allowed",action:"read",effect:"allow",owned:true}],counts:{sdk:1,language:1,httpRequest:0,httpResponse:0,requests:4,chunks:68,ends:4,auxiliary:0},events,failureCode:"none",
    links:[{case:"allowed",sessionHash:fixtureHash(proof.sessionId),upstreamCallHash:fixtureHash(proof.upstreamCallId),hostCallHash:fixtureHash(proof.hostCallId),expectedResultHash:trace[3]!.observationHash,actualResultHash:trace[3]!.observationHash},
      {case:"denied",sessionHash:h,upstreamCallHash:h,hostCallHash:h,expectedResultHash:h,actualResultHash:h}],
    profileHash:h,providerBuildHash:h,transport:"fixture-owned-before-provider",denialSource:"execute.after.error.error.Permission.BlockedError",auxiliary:"unverified",live:"unverified",allToolCompatibility:"unverified",recorder:{events:trace,proof}};
}
test("recorder capability accepts the independently bound positive projection",()=>{
  expect(parseCapabilityReport(recorderCapability()).ready).toBe(true);
});
for(const mutation of ["run","session","tool","result","schema","call"] as const)test(`capability recorder rejects unrelated ${mutation} evidence`,()=>{
  const r=recorderCapability();const p=r.recorder.proof;
  if(mutation==="run")r.recorder.events[3]!.runId="unrelated-run";
  if(mutation==="session"){p.sessionId="unrelated-session";for(const e of r.recorder.events)e.sessionId=p.sessionId;}
  if(mutation==="tool"){p.toolId="unrelated-tool";for(const e of r.recorder.events)if("toolId" in e)e.toolId=p.toolId;}
  if(mutation==="result")for(const e of r.recorder.events)if(e.kind==="executor_completed"||e.kind==="next_model_request")e.observationHash="f".repeat(64);
  if(mutation==="schema"){p.toolSchemaHash="f".repeat(64);for(const e of r.recorder.events)if("toolSchemaHash" in e)e.toolSchemaHash=p.toolSchemaHash;}
  if(mutation==="call"){
    p.hostCallId=p.upstreamCallId=p.continuationWireCallId="unrelated-call";
    for(const e of r.recorder.events){if("call" in e&&e.call)e.call.hostCallId=e.call.upstreamCallId=p.hostCallId;if("wireCallId" in e)e.wireCallId=p.hostCallId;}
  }
  expect(()=>parseCapabilityReport(r)).toThrow("HOST_CAPABILITY_REPORT");
});
test("recorder rejects an unrelated run before storage or proof assembly",()=>{
  const s=sample();const e={...s.v.report.events[0]!,runId:"unrelated-run"};
  expect(()=>s.recorder.record(e)).toThrow("incomplete_evidence");expect(s.recorder.events()).toEqual([]);expect(s.cancelled()).toBe(1);
});
