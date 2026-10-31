/** Passive owned-fixture evidence. Never dispatches tools or imports production code. */
import { fixtureHash, validateCaseSpec } from "./cases.js";
import { parseTraceEvent, parseRoundTripProof, parseRunSpec } from "./contracts.js";
import type { CaseSpec, ReasonCode, RoundTripProof, RunSpec, TraceEvent } from "./contracts.js";
type Incomplete=Readonly<{status:"INCOMPLETE";code:ReasonCode}>;
type Options={runId:string;cases:readonly CaseSpec[];limits:RunSpec["limits"];cancelOwnedTurn:()=>void};
const quiet=(fn:()=>void)=>{try{fn();}catch{/* Never expose callback exceptions. */}};

/** Inputs are explicit allowlisted scalar projections, never raw host/SDK objects.
 * execute.before means dispatch only; permission ordering needs capability preflight.
 */
export function createRoundTripRecorder(options:Options) {
  const {runId,cancelOwnedTurn}=options;
  const limits=parseRunSpec({mode:"offline",hostExecutable:"owned-host",bunExecutable:"owned-bun",preparedPython:"owned-python",caseManifest:"owned-manifest",ownedProfile:"owned-profile",model:"fixture",reportDirectory:"owned-report",loginPolicy:"reuse-only",limits:options.limits}).limits;
  const cases=options.cases.map(c=>validateCaseSpec(c,limits));
  if(!cases.length||new Set(cases.map(c=>c.id)).size!==cases.length)throw new Error("invalid_report");
  const stored:TraceEvent[]=[];let bytes=0,auxiliary=0,failure:ReasonCode|undefined;
  const incomplete=(code:ReasonCode="incomplete_evidence"):Incomplete=>{if(!failure){failure=code;quiet(cancelOwnedTurn);}return Object.freeze({status:"INCOMPLETE",code:failure});};
  const fail=(code:ReasonCode):never=>{incomplete(code);throw new Error(failure);};
  function record(value:unknown):TraceEvent|undefined {
    if(failure)throw new Error(failure);
    let e:TraceEvent;try{e=parseTraceEvent(value);}catch{return fail("redaction_failed");}
    const spec=cases.find(c=>c.id===e.caseId);if(e.runId!==runId||!spec)return fail("incomplete_evidence");
    if(e.requestKind!=="primary"){if(++auxiliary>limits.eventsPerCase)return fail("incomplete_evidence");return;}
    const size=new TextEncoder().encode(JSON.stringify(e)).length, scoped=stored.filter(p=>p.caseId===e.caseId);
    if(bytes+size>limits.traceBytes||scoped.length>=limits.eventsPerCase||stored.length>=32768||
      (stored.length&&e.sequence<=stored[stored.length-1]!.sequence))return fail("incomplete_evidence");
    if("toolId" in e&&!spec.toolIds.includes(e.toolId))return fail("incomplete_evidence");
    if(e.kind==="model_call"&&scoped.filter(p=>p.kind==="model_call").length>=limits.toolCallsPerCase)return fail("incomplete_evidence");
    bytes+=size;stored.push(e);return e;
  }
  function proofs(caseId:string):readonly RoundTripProof[]|Incomplete {
    if(failure)return Object.freeze({status:"INCOMPLETE",code:failure});
    const spec=cases.find(c=>c.id===caseId),events=stored.filter(e=>e.caseId===caseId);
    if(!spec?.positiveRequired||!events.length||events.some(e=>["denied","error","unsupported","host_cancelled","provider_aborted","fixture_cancelled"].includes(e.kind)))return incomplete();
    const calls=events.filter(e=>e.kind==="model_call");if(calls.length!==spec.expectedCalls.length)return incomplete();
    const result:RoundTripProof[]=[],used=new Set<number>(),hostIds=new Set<string>(),upstreamIds=new Set<string>(),wires=new Set<string>();
    for(const model of calls){
      if(model.kind!=="model_call")return incomplete();const call=model.call;
      const matches=spec.expectedCalls.map((c,i)=>({c,i})).filter(({c})=>c.toolId===model.toolId&&c.inputHash===call.inputHash&&
        (call.link==="host_exposed"?!call.fixtureNonceHash||call.fixtureNonceHash===c.fixtureNonceHash:call.fixtureNonceHash===c.fixtureNonceHash));
      if(matches.length!==1||used.has(matches[0]!.i)||hostIds.has(call.hostCallId)||upstreamIds.has(call.upstreamCallId)||model.wireCallId!==call.upstreamCallId)return incomplete();
      used.add(matches[0]!.i);hostIds.add(call.hostCallId);upstreamIds.add(call.upstreamCallId);const expected=matches[0]!.c;
      const chain=(e:TraceEvent)=>e.runId===model.runId&&e.scopeHash===model.scopeHash&&e.sessionId===model.sessionId&&e.chainId===model.chainId;
      const tool=(e:TraceEvent)=>"toolId" in e&&e.toolId===model.toolId&&e.toolSchemaHash===model.toolSchemaHash&&e.originHash===model.originHash;
      const linked=(e:TraceEvent)=>chain(e)&&tool(e)&&"call" in e&&!!e.call&&fixtureHash(e.call)===fixtureHash(call);
      const one=(kind:TraceEvent["kind"],predicate:(e:TraceEvent)=>boolean)=>{const list=events.filter(e=>e.kind===kind&&predicate(e));return list.length===1?list[0]:undefined;};
      const definition=one("definition",e=>chain(e)&&tool(e)&&e.requestId===model.requestId),start=one("executor_started",linked),end=one("executor_completed",linked),next=one("next_model_request",linked);
      const final=next&&one("final_response",e=>chain(e)&&e.requestId===next.requestId);
      if(!definition||!start||!end||!next||next.kind!=="next_model_request"||!final||start.requestId!==model.requestId||end.requestId!==model.requestId||next.requestId===model.requestId||
        end.observationHash!==expected.observationHash||next.observationHash!==expected.observationHash||![definition,model,start,end,next,final].every((e,i,a)=>i===0||a[i-1]!.sequence<e.sequence))return incomplete();
      const wire=`${next.requestId}:${next.wireCallId}`;if(wires.has(wire))return incomplete();wires.add(wire);
      result.push(parseRoundTripProof({id:`proof-${model.sequence}`,caseId,scopeHash:model.scopeHash,sessionId:model.sessionId,toolId:model.toolId,toolSchemaHash:model.toolSchemaHash,
        ...(model.originHash?{originHash:model.originHash}:{}),chainId:model.chainId,requestKind:"primary",primaryRequestId:model.requestId,nextRequestId:next.requestId,
        hostMessageId:call.hostMessageId,hostCallId:call.hostCallId,upstreamCallId:call.upstreamCallId,continuationWireCallId:next.wireCallId,
        definitionEvent:definition.sequence,modelCallEvent:model.sequence,executorStartEvent:start.sequence,executorEndEvent:end.sequence,nextRequestEvent:next.sequence,finalResponseEvent:final.sequence}));
    }
    const references=new Set(result.flatMap(p=>[p.definitionEvent,p.modelCallEvent,p.executorStartEvent,p.executorEndEvent,p.nextRequestEvent,p.finalResponseEvent]));
    if(events.some(e=>!["state_observed","executor_released"].includes(e.kind)&&!references.has(e.sequence)))return incomplete();
    return Object.freeze(result);
  }
  return Object.freeze({record,events:()=>Object.freeze([...stored]),diagnostics:()=>Object.freeze({auxiliary,bytes}),finishAll:proofs,
    finish(caseId:string):RoundTripProof|Incomplete{const p=proofs(caseId);return Array.isArray(p)?p.length===1?p[0]!:incomplete():p as Incomplete;}});
}

