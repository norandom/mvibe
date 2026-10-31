/** Explicit family probe. Retains every offered definition and actual native executor. */
import { Plugin } from "@opencode/plugin";
import { Schema } from "effect";
import { readFileSync, writeFileSync } from "node:fs";
import { setupVibe } from "../../src/index.js";
import { fixtureHash, DEFAULT_CASE_MANIFEST } from "./cases.js";
import { createWorkspaceQueries, type WorkspaceQuery } from "./fixtures/workspace.js";
import { createSyntheticInference } from "./synthetic-inference.js";
import { createRoundTripRecorder, hashOwnedFixture, tapOwnedResponse } from "./observe.js";
import type { CallCorrelation, ToolIdentity, TraceEvent } from "./contracts.js";

export default Plugin.define({id:"tool-workspace",async setup(ctx){
  const original=globalThis.fetch, registrations:{dispose():Promise<void>}[]=[];
  const fixture=createWorkspaceQueries(ctx.location.directory), scopeHash=fixtureHash({fixture:"workspace-query",permission:JSON.parse(readFileSync(ctx.location.directory+"/opencode.json","utf8")).permission});
  type Case={q:WorkspaceQuery;sessionId:string;requests:number;final:boolean;inputObserved:boolean;state?:string;result?:string;tool?:ToolIdentity;call?:CallCorrelation;driver?:ReturnType<typeof createSyntheticInference>;recorder:ReturnType<typeof createRoundTripRecorder>};
  const cases=new Map<string,Case>();
  const inventory:{id:string;name:string;schemaHash:string}[]=[], offered:{caseId:string;definitions:{name:string;schemaHash:string}[]}[]=[], permissions:{caseId:string;action:string;effect:string;resourceHash:string}[]=[];
  let sequence=0;
  let stage="setup",failureCode="none",ready=false;
  const safe=(s:string)=>{if(!/^[a-zA-Z0-9_.:/-]{1,160}$/.test(s))throw new Error("HOST_CAPTURE");return s;};
  const wait=async(predicate:()=>boolean,timeoutMs=15_000)=>{const end=Date.now()+timeoutMs;while(!predicate()){if(Date.now()>end)throw new Error("HOST_TIMEOUT");await new Promise(r=>setTimeout(r,25));}};
  const callId=(c:Case)=>"workspace-"+c.q.name;
  const trace=(c:Case,kind:TraceEvent["kind"],next=false)=>{
    if(!c.tool)throw new Error("HOST_DEFINITION");
    c.recorder.record({sequence:++sequence,runId:"workspace-queries",caseId:c.q.spec.id,scopeHash,sessionId:c.sessionId,chainId:"chain-"+c.q.name,requestId:next?"request-2":"request-1",requestKind:"primary",kind,
      payloadHash:fixtureHash({kind,input:c.q.spec.expectedCalls[0]!.inputHash}),
      ...(kind!=="final_response"?{toolId:c.tool.registryId,toolSchemaHash:c.tool.schemaHash,originHash:c.tool.originHash}:{}),
      ...(["model_call","executor_started","executor_completed","next_model_request"].includes(kind)?{call:c.call}:{}),
      ...(["model_call","next_model_request"].includes(kind)?{wireCallId:callId(c)}:{}),
      ...(["executor_completed","next_model_request"].includes(kind)?{observationHash:c.result}:{}),
      ...(kind==="state_observed"?{observationHash:c.state}:{})});
  };
  registrations.push(await ctx.session.hook("context",e=>{
    const c=cases.get(e.sessionID);if(!c)throw new Error("HOST_CORRELATION");
    if(offered.length>=8)throw new Error("HOST_CAPTURE");
    const definitions=Object.entries(e.tools).map(([name,t])=>({name:safe(name),schemaHash:fixtureHash({description:t.description,inputSchema:t.input})}));
    if(definitions.length>256)throw new Error("HOST_CAPTURE");offered.push({caseId:c.q.spec.id,definitions});
    const definition=e.tools[c.q.name];
    if(!definition||!inventory.some(t=>t.id===c.q.name&&t.name===c.q.name))throw new Error("HOST_DEFINITION");
    const tool:ToolIdentity={registryId:c.q.name,declaredName:c.q.name,offeredName:c.q.name,origin:"native",originHash:fixtureHash({host:"2.2.5",registryId:c.q.name}),schemaHash:fixtureHash({description:definition.description,inputSchema:definition.input})};
    if(c.tool&&c.tool.schemaHash!==tool.schemaHash)throw new Error("HOST_DEFINITION");c.tool=tool;
    if(!c.requests)trace(c,"definition");
  }));
  registrations.push(await ctx.tool.hook("execute.before",e=>{
    const c=cases.get(e.sessionID);
    if(!c||!c.inputObserved||e.tool!==c.q.name||e.id!==callId(c)||hashOwnedFixture(e.input,c.q.input)!==c.q.spec.expectedCalls[0]!.inputHash)throw new Error("HOST_TOOL_DENIED");
    c.call={hostMessageId:e.messageID,hostCallId:e.id,upstreamCallId:callId(c),inputHash:c.q.spec.expectedCalls[0]!.inputHash,link:"host_exposed"};
    trace(c,"model_call");trace(c,"executor_started");
  }));
  registrations.push(await ctx.permission.hook("evaluate",e=>{
    const c=cases.get(e.sessionID);if(!c||e.action!==c.q.name||e.effect!=="allow"||permissions.length>=8)throw new Error("HOST_PERMISSION");
    const resource=c.q.name==="read"?fixture.directory.slice(ctx.location.directory.length+1)+"/owned.match.txt":c.q.input.pattern;
    hashOwnedFixture(e.resources,[resource]);permissions.push({caseId:c.q.spec.id,action:safe(e.action),effect:e.effect,resourceHash:fixtureHash([resource])});
  }));
  registrations.push(await ctx.tool.hook("execute.after",e=>{
    const c=cases.get(e.sessionID);if(!c||e.tool!==c.q.name||e.id!==c.call?.hostCallId||e.status!=="completed")throw new Error("HOST_RESULT");
    const content=Array.isArray(e.result.content)&&e.result.content.length===1&&e.result.content[0]?.type==="text"?e.result.content[0].text:e.result.content;
    c.result=hashOwnedFixture(content,c.q.expected);trace(c,"executor_completed");
    c.state=fixture.inspect();if(c.state!==c.q.spec.expectedObservationHash)throw new Error("HOST_STATE");trace(c,"state_observed");
  }));
  // Install before production setup because the pinned custom SDK captures fetch.
  globalThis.fetch=Object.assign(async(input:RequestInfo|URL,init?:RequestInit)=>{
    const request=new Request(input,init),url=new URL(request.url);
    if(url.origin!=="https://inference.example.test"||request.headers.get("authorization")!=="Bearer synthetic-one")throw new Error("HOST_NETWORK");
    if(request.method==="GET"&&url.pathname==="/v1/models")return Response.json({data:[{id:"synthetic-model",name:"Synthetic",capabilities:{tools:true,input:["text"],output:["text"]},limit:{context:131072,output:4096},time:{released:0},status:"active",cost:[{input:0,output:0,cache:{read:0,write:0}}]}]});
    if(request.method!=="POST"||url.pathname!=="/v1/chat/completions")throw new Error("HOST_NETWORK");
    const reader=request.clone().body!.getReader(),bytes:Uint8Array[]=[];let length=0;
    try{for(;;){const item=await reader.read();if(item.done)break;length+=item.value.length;if(length>131072)throw new Error("HOST_CAPTURE");bytes.push(item.value);}}catch(e){void reader.cancel();void request.body?.cancel();throw e;}finally{reader.releaseLock();}
    const body=JSON.parse(new TextDecoder().decode(Buffer.concat(bytes)));
    const matches=[...cases.values()].filter(c=>Array.isArray(body.messages)&&body.messages.some((m:{role?:string;content?:unknown})=>m.role==="user"&&(m.content==="owned-"+c.q.spec.id||Array.isArray(m.content)&&m.content.some(p=>p.type==="text"&&p.text==="owned-"+c.q.spec.id))));
    if(matches.length!==1||!body.tools?.length)throw new Error("HOST_CORRELATION");
    const c=matches[0]!;if(!c.tool||++c.requests>2)throw new Error("HOST_REPLAY");
    // Validate the entire wire offering against the actual context set, retaining
    // every identity/schema; never replace body.tools or context.tools.
    const observed=offered.filter(o=>o.caseId===c.q.spec.id).at(-1)!;
    if(body.tools.length!==observed.definitions.length||body.tools.some((t:{function?:{name?:string;description?:string;parameters?:unknown}})=>!observed.definitions.some(d=>d.name===t.function?.name&&d.schemaHash===fixtureHash({description:t.function.description,inputSchema:t.function.parameters}))))throw new Error("HOST_DEFINITION");
    if(!c.driver)c.driver=createSyntheticInference({caseId:c.q.spec.id,scopeHash,sessionId:c.sessionId,chainId:"chain-"+c.q.name,model:"synthetic-model",answer:"Owned final "+c.q.name,calls:[{tool:c.tool,callId:callId(c),input:c.q.input,result:c.q.expected,validateInput:v=>fixtureHash(v)===c.q.spec.expectedCalls[0]!.inputHash}]});
    const response=await c.driver.forRequest({requestId:"request-"+c.requests,requestKind:"primary",scopeHash,sessionId:c.sessionId,chainId:"chain-"+c.q.name,offered:[c.tool]})(request);
    if(c.requests===2){if(c.result!==fixtureHash(c.q.expected)||c.state!==c.q.spec.expectedObservationHash)throw new Error("HOST_RESULT");trace(c,"next_model_request",true);}
    return tapOwnedResponse(response,{maxBytes:131072,onChunk:()=>{},cancelOwnedTurn:()=>{void ctx.session.interrupt({sessionID:c.sessionId});},signal:request.signal});
  },{preconnect(){}});
  const cleanup=await setupVibe(ctx);
  // Connect asynchronously so host setup can finish before model readiness.
  setTimeout(()=>{void(async()=>{
    try{
      stage="connect";const attempt=await ctx.integration.command.connect({integrationID:"vibe-oidc",methodID:"vibe-browser",label:"synthetic"});
      const end=Date.now()+15_000;let connected=false;
      while(!connected&&Date.now()<end){const s=(await ctx.integration.command.status({integrationID:"vibe-oidc",attemptID:attempt.data.attemptID})).data;if(s.status==="failed")throw new Error("HOST_AUTH");connected=s.status==="complete";if(!connected)await new Promise(r=>setTimeout(r,25));}
      if(!connected)throw new Error("HOST_TIMEOUT");stage="model";
      while(!JSON.stringify(await ctx.model.list()).includes('"synthetic-model"')){if(Date.now()>end)throw new Error("HOST_MODEL");await new Promise(r=>setTimeout(r,25));}
      stage="inventory";
      for(const t of await ctx.tool.list()){
        if(inventory.length>=256)throw new Error("HOST_CAPTURE");
        inventory.push({id:safe(t.id),name:safe(t.name),schemaHash:fixtureHash({description:t.description,inputSchema:Schema.isSchema(t.input)?Schema.toJsonSchemaDocument(t.input).schema:t.input})});
      }
      for(const q of fixture.queries){
        stage=q.name;
        const session=await ctx.session.create({title:"owned workspace "+q.name,model:{providerID:"vibe-oidc",id:"synthetic-model"}});
        const c:Case={q,sessionId:session.id,requests:0,final:false,inputObserved:false,recorder:createRoundTripRecorder({runId:"workspace-queries",cases:[q.spec],limits:DEFAULT_CASE_MANIFEST().limits,cancelOwnedTurn:()=>{void ctx.session.interrupt({sessionID:session.id});}})};cases.set(session.id,c);
        const abort=new AbortController();
        const observation=(async()=>{for await(const e of ctx.event.subscribe({signal:abort.signal})){
          if(e.type==="session.tool.input.ended"&&e.data.sessionID===session.id){if(e.data.id!==callId(c)||e.data.text.length>2048)throw new Error("HOST_CORRELATION");hashOwnedFixture(JSON.parse(e.data.text),q.input);c.inputObserved=true;}
          if(e.type==="session.execution.succeeded"&&e.data.sessionID===session.id){trace(c,"final_response",true);c.final=true;return;}
          if(e.type==="session.execution.failed"&&e.data.sessionID===session.id)throw new Error("HOST_EXECUTION");
        }})();
        try{await ctx.session.prompt({sessionID:session.id,text:"owned-"+q.spec.id});await wait(()=>c.final,q.spec.timeoutMs);await observation;}finally{abort.abort();await observation.catch(()=>{});await ctx.session.interrupt({sessionID:session.id});}
        fixture.inspect();const proof=c.recorder.finish(q.spec.id);if("status" in proof)throw new Error("HOST_CORRELATION");
        if(permissions.filter(p=>p.caseId===q.spec.id).length!==1)throw new Error("HOST_PERMISSION");
      }
      stage="complete";ready=true;
    }catch(e){failureCode=e instanceof Error&&/^HOST_[A-Z_]+$/.test(e.message)?e.message:"HOST_FAILURE";}
    const records=[...cases.values()].map(c=>({caseId:c.q.spec.id,expectedInputHash:c.q.spec.expectedCalls[0]!.inputHash,expectedResultHash:fixtureHash(c.q.expected),actualResultHash:c.result??null,expectedStateHash:c.q.spec.expectedObservationHash,actualStateHash:c.state??null,requests:c.requests,events:c.recorder.events(),proof:c.recorder.finish(c.q.spec.id)}));
    writeFileSync(String(ctx.options.readiness),JSON.stringify({ready,stage,failureCode,scopeHash,inventory,offered,permissions,records,live:"unverified",allToolCompatibility:"unverified"}));
  })();},50);
  return async()=>{for(const r of registrations.reverse())await r.dispose();await cleanup();globalThis.fetch=original;};
}});
