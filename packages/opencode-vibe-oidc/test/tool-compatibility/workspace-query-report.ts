/** Family-probe envelope only; full RunReport/coverage acceptance is separate. */
import { parseRoundTripProof, parseTraceEvent } from "./contracts.js";
import { fixtureHash } from "./cases.js";
export function parseWorkspaceQueryProbe(value:unknown):Record<string,unknown>{
 const fail=():never=>{throw new Error("HOST_WORKSPACE_REPORT");};
 const object=(v:unknown,keys:string[])=>{if(!v||typeof v!=="object"||Array.isArray(v)||Object.keys(v).length!==keys.length||keys.some(k=>!Object.hasOwn(v,k)))return fail();return v as Record<string,unknown>;};
 const array=(v:unknown,max:number):unknown[]=>{if(!Array.isArray(v)||v.length>max)return fail();return v;};
 const hash=(v:unknown)=>{if(typeof v!=="string"||!/^[a-f0-9]{64}$/.test(v))fail();};
 const name=(v:unknown)=>{if(typeof v!=="string"||!/^[a-zA-Z0-9_.:/-]{1,160}$/.test(v))fail();};
 const ids=["filesystem.read","filesystem.glob","filesystem.grep"];
 try{
  const r=object(value,["ready","stage","failureCode","scopeHash","inventory","offered","permissions","records","live","allToolCompatibility"]);
  if(JSON.stringify(r).length>65536||typeof r.ready!=="boolean"||!["setup","connect","model","inventory","read","glob","grep","complete"].includes(String(r.stage))||typeof r.failureCode!=="string"||!/^(none|HOST_[A-Z_]+)$/.test(r.failureCode)||r.live!=="unverified"||r.allToolCompatibility!=="unverified")fail();
  hash(r.scopeHash);
  const inventory=array(r.inventory,256);for(const entry of inventory){const e=object(entry,["id","name","schemaHash"]);name(e.id);name(e.name);hash(e.schemaHash);}
  const offered=array(r.offered,8);for(const entry of offered){const e=object(entry,["caseId","definitions"]);if(!ids.includes(String(e.caseId)))fail();const defs=array(e.definitions,256);for(const d of defs){const t=object(d,["name","schemaHash"]);name(t.name);hash(t.schemaHash);}if(new Set(defs.map(d=>(d as {name:string}).name)).size!==defs.length)fail();}
  const permissions=array(r.permissions,8);for(const entry of permissions){const p=object(entry,["caseId","action","effect","resourceHash"]);if(!ids.includes(String(p.caseId))||p.action!==String(p.caseId).split(".")[1]||p.effect!=="allow")fail();hash(p.resourceHash);}
  const records=array(r.records,3);
  let observedRunId:string|undefined;
  for(const entry of records){
   const c=object(entry,["caseId","expectedInputHash","expectedResultHash","actualResultHash","expectedStateHash","actualStateHash","requests","events","proof"]);
   if(!ids.includes(String(c.caseId))||!Number.isSafeInteger(c.requests)||Number(c.requests)<0||Number(c.requests)>16)fail();
   for(const key of ["expectedInputHash","expectedResultHash","expectedStateHash"])hash(c[key]);for(const key of ["actualResultHash","actualStateHash"])if(c[key]!==null)hash(c[key]);
   const events=array(c.events,8).map(parseTraceEvent);
   if(events.length){
    observedRunId ??= events[0]!.runId;
    if(events.some(e=>e.runId!==observedRunId||e.requestKind!=="primary"))fail();
   }
   if(!r.ready){const p=c.proof as Record<string,unknown>;if(p&&p.status==="INCOMPLETE"){const incomplete=object(p,["status","code"]);if(!["incomplete_evidence","redaction_failed"].includes(String(incomplete.code)))fail();}else parseRoundTripProof(p);continue;}
   const proof=parseRoundTripProof(c.proof),toolId=String(c.caseId).split(".")[1]!;
   if(c.requests!==2||c.expectedResultHash!==c.actualResultHash||c.expectedStateHash!==c.actualStateHash||proof.caseId!==c.caseId||proof.toolId!==toolId||proof.scopeHash!==r.scopeHash||proof.primaryRequestId===proof.nextRequestId||events.length!==7)fail();
   if(!inventory.some(t=>(t as {id:string;name:string}).id===toolId&&(t as {name:string}).name===toolId)||offered.filter(o=>(o as {caseId:string}).caseId===c.caseId).some(o=>!(o as {definitions:{name:string;schemaHash:string}[]}).definitions.some(t=>t.name===toolId&&t.schemaHash===proof.toolSchemaHash)))fail();
   const refs=[proof.definitionEvent,proof.modelCallEvent,proof.executorStartEvent,proof.executorEndEvent,proof.nextRequestEvent,proof.finalResponseEvent],kinds=["definition","model_call","executor_started","executor_completed","next_model_request","final_response"];
   if(refs.some((n,i)=>i>0&&n<=refs[i-1]!))fail();
   for(let i=0;i<refs.length;i++){
    const e=events.find(e=>e.sequence===refs[i]);if(!e)throw new Error("HOST_WORKSPACE_REPORT");if(e.kind!==kinds[i]||e.caseId!==proof.caseId||e.scopeHash!==proof.scopeHash||e.sessionId!==proof.sessionId||e.chainId!==proof.chainId||e.requestKind!=="primary"||e.requestId!==(i<4?proof.primaryRequestId:proof.nextRequestId))fail();
    if("toolId" in e&&(e.toolId!==proof.toolId||e.toolSchemaHash!==proof.toolSchemaHash||e.originHash!==proof.originHash))fail();
    if("call" in e&&e.call&&(e.call.inputHash!==c.expectedInputHash||e.call.hostMessageId!==proof.hostMessageId||e.call.hostCallId!==proof.hostCallId||e.call.upstreamCallId!==proof.upstreamCallId||fixtureHash(e.call)!==fixtureHash((events.find(e=>e.kind==="model_call") as {call:unknown}).call)))fail();
    if((e.kind==="executor_completed"||e.kind==="next_model_request")&&e.observationHash!==c.actualResultHash)fail();
    if(e.kind==="model_call"&&e.wireCallId!==proof.upstreamCallId||e.kind==="next_model_request"&&e.wireCallId!==proof.continuationWireCallId)fail();
   }
   const state=events.filter(e=>e.kind==="state_observed");if(state.length!==1||state[0]!.observationHash!==c.actualStateHash||state[0]!.caseId!==proof.caseId||state[0]!.scopeHash!==proof.scopeHash||state[0]!.sessionId!==proof.sessionId||state[0]!.chainId!==proof.chainId||!("toolId" in state[0]!)||state[0]!.toolId!==toolId||state[0]!.toolSchemaHash!==proof.toolSchemaHash||state[0]!.originHash!==proof.originHash||state[0]!.requestId!==proof.primaryRequestId||state[0]!.sequence<=proof.executorEndEvent||state[0]!.sequence>=proof.nextRequestEvent)fail();
   if(offered.filter(o=>(o as {caseId:string}).caseId===c.caseId).length!==2||permissions.filter(p=>(p as {caseId:string}).caseId===c.caseId).length!==1)fail();
  }
  if(r.ready&&(r.stage!=="complete"||r.failureCode!=="none"||records.length!==3||new Set(records.map(c=>(c as {caseId:string}).caseId)).size!==3))fail();
  return r;
 }catch{return fail();}
}
