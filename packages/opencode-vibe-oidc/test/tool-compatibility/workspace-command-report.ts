/** Native command family envelope. No full inventory or live compatibility claim. */
import { fixtureHash } from "./cases.js";
import { parseRoundTripProof, parseTraceEvent } from "./contracts.js";
export function parseWorkspaceCommandProbe(value:unknown):Record<string,unknown>{
 const fail=():never=>{throw new Error("HOST_WORKSPACE_REPORT");};
 const obj=(v:unknown,keys:string[])=>{if(!v||typeof v!=="object"||Array.isArray(v)||Object.keys(v).length!==keys.length||keys.some(k=>!Object.hasOwn(v,k)))return fail();return v as Record<string,unknown>;};
 const arr=(v:unknown,max:number):Record<string,unknown>[]=>{if(!Array.isArray(v)||v.length>max)return fail();return v;};
 const hash=(v:unknown)=>{if(typeof v!=="string"||!/^[a-f0-9]{64}$/.test(v))fail();};
 const name=(v:unknown)=>{if(typeof v!=="string"||!/^[A-Za-z0-9_.:/-]{1,160}$/.test(v))fail();};
 const ids=["shell.stdout-stderr-exit","shell.execution-error","shell.denied","shell.invalid-input"];
 try{
  const r=obj(value,["ready","stage","failureCode","scopeHash","inventory","offered","permissions","records","commandBindings","live","allToolCompatibility"]);
  if(JSON.stringify(r).length>65536||typeof r.ready!=="boolean"||typeof r.stage!=="string"||!["setup","connect","model","inventory","shell","complete"].includes(r.stage)||typeof r.failureCode!=="string"||!/^(none|HOST_[A-Z_]+)$/.test(r.failureCode)||r.live!=="unverified"||r.allToolCompatibility!=="unverified")fail();hash(r.scopeHash);
  const inventory=arr(r.inventory,256);for(const t of inventory){obj(t,["id","name","schemaHash"]);name(t.id);name(t.name);hash(t.schemaHash);}
  const offered=arr(r.offered,8);for(const o of offered){obj(o,["caseId","definitions"]);if(!ids.includes(String(o.caseId)))fail();const defs=arr(o.definitions,256);for(const d of defs){obj(d,["name","schemaHash"]);name(d.name);hash(d.schemaHash);}if(new Set(defs.map(d=>d.name)).size!==defs.length)fail();}
  const permissions=arr(r.permissions,2);for(const p of permissions){obj(p,["caseId","action","effect","resourceHash"]);if(!ids.slice(0,2).includes(String(p.caseId))||p.action!=="shell"||p.effect!=="allow")fail();hash(p.resourceHash);}
  const records=arr(r.records,4),bindings=arr(r.commandBindings,4);let run:string|undefined;
  if(r.ready&&(r.stage!=="complete"||r.failureCode!=="none"||records.length!==4||bindings.length!==4||permissions.length!==2||offered.length!==8))fail();
  if(new Set(records.map(c=>c.caseId)).size!==records.length||new Set(bindings.map(b=>b.caseId)).size!==bindings.length)fail();
  for(const c of records){
   obj(c,["caseId","expectedInputHash","expectedResultHash","actualResultHash","expectedStateHash","actualStateHash","requests","events","proof"]);if(!ids.includes(String(c.caseId)))fail();
   for(const k of ["expectedInputHash","expectedResultHash","expectedStateHash"])hash(c[k]);for(const k of ["actualResultHash","actualStateHash"])if(c[k]!==null)hash(c[k]);
   const events=arr(c.events,9).map(parseTraceEvent);if(!r.ready)continue;
   const b=bindings.find(b=>b.caseId===c.caseId);if(!b)fail();
   obj(b,["caseId","model","runId","sessionId","chainId","toolId","toolSchemaHash","originHash","call","inputHash","resultHash","stateHash","nativeHash","expectedNativeHash","idle"]);
   for(const k of ["caseId","model","runId","sessionId","chainId","toolId"])name(b![k]);for(const k of ["toolSchemaHash","originHash","inputHash","resultHash","stateHash"])hash(b![k]);
   const index=ids.indexOf(String(c.caseId)),completed=index<2;
   if(b!.model!=="synthetic-model"||b!.toolId!=="shell"||b!.idle!==true||c.requests!==2||c.actualResultHash!==c.expectedResultHash||c.actualStateHash!==c.expectedStateHash||b!.inputHash!==c.expectedInputHash||b!.resultHash!==c.expectedResultHash||b!.stateHash!==c.expectedStateHash)fail();
   if(completed){hash(b!.nativeHash);hash(b!.expectedNativeHash);if(b!.nativeHash!==b!.expectedNativeHash)fail();}else if(b!.nativeHash!==null||b!.expectedNativeHash!==null)fail();
   const kinds=index===0?["definition","model_call","executor_started","executor_completed","state_observed","next_model_request","final_response"]:index===1?["definition","model_call","executor_started","executor_completed","error","state_observed","next_model_request","final_response"]:["definition","model_call",index===2?"denied":"error","state_observed","next_model_request","final_response"];
   if(events.length!==kinds.length)fail();run??=String(b!.runId);if(b!.runId!==run)fail();
   const next=events.at(-2)!;
   for(let i=0;i<events.length;i++){
    const e=events[i]!;if(e.kind!==kinds[i]||e.runId!==b!.runId||e.caseId!==c.caseId||e.scopeHash!==r.scopeHash||e.sessionId!==b!.sessionId||e.chainId!==b!.chainId||e.requestKind!=="primary"||e.requestId!==(i<events.length-2?events[0]!.requestId:next.requestId)||(i>0&&e.sequence<=events[i-1]!.sequence))fail();
    if("toolId" in e&&(e.toolId!==b!.toolId||e.toolSchemaHash!==b!.toolSchemaHash||e.originHash!==b!.originHash))fail();
    if("call" in e&&fixtureHash(e.call)!==fixtureHash(b!.call))fail();
    if("call" in e&&e.call?.inputHash!==b!.inputHash)fail();
    if("wireCallId" in e&&e.wireCallId!==(b!.call as {upstreamCallId:string}).upstreamCallId)fail();
    if(["executor_completed","denied","error","next_model_request"].includes(e.kind)&&e.observationHash!==b!.resultHash)fail();
    if(e.kind==="state_observed"&&e.observationHash!==b!.stateHash)fail();
   }
   if(next.requestId===events[0]!.requestId)fail();
   const offers=offered.filter(o=>o.caseId===c.caseId);if(offers.length!==2||offers.some(o=>!(o.definitions as {name:string;schemaHash:string}[]).some(d=>d.name==="shell"&&d.schemaHash===b!.toolSchemaHash))||!inventory.some(t=>t.id==="shell"&&t.name==="shell")||permissions.filter(p=>p.caseId===c.caseId).length!==(completed?1:0))fail();
   if(index===0){const p=parseRoundTripProof(c.proof),call=b!.call as {hostMessageId:string;hostCallId:string;upstreamCallId:string};if(p.caseId!==c.caseId||p.scopeHash!==r.scopeHash||p.sessionId!==b!.sessionId||p.chainId!==b!.chainId||p.toolId!=="shell"||p.toolSchemaHash!==b!.toolSchemaHash||p.originHash!==b!.originHash||p.hostMessageId!==call.hostMessageId||p.hostCallId!==call.hostCallId||p.upstreamCallId!==call.upstreamCallId||p.continuationWireCallId!==call.upstreamCallId||p.primaryRequestId!==events[0]!.requestId||p.nextRequestId!==next.requestId||JSON.stringify([p.definitionEvent,p.modelCallEvent,p.executorStartEvent,p.executorEndEvent,p.nextRequestEvent,p.finalResponseEvent])!==JSON.stringify(events.filter(e=>e.kind!=="state_observed").map(e=>e.sequence)))fail();}
   else{const p=obj(c.proof,["status","code"]);if(p.status!=="INCOMPLETE"||p.code!=="incomplete_evidence")fail();}
  }
  return r;
 }catch{return fail();}
}
