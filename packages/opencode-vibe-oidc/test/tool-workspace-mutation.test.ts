import { test, expect } from "bun:test";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { state } from "./preload.js";
import { createWorkspaceMutations, inspectOwnedWorkspace, inspectMutationTransition } from "./tool-compatibility/fixtures/workspace.js";
import { fixtureHash } from "./tool-compatibility/cases.js";

test("mutation recipes bind native creation/edit/multifile patch to trusted bytes and expected diffs",()=>{
 const fixture=createWorkspaceMutations(state.root);
 expect(fixture.mutations.map((q:any)=>q.name)).toEqual(["write","edit","patch"]);
 for(const q of fixture.mutations){
  expect(q.spec.id).toBe("filesystem."+q.name);
  expect(q.spec.expectedCalls[0]!.inputHash).toBe(fixtureHash(q.input));
  expect(q.spec.expectedCalls[0]!.observationHash).toBe(fixtureHash(q.expected));
  expect(()=>q.inspect()).toThrow("HOST_STATE");
  for(const [name,bytes] of Object.entries(q.after))writeFileSync(join(q.directory,name),bytes as string);
  expect(q.inspect()).toBe(q.spec.expectedObservationHash);
  expect(q.diff()).toEqual(q.expectedDiff);
  writeFileSync(join(q.directory,"unexpected.txt"),"extra");
  expect(()=>q.inspect()).toThrow("HOST_STATE");
 }
 expect(fixture.mutations[2]!.expectedDiff).toHaveLength(2);
 expect(fixture.mutations[2]!.model).toBe("fixture-gpt-mutation");
});

test("mutation fixtures reject nonabsolute roots and changes outside the requested target",()=>{
 expect(()=>createWorkspaceMutations("relative")).toThrow("HOST_FIXTURE");
 const f=createWorkspaceMutations(state.root),q=f.mutations[0]!;
 for(const [name,bytes] of Object.entries(q.after))writeFileSync(join(q.directory,name),bytes as string);
 writeFileSync(join(q.directory,"sentinel.txt"),"unintended change");
 expect(()=>q.inspect()).toThrow("HOST_STATE");
});

test("malformed edit fixture expects native schema rejection and unchanged exact bytes",()=>{
 const q=createWorkspaceMutations(state.root).invalid;
 expect(q.input).not.toHaveProperty("newString");
 expect(q.spec.positiveRequired).toBe(false);
 expect(q.spec.expectedOutcome).toBe("error");
 expect(q.spec.executorExpectation).toBe("must_not_start");
 expect(q.inspect()).toBe(q.spec.expectedObservationHash);
 expect(JSON.parse(q.expected).error).toEqual({type:"tool.execution",message:q.errorMessage});
 expect(q.errorMessage).toContain('- newString: Missing key');
 expect(q.diff()).toEqual([]);
 writeFileSync(join(q.directory,"untouched.txt"),"unintended mutation");
 expect(()=>q.inspect()).toThrow("HOST_STATE");
});

test("independent repository transition permits declared file diffs and rejects sibling effects",()=>{
 const f=createWorkspaceMutations(state.root),q=f.mutations[0]!,before=inspectOwnedWorkspace(state.root);
 for(const [file,bytes] of Object.entries(q.after))writeFileSync(join(q.directory,file),bytes);
 expect(inspectMutationTransition(state.root,before,q)).toEqual(inspectOwnedWorkspace(state.root));
 writeFileSync(join(f.mutations[1]!.directory,"sentinel.txt"),"unintended sibling mutation");
 expect(()=>inspectMutationTransition(state.root,before,q)).toThrow("HOST_STATE");
});

