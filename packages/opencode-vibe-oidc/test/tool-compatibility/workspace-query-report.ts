/** Family-probe envelope only; full RunReport/coverage acceptance is separate. */
import type {TraceEvent} from "./contracts.js";
import { parseRoundTripProof, parseTraceEvent } from "./contracts.js";
import { fixtureHash } from "./cases.js";
export function parseWorkspaceQueryProbe(value:unknown):Record<string,unknown>{return parseWorkspaceProbe(value,false);}
export function parseWorkspaceMutationProbe(value:unknown):Record<string,unknown>{return parseWorkspaceProbe(value,true);}
function parseWorkspaceProbe(value:unknown,mutation:boolean):Record<string,unknown>{
 const fail=():never=>{throw new Error("HOST_WORKSPACE_REPORT");};
 const object=(v:unknown,keys:string[])=>{if(!v||typeof v!=="object"||Array.isArray(v)||Object.keys(v).length!==keys.length||keys.some(k=>!Object.hasOwn(v,k)))return fail();return v as Record<string,unknown>;};
 const array=(v:unknown,max:number):unknown[]=>{if(!Array.isArray(v)||v.length>max)return fail();return v;};
 const hash=(v:unknown)=>{if(typeof v!=="string"||!/^[a-f0-9]{64}$/.test(v))fail();};
 const name=(v:unknown)=>{if(typeof v!=="string"||!/^[a-zA-Z0-9_.:/-]{1,160}$/.test(v))fail();};
 const ids=mutation?["filesystem.write","filesystem.edit","filesystem.patch"]:["filesystem.read","filesystem.glob","filesystem.grep"];
 try{
  const r=object(value,["ready","stage","failureCode","scopeHash","inventory","offered","permissions","records","live","allToolCompatibility",...(mutation?["profiles","bindings","negative"]:[])]);
  if(JSON.stringify(r).length>65536||typeof r.ready!=="boolean"||!["setup","connect","model","inventory","read","glob","grep","write","edit","patch","complete"].includes(String(r.stage))||typeof r.failureCode!=="string"||!/^(none|HOST_[A-Z_]+)$/.test(r.failureCode)||r.live!=="unverified"||r.allToolCompatibility!=="unverified")fail();
  hash(r.scopeHash);
  const inventory=array(r.inventory,256);for(const entry of inventory){const e=object(entry,["id","name","schemaHash"]);name(e.id);name(e.name);hash(e.schemaHash);}
  const offered=array(r.offered,8);for(const entry of offered){const e=object(entry,["caseId","definitions"]);if(![...ids,...(mutation?["filesystem.invalid-input"]:[])].includes(String(e.caseId)))fail();const defs=array(e.definitions,256);for(const d of defs){const t=object(d,["name","schemaHash"]);name(t.name);hash(t.schemaHash);}if(new Set(defs.map(d=>(d as {name:string}).name)).size!==defs.length)fail();}
  const permissions=array(r.permissions,8);for(const entry of permissions){const p=object(entry,["caseId","action","effect","resourceHash"]);if(!ids.includes(String(p.caseId))||p.action!==(mutation?"edit":String(p.caseId).split(".")[1])||p.effect!=="allow")fail();hash(p.resourceHash);}
  if(mutation){
   const profiles=array(r.profiles,4);
   if(profiles.length!==4)fail();
   for(let i=0;i<4;i++){
    const p=object(profiles[i],["caseId","model","disabledToolIds"]),patch=i===2;
    if(p.caseId!==(i===3?"filesystem.invalid-input":ids[i])||p.model!==(patch?"fixture-gpt-mutation":"synthetic-model")||JSON.stringify(p.disabledToolIds)!==JSON.stringify(patch?["write","edit"]:["patch"]))fail();
    for(const offering of offered.filter(o=>(o as {caseId:string}).caseId===p.caseId))if((offering as {definitions:{name:string}[]}).definitions.some(d=>(p.disabledToolIds as string[]).includes(d.name)))fail();
   }
  }
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
  if(mutation&&r.ready){
   const n=object(r.negative,["caseId","expectedInputHash","expectedResultHash","actualResultHash","expectedStateHash","actualStateHash","requests","events","proof"]);
   if(n.caseId!=="filesystem.invalid-input"||n.requests!==2||n.actualResultHash!==n.expectedResultHash||n.actualStateHash!==n.expectedStateHash)fail();
   for(const k of ["expectedInputHash","expectedResultHash","expectedStateHash"])hash(n[k]);
   const p=object(n.proof,["status","code"]);if(p.status!=="INCOMPLETE"||p.code!=="incomplete_evidence")fail();
   const events=array(n.events,6).map(parseTraceEvent),kinds=["definition","model_call","error","state_observed","next_model_request","final_response"];
   if(events.length!==6)fail();
   const first=events[0]! as Extract<TraceEvent,{kind:"definition"|"unsupported"|"state_observed"}>,call=events[1]! as Extract<TraceEvent,{kind:"model_call"|"next_model_request"}>,next=events[4]!;
   if(!("call" in call)||!call.call||!("call" in next)||!next.call||next.requestId===first.requestId||!("toolSchemaHash" in first))fail();
   for(let i=0;i<6;i++){
    const e=events[i]!;
    if(e.sequence<1||(i>0&&e.sequence<=events[i-1]!.sequence)||e.kind!==kinds[i]||e.runId!==observedRunId||e.caseId!==n.caseId||e.scopeHash!==r.scopeHash||e.requestKind!=="primary"||e.sessionId!==first.sessionId||e.chainId!==first.chainId||e.requestId!==(i<4?first.requestId:next.requestId))fail();
    if(i<5&&(!("toolId" in e)||e.toolId!=="edit"||e.toolSchemaHash!==first.toolSchemaHash||e.originHash!==first.originHash))fail();
    if("call" in e&&e.call&&(e.call.inputHash!==n.expectedInputHash||fixtureHash(e.call)!==fixtureHash(call.call)))fail();
    if((e.kind==="model_call"||e.kind==="next_model_request")&&e.wireCallId!==call.call.upstreamCallId)fail();
    if((i===2||i===4)&&e.observationHash!==n.expectedResultHash||i===3&&e.observationHash!==n.expectedStateHash)fail();
   }
   const offerings=offered.filter(o=>(o as {caseId:string}).caseId===n.caseId);
   if(offerings.length!==2||offerings.some(o=>!(o as {definitions:{name:string;schemaHash:string}[]}).definitions.some(t=>t.name==="edit"&&t.schemaHash===first.toolSchemaHash))||permissions.some(p=>(p as {caseId:string}).caseId===n.caseId))fail();
  }
  if(mutation&&r.ready){
   const bindings=array(r.bindings,4);
   if(bindings.length!==4||new Set(bindings.map(b=>(b as {caseId:string}).caseId)).size!==4)fail();
   for(const entry of bindings){
    const b=object(entry,["caseId","model","runId","sessionId","chainId","toolId","toolSchemaHash","originHash","call","inputHash","resultHash","stateHash"]);
    for(const k of ["caseId","model","runId","sessionId","chainId","toolId"])name(b[k]);
    for(const k of ["toolSchemaHash","originHash","inputHash","resultHash","stateHash"])hash(b[k]);
    const record=[...records,r.negative].find(c=>(c as {caseId:string}).caseId===b.caseId) as Record<string,unknown>|undefined;
    const profile=(r.profiles as Record<string,unknown>[]).find(p=>p.caseId===b.caseId);
    if(!record||!profile||profile.model!==b.model||b.toolId!==(b.caseId==="filesystem.invalid-input"?"edit":String(b.caseId).split(".")[1])||record.expectedInputHash!==b.inputHash||record.expectedResultHash!==b.resultHash||record.expectedStateHash!==b.stateHash)fail();
    for(const e of (record!.events as ReturnType<typeof parseTraceEvent>[])){
     if(e.runId!==b.runId||e.sessionId!==b.sessionId||e.chainId!==b.chainId)fail();
     if("toolId" in e&&(e.toolId!==b.toolId||e.toolSchemaHash!==b.toolSchemaHash||e.originHash!==b.originHash))fail();
     if("call" in e&&fixtureHash(e.call)!==fixtureHash(b.call))fail();
    }
   }
  }
  if(r.ready&&(r.stage!=="complete"||r.failureCode!=="none"||records.length!==3||new Set(records.map(c=>(c as {caseId:string}).caseId)).size!==3))fail();
  return r;
 }catch{return fail();}
}