/** Compare descriptors before values so accessors/private extra keys are not evaluated. */
export function hashOwnedFixture(value:unknown,expected:unknown):string {
  const hash=fixtureHash(expected);
  const exact=(a:unknown,b:unknown):boolean=>{
    if(a===b)return true;
    if(!a||!b||typeof a!=="object"||typeof b!=="object"||Object.getPrototypeOf(a)!==Object.getPrototypeOf(b))return false;
    const aa=Reflect.ownKeys(a),bb=Reflect.ownKeys(b);return aa.length===bb.length&&aa.every(k=>{
      const actual=Object.getOwnPropertyDescriptor(a,k),wanted=Object.getOwnPropertyDescriptor(b,k);
      return !!actual&&!!wanted&&"value" in actual&&"value" in wanted&&actual.enumerable===wanted.enumerable&&exact(actual.value,wanted.value);
    });
  };
  try{if(!exact(value,expected))throw new Error();return hash;}catch{throw new Error("redaction_failed");}
}

/** Exact fixture shape comparison precedes hashing: rejected private fields are never hashed. */
export async function inspectOwnedRequest(request:Request,expected:unknown,maxBytes:number,cancelOwnedTurn:()=>void):Promise<string>{
  if(!Number.isSafeInteger(maxBytes)||maxBytes<1||maxBytes>16_777_216)throw new Error("invalid_report");
  fixtureHash(expected);expected=structuredClone(expected);const reader=request.clone().body?.getReader();let bytes=0;const chunks:Uint8Array[]=[];
  try{
    if(!reader)throw new Error("redaction_failed");
    for(;;){const item=await reader.read();if(item.done)break;bytes+=item.value.byteLength;if(bytes>maxBytes)throw new Error("incomplete_evidence");chunks.push(item.value);}
    const body:unknown=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(Buffer.concat(chunks)));
    return hashOwnedFixture(body,expected);
  }catch(error){void reader?.cancel().catch(()=>{});quiet(cancelOwnedTurn);throw new Error(error instanceof Error&&error.message==="incomplete_evidence"?"incomplete_evidence":"redaction_failed");}
  finally{reader?.releaseLock();}
}

