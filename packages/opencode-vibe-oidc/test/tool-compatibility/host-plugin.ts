/** Fixture-only observer installed before the unchanged production provider. */
import { Plugin } from "@opencode/plugin";
import { Schema } from "effect";
import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { setupVibe } from "../../src/index.js";
import { fixtureHash } from "./cases.js";
import { createSyntheticInference } from "./synthetic-inference.js";
import { capabilityDecision, expectedNativeRead, type CapabilityEvent } from "./capability.js";
import { createRoundTripRecorder, tapOwnedResponse, hashOwnedFixture } from "./observe.js";
import type { CallCorrelation, TraceEvent } from "./contracts.js";
import type { ToolIdentity } from "./contracts.js";

export default Plugin.define({id:"tool-capability", async setup(ctx) {
  const original = globalThis.fetch;
  const registrations: {dispose():Promise<void>}[] = [];
  const events: CapabilityEvent[] = [];
  const inventory: {id:string; name:string; permission?:string; namespace?:string; schemaHash:string}[] = [];
  const offered: {case:"allowed"|"denied"; agent:string; definitions:{name:string;schemaHash:string}[]}[] = [];
  let failureCode="none";
  const permissions: {case:"allowed"|"denied"; action:string; effect:string; owned:boolean}[] = [];
  type Case = {kind:"allowed"|"denied"; path:string; nonce:string; expected:string; tool?:ToolIdentity; hostId?:string; resultHash?:string; final:boolean; delta:boolean; streamComplete:boolean; requests:number; driver?:ReturnType<typeof createSyntheticInference>; recorder?:ReturnType<typeof createRoundTripRecorder>; call?:CallCorrelation; inputObserved?:boolean; sequence?:number};
  const cases = new Map<string, Case>();
  const counts = {sdk:0,language:0,httpRequest:0,httpResponse:0,requests:0,chunks:0,ends:0,auxiliary:0};
  let stage = "setup", nativeName = "", nativeId = "", ready = false;
  const safe = (value:string) => {if (!/^[a-zA-Z0-9_.:/-]{1,160}$/.test(value)) throw new Error("HOST_CAPTURE"); return value;};
  const add = (c: NonNullable<ReturnType<typeof cases.get>>, kind:CapabilityEvent["kind"]) => {
    if (events.length >= 256) throw new Error("HOST_CAPTURE");
    events.push({case:c.kind,kind,link:"owned-"+c.kind});
  };
  const wait = async (predicate:()=>boolean) => {const end=Date.now()+15_000; while (!predicate()) {if(Date.now()>end) throw new Error("HOST_TIMEOUT"); await new Promise(r=>setTimeout(r,25));}};
  // Only the allowed owned read is a positive recorder smoke, not family coverage.
  const trace=(sessionId:string,c:Case,kind:TraceEvent["kind"],continuation=false)=>{
    if(!c.recorder||!c.tool)return;
    const base={sequence:c.sequence=(c.sequence??0)+1,runId:"capability-recorder",caseId:"capability-allowed",scopeHash:fixtureHash({profile:"owned-capability"}),sessionId,chainId:"chain-allowed",requestId:continuation?"request-2":"request-1",requestKind:"primary",payloadHash:fixtureHash({stage:kind,schemaHash:c.tool.schemaHash,inputHash:fixtureHash({path:c.path}),...(c.resultHash?{observationHash:c.resultHash}:{})})};
    c.recorder.record({...base,kind,...(kind!=="final_response"?{toolId:c.tool.registryId,toolSchemaHash:c.tool.schemaHash,originHash:c.tool.originHash}:{}),
      ...(["model_call","executor_started","executor_completed","next_model_request"].includes(kind)?{call:c.call}:{}),
      ...(["model_call","next_model_request"].includes(kind)?{wireCallId:"owned-allowed"}:{}),
      ...(["executor_completed","next_model_request"].includes(kind)?{observationHash:c.resultHash}:{})});
  };
  registrations.push(await ctx.session.hook("context", e=>{
    const c=cases.get(e.sessionID); if(!c) throw new Error("HOST_CORRELATION");
    const definitions=Object.entries(e.tools).map(([name,d])=>({name:safe(name),schemaHash:fixtureHash({description:d.description,inputSchema:d.input})}));
    if(definitions.length>256 || offered.length>=8) throw new Error("HOST_CAPTURE");
    offered.push({case:c.kind,agent:safe(e.agent),definitions});
    const definition=e.tools[nativeName]; if(!definition) throw new Error("HOST_DEFINITION");
    c.tool={registryId:nativeId,declaredName:nativeName,offeredName:nativeName,origin:"native",originHash:fixtureHash({host:"2.2.5",registryId:nativeId}),schemaHash:fixtureHash({description:definition.description,inputSchema:definition.input})};
    if(c.requests===0)trace(e.sessionID,c,"definition");
    add(c,"context");
  }));
  registrations.push(await ctx.session.hook("http.request", e=>{counts.httpRequest++; const c=cases.get(e.sessionID); if(c && e.kind==="primary") add(c,"http_request");}));
  registrations.push(await ctx.session.hook("http.response", e=>{counts.httpResponse++; const c=cases.get(e.sessionID); if(c && e.kind==="primary") add(c,"http_response");}));
  registrations.push(await ctx.aisdk.hook("sdk", ()=>{counts.sdk++;},{providerID:"vibe-oidc"}));
  registrations.push(await ctx.aisdk.hook("language", ()=>{counts.language++;},{providerID:"vibe-oidc"}));
  registrations.push(await ctx.tool.hook("execute.before", e=>{
    const c=cases.get(e.sessionID);
    if(!c || e.tool!==nativeName || e.id!=="owned-"+c.kind || hashOwnedFixture(e.input,{path:c.path})!==fixtureHash({path:c.path})) throw new Error("HOST_TOOL_DENIED");
    c.hostId=e.id; add(c,"dispatch");
    if(c.recorder){if(!c.inputObserved)throw new Error("HOST_CORRELATION");c.call={hostMessageId:e.messageID,hostCallId:e.id,upstreamCallId:"owned-allowed",inputHash:fixtureHash({path:c.path}),link:"host_exposed"};trace(e.sessionID,c,"model_call");trace(e.sessionID,c,"executor_started");}
  }));
  registrations.push(await ctx.permission.hook("evaluate", e=>{
    const c=cases.get(e.sessionID); if(!c) throw new Error("HOST_PERMISSION");
    const owned=e.resources.length===1 && e.resources[0]==="capability-"+c.kind+".txt";
    permissions.push({case:c.kind,action:safe(e.action),effect:e.effect,owned});
    if(!owned || e.action!=="read" || permissions.length>16) throw new Error("HOST_PERMISSION");
    if(e.effect==="allow") add(c,"permission_allow");
    if(e.effect==="deny") add(c,"permission_deny");
  }));
  registrations.push(await ctx.tool.hook("execute.after", e=>{
    const c=cases.get(e.sessionID); if(!c || e.id!==c.hostId || e.tool!==nativeName) throw new Error("HOST_CORRELATION");
    if(e.status==="completed") {
      const content=Array.isArray(e.result.content) && e.result.content.length===1 && e.result.content[0]?.type==="text" ? e.result.content[0].text : e.result.content;
      if(c.kind!=="allowed" || content!==c.expected) throw new Error("HOST_RESULT");
      c.resultHash=fixtureHash(c.expected); add(c,"completed");
      trace(e.sessionID,c,"executor_completed");
    } else {
      // 2.2.5 short-circuits deny rules before permission.evaluate. Inspect only
      // these allowlisted fields of the actual nested host permission error.
      const cause=e.error.error as {_tag?:unknown;permission?:unknown;resources?:unknown};
      if(c.kind!=="denied" || cause?._tag!=="Permission.BlockedError" || cause.permission!=="read" || hashOwnedFixture(cause.resources,["capability-denied.txt"])!==fixtureHash(["capability-denied.txt"]) || e.error.message!==`Unable to read ${c.path}`) throw new Error("HOST_DENIAL");
      c.resultHash=fixtureHash(c.expected); add(c,"permission_deny"); add(c,"error");
    }
  }));
  // The custom SDK resolves fetch during setup. Install the owned boundary first.
  globalThis.fetch=Object.assign(async(input:RequestInfo|URL,init?:RequestInit)=>{
    const request=new Request(input,init), url=new URL(request.url);
    if(url.origin!=="https://inference.example.test" || request.headers.get("authorization")!=="Bearer synthetic-one") throw new Error("HOST_NETWORK");
    if(request.method==="GET" && url.pathname==="/v1/models") return Response.json({data:[{id:"synthetic-model",name:"Synthetic",capabilities:{tools:true,input:["text"],output:["text"]},limit:{context:131072,output:4096},time:{released:0},status:"active",cost:[{input:0,output:0,cache:{read:0,write:0}}]}]});
    if(request.method!=="POST" || url.pathname!=="/v1/chat/completions" || ++counts.requests>12) throw new Error("HOST_NETWORK");
    const clone=request.clone();
    const reader=clone.body!.getReader(); let length=0; const bytes:Uint8Array[]=[];
    try {while(true){const item=await reader.read(); if(item.done)break;length+=item.value.length;if(length>131072)throw new Error("HOST_CAPTURE");bytes.push(item.value);}} catch(e){void reader.cancel();void request.body?.cancel();throw e;} finally {reader.releaseLock();}
    const body=JSON.parse(new TextDecoder().decode(Buffer.concat(bytes)));
    if(!Array.isArray(body.messages)) throw new Error("HOST_CORRELATION");
    const matches=[...cases.entries()].filter(([,c])=>body.messages.some((m:{role?:string;content?:unknown})=>m.role==="user" && (m.content==="owned-capability-"+c.kind || Array.isArray(m.content) && m.content.some(p=>p.type==="text" && p.text==="owned-capability-"+c.kind))));
    // Auxiliary routes are deliberately unverified by this two-primary-case probe.
    if(!body.tools?.length){counts.auxiliary++;throw new Error("HOST_AUXILIARY_UNVERIFIED");}
    if(matches.length!==1)throw new Error("HOST_CORRELATION");
    const [sessionId,c]=matches[0]!; if(!c.tool) throw new Error("HOST_DEFINITION");
    if(++c.requests>2)throw new Error("HOST_REPLAY");
    add(c,"transport_request");
    if(!c.driver){
      c.driver=createSyntheticInference({caseId:"capability-"+c.kind,scopeHash:fixtureHash({profile:"owned-capability"}),sessionId,chainId:"chain-"+c.kind,model:"synthetic-model",answer:"Owned final "+c.kind,calls:[{tool:c.tool,callId:"owned-"+c.kind,input:{path:c.path},validateInput:i=>fixtureHash(i)===fixtureHash({path:c.path}),result:c.expected}]});
    }
    const response=await c.driver.forRequest({requestId:"request-"+counts.requests,requestKind:"primary",scopeHash:fixtureHash({profile:"owned-capability"}),sessionId,chainId:"chain-"+c.kind,offered:[c.tool]})(request);
    if(c.requests===2){if(c.resultHash!==fixtureHash(c.expected))throw new Error("HOST_RESULT");add(c,"continuation");trace(sessionId,c,"next_model_request",true);}
    const source=response.body!.getReader(); let seen=0;
    const tapped=new Response(new ReadableStream<Uint8Array>({async pull(controller){
      // Withhold the rest until the real host observes tool input start. This proves
      // the observer did not wait for the entire stream before delivering it.
      if(c.requests===1 && seen===3)await wait(()=>c.delta);
      const item=await source.read();
      if(item.done){counts.ends++;c.streamComplete=true;add(c,"transport_end");controller.close();source.releaseLock();return;}
      if(++seen>256) {await source.cancel();throw new Error("HOST_CAPTURE");}
      counts.chunks++;add(c,"transport_chunk");controller.enqueue(item.value);
    },cancel(reason){return source.cancel(reason);}}, {highWaterMark:0}),{status:response.status,headers:response.headers});
    return tapOwnedResponse(tapped,{maxBytes:131072,onChunk:()=>{},cancelOwnedTurn:()=>{void ctx.session.interrupt({sessionID:sessionId});},signal:request.signal});
  },{preconnect(){}});
  const cleanup=await setupVibe(ctx);
  let connected=false;
  setTimeout(()=>{void(async()=>{
    try {
      stage="connect";
      const attempt=await ctx.integration.command.connect({integrationID:"vibe-oidc",methodID:"vibe-browser",label:"synthetic"});
      const end=Date.now()+15_000;
      while(!connected && Date.now()<end){const s=(await ctx.integration.command.status({integrationID:"vibe-oidc",attemptID:attempt.data.attemptID})).data; if(s.status==="failed")throw new Error("HOST_AUTH");connected=s.status==="complete";if(!connected)await new Promise(r=>setTimeout(r,25));}
      if(!connected)throw new Error("HOST_TIMEOUT");
      stage="model";
      while(!JSON.stringify(await ctx.model.list()).includes('"synthetic-model"')){if(Date.now()>end)throw new Error("HOST_MODEL");await new Promise(r=>setTimeout(r,25));}
      stage="inventory";
      for(const t of await ctx.tool.list()) {
        if(inventory.length>=256)throw new Error("HOST_CAPTURE");
        const schema=Schema.isSchema(t.input)?Schema.toJsonSchemaDocument(t.input).schema:t.input;
        inventory.push({id:safe(t.id),name:safe(t.name),...(t.options?.namespace?{namespace:safe(t.options.namespace)}:{}),...(t.options?.permission?{permission:safe(t.options.permission)}:{}),schemaHash:fixtureHash({description:t.description,inputSchema:schema})});
      }
      // Exact pinned native registration, not a name-prefix provenance guess.
      const native=inventory.find(t=>t.name==="read" && t.id==="read");if(!native)throw new Error("HOST_NATIVE_UNAVAILABLE");nativeName=native.name;nativeId=native.id;
      for(const kind of ["allowed","denied"] as const){
        stage=kind;
        const path=join(ctx.location.directory,"capability-"+kind+".txt");const nonce=crypto.randomUUID();writeFileSync(path,nonce);
        const session=await ctx.session.create({title:"owned capability "+kind,model:{providerID:"vibe-oidc",id:"synthetic-model"}});
        // Reference host's oI error normalization preserves the nested permission
        // reason; the installed custom SDK serializes this known error as JSON.
        const c:Case={kind,path,nonce,expected:kind==="allowed"?expectedNativeRead(path,nonce):JSON.stringify({error:{type:"permission.rejected",message:"Permission denied: read"},content:[]}),final:false,delta:false,streamComplete:false,requests:0};cases.set(session.id,c);
        if(kind==="allowed")c.recorder=createRoundTripRecorder({runId:"capability-recorder",limits:{startupMs:1000,discoveryMs:1000,cancellationMs:1000,runMs:15000,traceBytes:131072,eventsPerCase:64,toolCallsPerCase:1},cancelOwnedTurn:()=>{void ctx.session.interrupt({sessionID:session.id});},cases:[{id:"capability-allowed",toolIds:[nativeId],prerequisites:[],fixtureKind:"filesystem",expectedObservationHash:fixtureHash(nonce),positiveRequired:true,timeoutMs:15000,expectedCalls:[{toolId:nativeId,fixtureNonceHash:fixtureHash(nonce),inputHash:fixtureHash({path}),observationHash:fixtureHash(c.expected)}],expectedOutcome:"success",requiredObservations:["definition","model_call","executor_started","executor_completed","next_model_request","final_response"],executorExpectation:"must_complete"}]});
        const abort=new AbortController();
        const observation=(async()=>{for await(const e of ctx.event.subscribe({signal:abort.signal})){
          if(e.type==="session.tool.input.started" && e.data.sessionID===session.id && e.data.id==="owned-"+kind && !c.delta){if(c.streamComplete)throw new Error("HOST_BUFFERED");c.delta=true;add(c,"input_started");}
          if(e.type==="session.tool.input.ended" && e.data.sessionID===session.id){if(e.data.id!=="owned-"+kind || e.data.text.length>1024 || hashOwnedFixture(JSON.parse(e.data.text),{path})!==fixtureHash({path}))throw new Error("HOST_CORRELATION");c.inputObserved=true;add(c,"input_ended");}
          if(e.type==="session.execution.succeeded" && e.data.sessionID===session.id){trace(session.id,c,"final_response",true);c.final=true;add(c,"final");return;}
          if(e.type==="session.execution.failed" && e.data.sessionID===session.id)throw new Error("HOST_EXECUTION");
        }})();
        try{await ctx.session.prompt({sessionID:session.id,text:"owned-capability-"+kind});await wait(()=>c.final);await observation;}
        finally{abort.abort();await observation.catch(()=>{});await ctx.session.interrupt({sessionID:session.id});}
        if(readFileSync(path,"utf8")!==nonce)throw new Error("HOST_STATE");
        add(c,"state_unchanged");
      }
      stage="decision"; ready=capabilityDecision(events)==="observed" && counts.sdk>0 && counts.language>0;
      const recorded=[...cases.values()].find(c=>c.recorder)?.recorder?.finish("capability-allowed");if(!recorded||"status" in recorded)throw new Error("HOST_CORRELATION");
    } catch(e) {ready=false;failureCode=e instanceof Error && /^HOST_[A-Z_]+$/.test(e.message)?e.message:"HOST_FAILURE";}
    const links=[...cases.entries()].map(([session,c])=>({case:c.kind,sessionHash:fixtureHash(session),upstreamCallHash:fixtureHash("owned-"+c.kind),...(c.hostId?{hostCallHash:fixtureHash(c.hostId)}:{}),expectedResultHash:fixtureHash(c.expected),...(c.resultHash?{actualResultHash:c.resultHash}:{})}));
    const profileHash=fixtureHash({fixture:"tool-capability",permission:JSON.parse(readFileSync(join(ctx.location.directory,"opencode.json"),"utf8")).permission});
    const providerBuildHash=fixtureHash({index:readFileSync(resolve(import.meta.dir,"../../src/index.ts"),"utf8"),provider:readFileSync(resolve(import.meta.dir,"../../src/provider.ts"),"utf8")});
    const recorded=[...cases.values()].find(c=>c.recorder)?.recorder;
    writeFileSync(String(ctx.options.readiness),JSON.stringify({ready,status:ready?"observed":"unsupported",code:ready?null:"hook_unavailable",stage,inventory,offered,permissions,counts,events,failureCode,links,profileHash,providerBuildHash,recorder:recorded?{events:recorded.events(),proof:recorded.finish("capability-allowed")}:null,transport:"fixture-owned-before-provider",denialSource:"execute.after.error.error.Permission.BlockedError",auxiliary:"unverified",live:"unverified",allToolCompatibility:"unverified"}));
  })();},50);
  return async()=>{for(const r of registrations.reverse())await r.dispose();await cleanup();globalThis.fetch=original;};
}});