import { parseWorkspaceMutationProbe } from "./tool-compatibility/workspace-query-report.js";
import vector from "./tool-compatibility/vectors/report-v1.json" with {type:"json"};
/** Contract sample; actual native execution lives in workspace-mutation-probe. */
function syntheticMutationProbe():any {
 const h="a".repeat(64),names=["write","edit","patch"];
 const records=names.map((name,i)=>{
  const offset=i*7,caseId="filesystem."+name;
  const events:any[]=structuredClone(vector.report.events).map((e,j)=>({...e,caseId,...("toolId" in e?{toolId:name,originHash:h}:{}),sequence:e.sequence+offset+(j>=4?1:0)}));
  events.splice(4,0,{...events[0],sequence:offset+5,kind:"state_observed",observationHash:h});
  const p=vector.report.proofs[0]!;
  const proof={...p,id:"proof-"+i,caseId,toolId:name,originHash:h,definitionEvent:offset+1,modelCallEvent:offset+2,executorStartEvent:offset+3,executorEndEvent:offset+4,nextRequestEvent:offset+6,finalResponseEvent:offset+7};
  return {caseId,expectedInputHash:h,expectedResultHash:h,actualResultHash:h,expectedStateHash:h,actualStateHash:h,requests:2,events,proof};
 });
 const negative:any=structuredClone(records[1]!);negative.caseId="filesystem.invalid-input";
 negative.events=negative.events.filter((e:any)=>e.kind!=="executor_started").map((e:any,i:number)=>({...e,caseId:negative.caseId,sequence:22+i,kind:e.kind==="executor_completed"?"error":e.kind}));
 negative.proof={status:"INCOMPLETE",code:"incomplete_evidence"};
 const all=[...records,negative];
 const profiles=all.map(c=>({caseId:c.caseId,model:c.caseId==="filesystem.patch"?"fixture-gpt-mutation":"synthetic-model",disabledToolIds:c.caseId==="filesystem.patch"?["write","edit"]:["patch"]}));
 const offered=all.flatMap(c=>{const definitions=names.filter(n=>!profiles.find(p=>p.caseId===c.caseId)!.disabledToolIds.includes(n)).map(name=>({name,schemaHash:h}));return [{caseId:c.caseId,definitions},{caseId:c.caseId,definitions}];});
 const bindings=all.map((c,i)=>{const e=c.events[0];return {caseId:c.caseId,model:profiles[i]!.model,runId:e.runId,sessionId:e.sessionId,chainId:e.chainId,toolId:e.toolId,toolSchemaHash:e.toolSchemaHash,originHash:e.originHash,call:structuredClone(c.events[1].call),inputHash:h,resultHash:h,stateHash:h};});
 return {ready:true,stage:"complete",failureCode:"none",scopeHash:h,inventory:names.map(name=>({id:name,name,schemaHash:h})),offered,permissions:records.map(c=>({caseId:c.caseId,action:"edit",effect:"allow",resourceHash:h})),records,profiles,bindings,negative,live:"unverified",allToolCompatibility:"unverified"};
}

test("mutation report accepts only complete native mutation proofs plus a separate schema error",()=>{
 expect(parseWorkspaceMutationProbe(syntheticMutationProbe()).ready).toBe(true);
 const mutations=[
  (r:any)=>{r.profiles[2].model="synthetic-model";},
  (r:any)=>{r.offered[0].definitions.push({name:"patch",schemaHash:"a".repeat(64)});},
  (r:any)=>{r.records[0].actualStateHash="b".repeat(64);},
  (r:any)=>{r.records[0].actualResultHash="b".repeat(64);},
  (r:any)=>{r.negative.events.find((e:any)=>e.kind==="error").kind="executor_completed";},
  (r:any)=>{r.negative.proof=structuredClone(r.records[1].proof);},
  (r:any)=>{r.negative.requests=1;},
  (r:any)=>{r.negative.actualStateHash="b".repeat(64);},
  (r:any)=>{r.negative.events[4].call.hostCallId="crossed";},
  (r:any)=>{r.offered.pop();},
  (r:any)=>{r.bindings.pop();},
  (r:any)=>{r.records[0].proof.hostMessageId="unrelated";for(const e of r.records[0].events)if(e.call)e.call.hostMessageId="unrelated";},
  (r:any)=>{r.records[0].proof.sessionId="unrelated";for(const e of r.records[0].events)e.sessionId="unrelated";},
  (r:any)=>{for(const e of r.negative.events)e.sessionId="unrelated";},
  (r:any)=>{r.live="PASS";},
 ];
 for(const mutate of mutations){const r=syntheticMutationProbe();mutate(r);expect(()=>parseWorkspaceMutationProbe(r)).toThrow("HOST_WORKSPACE_REPORT");}
});

for(const id of ["filesystem.write","filesystem.edit","filesystem.patch","filesystem.invalid-input"]){
 const kinds=id==="filesystem.invalid-input"?["definition","model_call","error","state_observed","next_model_request","final_response"]:["definition","model_call","executor_started","executor_completed","state_observed","next_model_request","final_response"];
 for(const kind of kinds){
  for(const field of ["runId","requestKind","scopeHash","sessionId","chainId"]){
   test(`mutation report rejects crossed ${id} ${kind} ${field}`,()=>{
    const r=syntheticMutationProbe(),c=id==="filesystem.invalid-input"?r.negative:r.records.find((c:any)=>c.caseId===id);
    c.events.find((e:any)=>e.kind===kind)[field]=field==="scopeHash"?"b".repeat(64):field==="requestKind"?"title":"unrelated";
    expect(()=>parseWorkspaceMutationProbe(r)).toThrow("HOST_WORKSPACE_REPORT");
   });
  }
 }
}