type TapOptions={maxBytes:number;onChunk:(bytes:Uint8Array)=>void;onClose?:()=>void;cancelOwnedTurn:()=>void;signal?:AbortSignal};
/** Pull-driven, no eager drain or tee. Callback validates bounded parts and stores hashes only. */
export function tapOwnedResponse(response:Response,options:TapOptions):Response{
  options={...options};
  if(!Number.isSafeInteger(options.maxBytes)||options.maxBytes<1||options.maxBytes>16_777_216)throw new Error("invalid_report");
  if(!response.body)return response;const source=response.body.getReader();let total=0,closed=false;let output:ReadableStreamDefaultController<Uint8Array>;
  const cleanup=()=>{options.signal?.removeEventListener("abort",onAbort);};
  const stop=(code:string,reason?:unknown)=>{if(closed)return;closed=true;cleanup();void source.cancel(reason).catch(()=>{}).finally(()=>source.releaseLock());quiet(options.cancelOwnedTurn);output.error(new Error(code));};
  const onAbort=()=>stop("cancelled",options.signal?.reason);
  const stream=new ReadableStream<Uint8Array>({start(c){output=c;options.signal?.addEventListener("abort",onAbort,{once:true});if(options.signal?.aborted)onAbort();},
    async pull(c){try{const item=await source.read();if(closed)return;if(item.done){options.onClose?.();closed=true;cleanup();source.releaseLock();c.close();return;}
      total+=item.value.byteLength;if(total>options.maxBytes){stop("incomplete_evidence");return;}options.onChunk(item.value.slice());c.enqueue(item.value);
    }catch{stop("redaction_failed");}},async cancel(reason){if(closed)return;closed=true;cleanup();try{await source.cancel(reason);}finally{source.releaseLock();}}
  },{highWaterMark:0});return new Response(stream,{status:response.status,statusText:response.statusText,headers:response.headers});
}
