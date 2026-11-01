/** Explicit host-owned MCP client probe; model transport never dispatches tools. */
import { Plugin } from "@opencode/plugin";
import { Schema } from "effect";
import { readFileSync,writeFileSync,statSync } from "node:fs";
import { setupVibe } from "../../src/index.js";
import { fixtureHash,DEFAULT_CASE_MANIFEST,materializeCase } from "./cases.js";
import { hashOwnedFixture,createRoundTripRecorder } from "./observe.js";
import { createSyntheticInference } from "./synthetic-inference.js";
import { MCP_TOOL,mcpRecipe } from "./fixtures/mcp-recipe.js";
import type { ToolIdentity } from "./contracts.js";
export default Plugin.define({id:"tool-mcp",async setup(ctx){
 const original=globalThis.fetch,disposers:{dispose():Promise<void>}[]=[];
 const recipe=mcpRecipe(String(ctx.options.mcpNonce)),scopeHash=fixtureHash({recipe,server:"owned",host:"2.2.5"}),runId="mcp-baseline";
 const records:any[]=[],inventory:any[]=[],permissions:any[]=[];let sequence=0;let current:any,stage="setup",failureCode="none",ready=false;
 const log=()=>{const path=String(ctx.options.mcpLog);if(statSync(path).size>32768)throw new Error("HOST_CAPTURE");return readFileSync(path,"utf8").trim().split("\n").filter(Boolean).map(v=>JSON.parse(v));};
 const wait=async(p:()=>boolean,ms=12000)=>{const end=Date.now()+ms;while(!p()){if(Date.now()>end)throw new Error("HOST_TIMEOUT");await new Promise(r=>setTimeout(r,25));}};
 const trace=(c:any,kind:any,next=false)=>{if(!c.recorder)return;c.recorder.record({sequence:++sequence,runId,caseId:c.caseId,scopeHash,sessionId:c.sessionId,chainId:c.chainId,requestKind:"primary",requestId:next?"request-2":"request-1",kind,payloadHash:fixtureHash({kind}),...(kind!=="final_response"?{toolId:c.tool.registryId,toolSchemaHash:c.tool.schemaHash,originHash:c.tool.originHash}:{}),...(["model_call","executor_started","executor_completed","next_model_request"].includes(kind)?{call:c.call}:{}),...(["model_call","next_model_request"].includes(kind)?{wireCallId:c.callId}:{}),...(["executor_completed","next_model_request"].includes(kind)?{observationHash:c.actualResultHash}:{}),...(kind==="state_observed"?{observationHash:c.actualStateHash}:{})});};
 disposers.push(await ctx.session.hook("model.request",e=>{if(!current||e.sessionID!==current.sessionId||e.kind!=="primary"||e.model.providerID!=="vibe-oidc"||e.model.id!=="synthetic-model")throw new Error("HOST_PRIMARY");current.primaryRequests??=[];current.primaryRequests.push({runId,scopeHash,sessionId:e.sessionID,chainId:current.chainId,requestKind:e.kind,model:e.model});}));
 disposers.push(await ctx.session.hook("context",e=>{
  if(!current||current.sessionId!==e.sessionID)throw new Error("HOST_CORRELATION");
  const definitions=Object.entries(e.tools).map(([name,t])=>({name,schemaHash:fixtureHash({description:t.description,inputSchema:t.input})}));
  if(definitions.length>256)throw new Error("HOST_CAPTURE");current.offered=definitions;current.definitions=Object.entries(e.tools).map(([name,t])=>({name,description:t.description,inputSchema:t.input}));
  const registered=inventory.filter(t=>t.name===MCP_TOOL.name||t.description===MCP_TOOL.description);
  if(registered.length!==1||registered[0].schemaHash!==fixtureHash(MCP_TOOL.inputSchema))throw new Error("HOST_DEFINITION");
  const found=definitions.filter(t=>t.name===registered[0].id);
  if(found.length!==1||fixtureHash(e.tools[found[0]!.name]!.input)!==fixtureHash(MCP_TOOL.inputSchema))throw new Error("HOST_DEFINITION");
  const tool:ToolIdentity={registryId:registered[0].id,declaredName:registered[0].name,offeredName:found[0]!.name,origin:"mcp",originHash:fixtureHash({server:"owned",schemaHash:fixtureHash(MCP_TOOL)}),schemaHash:found[0]!.schemaHash};
  if(current.tool&&fixtureHash(current.tool)!==fixtureHash(tool))throw new Error("HOST_DEFINITION");current.tool=tool;
  if(current.mode==="success"&&!current.recorder){const manifest=DEFAULT_CASE_MANIFEST(),template=manifest.cases.find(c=>c.spec.id==="mcp.echo")!;const objective="mcp.nested-unicode-typed-echo";const spec=materializeCase({...template,calls:[tool.registryId],spec:{...template.spec,id:current.caseId,toolIds:[tool.registryId]}},{recipeVersion:1,objective,observation:recipe.success.structured,calls:[{toolId:tool.registryId,nonce:String(ctx.options.mcpNonce),input:current.input,observation:recipe.success.result}]},manifest.limits);current.recorder=createRoundTripRecorder({runId,cases:[spec],limits:manifest.limits,cancelOwnedTurn:()=>{void ctx.session.interrupt({sessionID:current.sessionId});}});trace(current,"definition");}

 }));
 disposers.push(await ctx.tool.hook("execute.before",e=>{
  if(!current||e.sessionID!==current.sessionId||e.tool!==current.tool.registryId||e.id!==current.callId)throw new Error("HOST_CORRELATION");
  hashOwnedFixture(e.input,current.input);current.call={hostMessageId:e.messageID,hostCallId:e.id,upstreamCallId:current.callId,inputHash:fixtureHash(current.input),link:"host_exposed"};trace(current,"model_call");trace(current,"executor_started");
 }));
 disposers.push(await ctx.tool.hook("execute.after",e=>{
  if(!current||e.sessionID!==current.sessionId||e.id!==current.call?.hostCallId||e.messageID!==current.call.hostMessageId||e.tool!==current.tool.registryId)throw new Error("HOST_CORRELATION");
  current.hostStatus=e.status;
  if(current.mode==="success"){
   if(e.status!=="completed")throw new Error("HOST_RESULT");hashOwnedFixture(e.result.output,recipe.success.structured);hashOwnedFixture(e.result.content,[{type:"text",text:recipe.success.result}]);current.actualResultHash=fixtureHash(recipe.success.result);
   const calls=log().filter(e=>e.kind==="called"&&e.mode==="success"),returned=log().filter(e=>e.kind==="returned"&&e.mode==="success");
   if(calls.length!==1||returned.length!==1||calls[0].requestId!==returned[0].requestId||calls[0].inputHash!==fixtureHash(recipe.success.input)||returned[0].resultHash!==fixtureHash({structuredContent:recipe.success.structured,content:[{type:"text",text:recipe.success.result}]}))throw new Error("HOST_STATE");
   current.serverRequestId=calls[0].requestId;current.actualStateHash=fixtureHash(recipe.success.structured);trace(current,"executor_completed");trace(current,"state_observed");
  }else if(current.mode==="error"){
   if(e.status!=="error")throw new Error("HOST_RESULT");hashOwnedFixture(e.error.message,recipe.error.message);current.actualResultHash=fixtureHash(recipe.error.result);
   const calls=log().filter(e=>e.kind==="called"&&e.mode==="error"),returned=log().filter(e=>e.kind==="returned"&&e.mode==="error");if(calls.length!==1||returned.length!==1||calls[0].requestId!==returned[0].requestId||calls[0].inputHash!==fixtureHash(recipe.error.input)||returned[0].isError!==true||returned[0].resultHash!==fixtureHash({isError:true,content:[{type:"text",text:recipe.error.message}]}))throw new Error("HOST_STATE");current.serverRequestId=calls[0].requestId;
  }
 }));
 disposers.push(await ctx.permission.hook("evaluate",e=>{if(!current||e.sessionID!==current.sessionId||e.effect!=="allow")throw new Error("HOST_PERMISSION");permissions.push({caseId:current.caseId,action:e.action,effect:e.effect,resources:e.resources});}));
 globalThis.fetch=Object.assign(async(input:RequestInfo|URL,init?:RequestInit)=>{
  const request=new Request(input,init),url=new URL(request.url);
  if(url.origin!=="https://inference.example.test"||request.headers.get("authorization")!=="Bearer synthetic-one")throw new Error("HOST_NETWORK");
  if(request.method==="GET"&&url.pathname==="/v1/models")return Response.json({data:[{id:"synthetic-model",name:"Synthetic owned fixture",capabilities:{tools:true,input:["text"],output:["text"]},limit:{context:131072,output:4096},time:{released:0},status:"active",cost:[{input:0,output:0,cache:{read:0,write:0}}]}]});
  if(request.method!=="POST"||url.pathname!=="/v1/chat/completions"||!current)throw new Error("HOST_NETWORK");
  const reader=request.clone().body!.getReader(),chunks:Uint8Array[]=[];let length=0;
  try{for(;;){const item=await reader.read();if(item.done)break;length+=item.value.length;if(length>131072)throw new Error("HOST_CAPTURE");chunks.push(item.value);}}catch(e){void reader.cancel();void request.body?.cancel();throw e;}finally{reader.releaseLock();}
  const parsed=JSON.parse(new TextDecoder().decode(Buffer.concat(chunks))),c=current;
  if(parsed.model!=="synthetic-model"||!parsed.messages.some((m:any)=>m.role==="user"&&(m.content==="owned-"+c.caseId||Array.isArray(m.content)&&m.content.some((p:any)=>p.text==="owned-"+c.caseId))))throw new Error("HOST_CORRELATION");
  if(++c.requests>2||c.mode==="cancel"&&c.requests!==1)throw new Error("HOST_REPLAY");
  hashOwnedFixture(parsed.tools.map((t:any)=>({name:t.function.name,schemaHash:fixtureHash({description:t.function.description,inputSchema:t.function.parameters})})),c.offered);
  if(!c.driver)c.driver=createSyntheticInference({caseId:c.caseId,scopeHash,sessionId:c.sessionId,chainId:c.chainId,model:"synthetic-model",answer:"Owned final "+c.mode,calls:[{tool:c.tool,callId:c.callId,input:c.input,result:c.expectedResult??"cancelled",validateInput:v=>fixtureHash(v)===fixtureHash(c.input)}]});
  if(c.requests===2){if(c.actualResultHash!==fixtureHash(c.expectedResult))throw new Error("HOST_RESULT");c.next={runId,scopeHash,sessionId:c.sessionId,chainId:c.chainId,requestKind:"primary",requestId:"request-2",call:structuredClone(c.call),tool:structuredClone(c.tool),resultHash:c.actualResultHash};}
  const response=await c.driver.forRequest({requestId:"request-"+c.requests,requestKind:"primary",scopeHash,sessionId:c.sessionId,chainId:c.chainId,offered:[c.tool]})(request);if(c.requests===2)trace(c,"next_model_request",true);return response;
 },{preconnect(){}});
 const cleanup=await setupVibe(ctx);
 // Setup completion precedes async host calls.
 setTimeout(()=>{void(async()=>{
  try{
   stage="connect";const attempt=await ctx.integration.command.connect({integrationID:"vibe-oidc",methodID:"vibe-browser",label:"synthetic"});
   let connected=false;const end=Date.now()+12000;while(!connected){const status=(await ctx.integration.command.status({integrationID:"vibe-oidc",attemptID:attempt.data.attemptID})).data;if(status.status==="failed"||Date.now()>end)throw new Error("HOST_AUTH");connected=status.status==="complete";if(!connected)await new Promise(r=>setTimeout(r,25));}
   stage="model";while(!JSON.stringify(await ctx.model.list()).includes('"synthetic-model"')){if(Date.now()>end)throw new Error("HOST_MODEL");await new Promise(r=>setTimeout(r,25));}
   stage="discovery";await ctx.mcp.list();await ctx.tool.list();await wait(()=>log().some(e=>e.kind==="discovered"));
   for(const t of await ctx.tool.list())inventory.push({id:t.id,name:t.name,description:t.description,schemaHash:fixtureHash(Schema.isSchema(t.input)?Schema.toJsonSchemaDocument(t.input).schema:t.input)});
   for(const mode of ["success","error","cancel"] as const){
    stage=mode;const q=recipe[mode],session=await ctx.session.create({title:"owned MCP "+mode,model:{providerID:"vibe-oidc",id:"synthetic-model"}});
    const c:any={caseId:"mcp."+mode,runId,scopeHash,mode,sessionId:session.id,chainId:"mcp-chain-"+mode,callId:"mcp-call-"+mode,input:q.input,expectedResult:"result" in q?q.result:null,requests:0,positiveRequired:q.positiveRequired};records.push(c);current=c;
    let final=false;const abort=new AbortController();const observation=(async()=>{for await(const e of ctx.event.subscribe({signal:abort.signal})){if(e.type==="session.execution.succeeded"&&e.data.sessionID===session.id){final=true;c.final=true;trace(c,"final_response",true);return;}if(e.type==="session.execution.failed"&&e.data.sessionID===session.id&&mode!=="cancel"){c.executionFailure="session.execution.failed";return;}}})();
    try{
     await ctx.session.prompt({sessionID:session.id,text:"owned-"+c.caseId});
     if(mode==="cancel"){
      await wait(()=>log().some(e=>e.kind==="called"&&e.mode==="cancel"));await ctx.session.interrupt({sessionID:session.id});c.interrupted=true;await wait(()=>log().some(e=>e.kind==="cancelled"));c.serverCancelled=true;const called=log().find(e=>e.kind==="called"&&e.mode==="cancel"),cancelled=log().find(e=>e.kind==="cancelled");if(called.requestId!==cancelled.requestId||called.inputHash!==fixtureHash(c.input))throw new Error("HOST_CORRELATION");c.serverRequestId=called.requestId;if(c.requests!==1||c.final||c.actualResultHash)throw new Error("HOST_CANCELLED");
     }else{await wait(()=>final||!!c.executionFailure);await observation;if(c.executionFailure)throw new Error("HOST_EXECUTION");if(c.requests!==2||!c.next||!c.call)throw new Error("HOST_RESULT");if(c.recorder){c.proof=c.recorder.finish(c.caseId);c.events=c.recorder.events();if("status" in c.proof)throw new Error("HOST_CORRELATION");}}
    }finally{abort.abort();await observation.catch(()=>{});await ctx.session.interrupt({sessionID:session.id});}
   }
   stage="complete";ready=true;
  }catch(e){failureCode=e instanceof Error&&/^HOST_[A-Z_]+$/.test(e.message)?e.message:"HOST_FAILURE";}
  writeFileSync(String(ctx.options.readiness),JSON.stringify({ready,stage,failureCode,runId,scopeHash,model:"vibe-oidc/synthetic-model",sdk:"1.32.2",inventory,permissions,records:records.map(({driver,recorder,...c})=>c),server:log(),live:"unverified",allToolCompatibility:"unverified"}));
 })();},50);
 return async()=>{for(const d of disposers.reverse())await d.dispose();await cleanup();globalThis.fetch=original;};
}});
