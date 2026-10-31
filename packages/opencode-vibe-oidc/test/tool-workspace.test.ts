import { test, expect } from "bun:test";
import { join } from "node:path";
import { writeFileSync, unlinkSync } from "node:fs";
import { state } from "./preload.js";
import { createWorkspaceQueries } from "./tool-compatibility/fixtures/workspace.js";
import { fixtureHash } from "./tool-compatibility/cases.js";

test("workspace query fixtures bind authoritative read/glob/grep expectations to unpredictable owned material",()=>{
  const fixture=createWorkspaceQueries(state.root), other=createWorkspaceQueries(state.root);
  expect(fixture.queries.map(q=>q.name)).toEqual(["read","glob","grep"]);
  expect(fixture.directory).not.toBe(other.directory);
  for(const q of fixture.queries){
    expect(q.spec.id).toBe("filesystem."+q.name);
    expect(q.spec.expectedCalls[0]?.inputHash).toBe(fixtureHash(q.input));
    expect(q.spec.expectedCalls[0]?.observationHash).toBe(fixtureHash(q.expected));
    expect(q.spec.expectedObservationHash).toBe(fixture.inspect());
    expect(q.spec.requiredObservations).toContain("state_observed");
    expect(Object.isFrozen(q.input)).toBe(true);
    expect(q.expected).not.toContain("excluded.skip.txt");
    expect(q.expected).not.toContain("unrelated.txt");
  }
  expect(fixture.queries[0]!.expected).toContain("1: Owned café 雪\n2: needle_");
  expect(fixture.queries[1]!.input.pattern).toBe("**/*.match.txt");
  expect(fixture.queries[2]!.expected).toContain("Found 1 matches\n");
  expect(fixture.queries[2]!.expected).toContain("  Line 2: needle_");
  expect(fixture.queries[2]!.expected.endsWith("\n")).toBe(true);
});
test("independent fixture state rejects changed, extra or missing bytes",()=>{
  const fixture=createWorkspaceQueries(state.root);
  const hash=fixture.inspect();expect(hash).toMatch(/^[a-f0-9]{64}$/);
  writeFileSync(join(fixture.directory,"owned.match.txt"),"forged success");
  expect(()=>fixture.inspect()).toThrow("HOST_STATE");
  const other=createWorkspaceQueries(state.root);
  writeFileSync(join(other.directory,"unexpected.txt"),"extra");
  expect(()=>other.inspect()).toThrow("HOST_STATE");
  const missing=createWorkspaceQueries(state.root);
  unlinkSync(join(missing.directory,"excluded.skip.txt"));
  expect(()=>missing.inspect()).toThrow("HOST_STATE");
});
test("fixture creation rejects nonabsolute roots",()=>{
  expect(()=>createWorkspaceQueries("relative")).toThrow("HOST_FIXTURE");
});
import { parseWorkspaceQueryProbe } from "./tool-compatibility/workspace-query-report.js";
import vector from "./tool-compatibility/vectors/report-v1.json" with {type:"json"};
/** Contract sample only; real execution is tested by the deliberate process probe. */
function syntheticProbe(){
 const hash="a".repeat(64),names=["read","glob","grep"];
 const records=names.map((name,i)=>{
  const offset=i*7,caseId="filesystem."+name;
  const events:Record<string,any>[]=structuredClone(vector.report.events).map((e,j)=>({...e,caseId,...("toolId" in e?{toolId:name}:{}),sequence:e.sequence+offset+(j>=4?1:0)}));
  events.splice(4,0,{...events[0],sequence:offset+5,kind:"state_observed",observationHash:hash});
  const p=vector.report.proofs[0]!;
  const proof={...p,id:"proof-"+i,caseId,toolId:name,definitionEvent:offset+1,modelCallEvent:offset+2,executorStartEvent:offset+3,executorEndEvent:offset+4,nextRequestEvent:offset+6,finalResponseEvent:offset+7};
  return {caseId,expectedInputHash:hash,expectedResultHash:hash,actualResultHash:hash,expectedStateHash:hash,actualStateHash:hash,requests:2,events,proof};
 });
 const definitions=names.map(name=>({name,schemaHash:hash}));
 return {ready:true,stage:"complete",failureCode:"none",scopeHash:hash,inventory:names.map(name=>({id:name,name,schemaHash:hash})),offered:records.flatMap(c=>[{caseId:c.caseId,definitions},{caseId:c.caseId,definitions}]),permissions:records.map((c,i)=>({caseId:c.caseId,action:names[i],effect:"allow",resourceHash:hash})),records,live:"unverified",allToolCompatibility:"unverified"};
}
test("family probe envelope requires real proof stages, exact result/state hashes, and correlated continuation",()=>{
 expect(parseWorkspaceQueryProbe(syntheticProbe()).ready).toBe(true);
 const mutations=[
  (r:ReturnType<typeof syntheticProbe>)=>{r.records[0]!.expectedInputHash="b".repeat(64);},
  (r:ReturnType<typeof syntheticProbe>)=>{r.offered[0]!.definitions[0]!.schemaHash="b".repeat(64);},
  (r:ReturnType<typeof syntheticProbe>)=>{r.records[0]!.actualResultHash="b".repeat(64);},
  (r:ReturnType<typeof syntheticProbe>)=>{r.records[0]!.actualStateHash="b".repeat(64);},
  (r:ReturnType<typeof syntheticProbe>)=>{r.records[0]!.events.splice(2,1);},
  (r:ReturnType<typeof syntheticProbe>)=>{r.records[0]!.events[5]!.call.hostCallId="crossed-call";},
  (r:ReturnType<typeof syntheticProbe>)=>{r.records[0]!.events[5]!.wireCallId="crossed-wire";},
  (r:ReturnType<typeof syntheticProbe>)=>{r.records[0]!.requests=1;},
  (r:ReturnType<typeof syntheticProbe>)=>{r.offered.pop();},
  (r:ReturnType<typeof syntheticProbe>)=>{r.records.pop();},
  (r:ReturnType<typeof syntheticProbe>)=>{Object.assign(r,{raw:"private"});},
 ];
 for(const mutate of mutations){const r=syntheticProbe();mutate(r);expect(()=>parseWorkspaceQueryProbe(r)).toThrow("HOST_WORKSPACE_REPORT");}
});

for(const name of ["read","glob","grep"]){
 for(const kind of ["definition","model_call","executor_started","executor_completed","state_observed","next_model_request","final_response"]){
  test(`query report rejects ${name} ${kind} from a different run`,()=>{
   const report=syntheticProbe(), record=report.records.find(r=>r.caseId==="filesystem."+name)!;
   record.events.find(e=>e.kind===kind)!.runId="different-run";
   expect(()=>parseWorkspaceQueryProbe(report)).toThrow("HOST_WORKSPACE_REPORT");
  });
  test(`query report rejects auxiliary ${name} ${kind}`,()=>{
   const report=syntheticProbe(), record=report.records.find(r=>r.caseId==="filesystem."+name)!;
   record.events.find(e=>e.kind===kind)!.requestKind="title";
   expect(()=>parseWorkspaceQueryProbe(report)).toThrow("HOST_WORKSPACE_REPORT");
  });
 }
 test(`query report rejects a consistently crossed ${name} record`,()=>{
  const report=syntheticProbe(),record=report.records.find(r=>r.caseId==="filesystem."+name)!;
  for(const event of record.events)event.runId="different-run";
  expect(()=>parseWorkspaceQueryProbe(report)).toThrow("HOST_WORKSPACE_REPORT");
 });
}
