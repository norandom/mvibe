/** Explicit native shell execution; never part of the guarded pure suite. */
import { smokeOfflineHost } from "./runner.js";
import { parseWorkspaceCommandProbe } from "./workspace-command-report.js";
/** Adversarial checks use this run's actual artifact, never a fabricated pass. */
export function rejectCrossedCommandEvidence(workspace:unknown):number {
 const changes=[
  (r:any)=>{r.records[0].actualResultHash="b".repeat(64);},
  (r:any)=>{r.records[0].actualStateHash="b".repeat(64);},
  (r:any)=>{r.commandBindings[0].nativeHash="b".repeat(64);},
  (r:any)=>{r.commandBindings[0].model="unrelated";},
  (r:any)=>{r.commandBindings[0].toolSchemaHash="b".repeat(64);},
  (r:any)=>{r.commandBindings[0].call.hostMessageId="unrelated";},
  (r:any)=>{r.records[1].proof=structuredClone(r.records[0].proof);},
  (r:any)=>{r.records[2].proof=structuredClone(r.records[0].proof);},
  (r:any)=>{r.records[3].actualStateHash="b".repeat(64);},
  (r:any)=>{r.records[1].requests=3;},
  (r:any)=>{r.permissions.pop();},
  (r:any)=>{r.offered.pop();},
 ];
 for(const record of (workspace as any).records)for(const event of record.events)for(const field of ["runId","requestKind","scopeHash","sessionId","chainId"]){
  changes.push((r:any)=>{r.records.find((c:any)=>c.caseId===record.caseId).events.find((e:any)=>e.sequence===event.sequence)[field]=field==="scopeHash"?"b".repeat(64):field==="requestKind"?"title":"unrelated";});
 }
 for(const change of changes){const crossed=structuredClone(workspace);change(crossed);let rejected=false;try{parseWorkspaceCommandProbe(crossed);}catch{rejected=true;}if(!rejected)throw new Error("HOST_CROSSED_EVIDENCE");}
 return changes.length;
}
if(import.meta.main){
try {
 const host=process.env.OPENCODE_TEST_BINARY,python=process.env.VIBE_TEST_PYTHON;
 if(!host||!python)throw new Error("HOST_RUNTIME");
 const result=await smokeOfflineHost({host,python,bun:process.execPath,startupMs:30_000,executionMs:60_000,cleanupMs:8_000,workspaceCommandProbe:true});
 const rejectedArtifactMutations=result.ready?rejectCrossedCommandEvidence(result.workspace):0;
 console.log(JSON.stringify({...result,rejectedArtifactMutations,cleanup:"verified"}));if(!result.ready)process.exitCode=1;
}catch(e){console.error(e instanceof Error&&/^HOST_[A-Z_]+$/.test(e.message)?e.message:"HOST_WORKSPACE");process.exitCode=1;}
}
