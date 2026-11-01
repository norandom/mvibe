/** Focused MCP family artifact checks using existing recorder and hash helpers. */
import { fixtureHash,DEFAULT_CASE_MANIFEST,materializeCase } from "./cases.js";
import { createRoundTripRecorder } from "./observe.js";
import { parseToolIdentity } from "./contracts.js";
import { MCP_TOOL,mcpRecipe } from "./fixtures/mcp-recipe.js";
export function parseMcpProbe(value:unknown):any{
 const r=value as any,fail=():never=>{throw new Error("HOST_MCP_EVIDENCE");};
 try{
  if(!r||r.ready!==true||r.stage!=="complete"||r.failureCode!=="none"||r.runId!=="mcp-baseline"||r.sdk!=="1.32.2"||r.model!=="vibe-oidc/synthetic-model"||r.live!=="unverified"||r.allToolCompatibility!=="unverified"||r.records?.length!==3||r.server?.length!==10||r.permissions?.length!==3)fail();
  const nonce=r.records[0].input.payload.nonce,recipe=mcpRecipe(nonce),scopeHash=fixtureHash({recipe,server:"owned",host:"2.2.5"});
  if(r.scopeHash!==scopeHash||r.processCleanup?.exited!==true||r.processCleanup?.withinBudget!==true||r.processCleanup.pids.length!==1)fail();
  if(new Set(r.records.map((c:any)=>c.sessionId)).size!==3||new Set(r.records.map((c:any)=>c.call?.hostMessageId)).size!==3)fail();
  const kinds=["initialize","negotiated","initialized","discovered","called","returned","called","returned","called","cancelled"];
  r.server.forEach((e:any,i:number)=>{if(e.kind!==kinds[i]||e.sequence!==i+1||e.pid!==r.processCleanup.pids[0])fail();});
  if(r.server[0].params.clientInfo.name!=="opencode"||r.server[0].params.clientInfo.version!=="2.2.5"||r.server[1].result.protocolVersion!==r.server[0].params.protocolVersion||fixtureHash(r.server[1].result.capabilities)!==fixtureHash({tools:{}})||r.server[3].schemaHash!==fixtureHash(MCP_TOOL))fail();
  for(const [index,mode] of ["success","error","cancel"].entries()){
   const c=r.records[index],q=recipe[mode as keyof typeof recipe],tool=parseToolIdentity(c.tool),called=r.server[4+index*2],outcome=r.server[5+index*2];
   if(c.mode!==mode||c.caseId!=="mcp."+mode||c.runId!==r.runId||c.scopeHash!==scopeHash||c.chainId!=="mcp-chain-"+mode||c.callId!=="mcp-call-"+mode||c.call.hostCallId!==c.callId||c.call.upstreamCallId!==c.callId||c.call.link!=="host_exposed"||fixtureHash(c.input)!==fixtureHash(q.input)||c.call.inputHash!==fixtureHash(q.input)||c.positiveRequired!==q.positiveRequired)fail();
   const registered=r.inventory.filter((t:any)=>t.id===tool.registryId&&t.name===MCP_TOOL.name&&t.description===MCP_TOOL.description);
   const offered=c.definitions.filter((t:any)=>t.name===tool.offeredName);
   if(registered.length!==1||registered[0].schemaHash!==fixtureHash(MCP_TOOL.inputSchema)||offered.length!==1||fixtureHash(offered[0].inputSchema)!==fixtureHash(MCP_TOOL.inputSchema)||tool.registryId!==tool.offeredName||tool.declaredName!==MCP_TOOL.name||tool.origin!=="mcp"||tool.originHash!==fixtureHash({server:"owned",schemaHash:fixtureHash(MCP_TOOL)})||tool.schemaHash!==fixtureHash({description:offered[0].description,inputSchema:offered[0].inputSchema})||fixtureHash(c.offered)!==fixtureHash(c.definitions.map((t:any)=>({name:t.name,schemaHash:fixtureHash({description:t.description,inputSchema:t.inputSchema})}))))fail();
   if(c.primaryRequests.length!==(mode==="cancel"?1:2)||c.primaryRequests.some((p:any)=>p.runId!==r.runId||p.scopeHash!==scopeHash||p.sessionId!==c.sessionId||p.chainId!==c.chainId||p.requestKind!=="primary"||p.model.providerID!=="vibe-oidc"||p.model.id!=="synthetic-model"))fail();
   if(called.mode!==mode||called.inputHash!==fixtureHash(q.input)||outcome.mode!==mode||called.requestId!==outcome.requestId||c.serverRequestId!==called.requestId||r.permissions.filter((p:any)=>p.caseId===c.caseId&&p.effect==="allow").length!==1)fail();
   if(mode==="cancel"){
    if(c.requests!==1||c.interrupted!==true||c.serverCancelled!==true||c.final||c.next||c.proof||c.actualResultHash||outcome.kind!=="cancelled")fail();
   }else{
    const expected=mode==="success"?recipe.success.result:recipe.error.result;
    const serverResult=mode==="success"?{structuredContent:recipe.success.structured,content:[{type:"text",text:recipe.success.result}]}:{isError:true,content:[{type:"text",text:recipe.error.message}]};
    if(c.requests!==2||c.final!==true||c.hostStatus!==(mode==="success"?"completed":"error")||c.expectedResult!==expected||c.actualResultHash!==fixtureHash(expected)||outcome.isError!==(mode==="error")||outcome.resultHash!==fixtureHash(serverResult))fail();
    const next=c.next;if(next.runId!==r.runId||next.scopeHash!==scopeHash||next.sessionId!==c.sessionId||next.chainId!==c.chainId||next.requestKind!=="primary"||next.requestId!=="request-2"||next.resultHash!==c.actualResultHash||fixtureHash(next.call)!==fixtureHash(c.call)||fixtureHash(next.tool)!==fixtureHash(tool))fail();
    if(mode==="success"){
     if(c.actualStateHash!==fixtureHash(recipe.success.structured))fail();
     const manifest=DEFAULT_CASE_MANIFEST(),template=manifest.cases.find(c=>c.spec.id==="mcp.echo")!;
     const spec=materializeCase({...template,calls:[tool.registryId],spec:{...template.spec,id:c.caseId,toolIds:[tool.registryId]}},{recipeVersion:1,objective:template.objective,observation:recipe.success.structured,calls:[{toolId:tool.registryId,nonce,input:q.input,observation:expected}]},manifest.limits);
     if(c.events.some((e:any)=>e.runId!==r.runId||e.caseId!==c.caseId||e.scopeHash!==scopeHash||e.sessionId!==c.sessionId||e.chainId!==c.chainId||e.requestKind!=="primary"||e.kind==="state_observed"&&e.observationHash!==c.actualStateHash))fail();
     const recorder=createRoundTripRecorder({runId:r.runId,cases:[spec],limits:manifest.limits,cancelOwnedTurn(){}});for(const event of c.events)recorder.record(event);const proof=recorder.finish(c.caseId);if("status" in proof||fixtureHash(proof)!==fixtureHash(c.proof))fail();
    }else if(c.proof||c.events)fail();
   }
  }
  return r;
 }catch{return fail();}
}
/** Mutations are applied only to a fresh actual-run artifact in the explicit probe. */
export function rejectCrossedMcpEvidence(value:unknown):number{
 parseMcpProbe(value);const changes:((r:any)=>void)[]=[r=>r.scopeHash="b".repeat(64),r=>r.server[5].resultHash="b".repeat(64),r=>r.server[7].isError=false,r=>r.server[9].requestId=999,r=>r.processCleanup.exited=false,r=>r.records[0].actualStateHash="b".repeat(64),r=>r.records[1].positiveRequired=true,r=>r.records[2].next=r.records[0].next,r=>r.records[0].definitions[0].inputSchema={},r=>r.records[1].next.call=r.records[0].call];
 for(let index=0;index<3;index++)for(const field of ["runId","scopeHash","sessionId","chainId","requestKind"])changes.push(r=>r.records[index].primaryRequests[0][field]="crossed");
 for(const event of (value as any).records[0].events)for(const field of ["runId","scopeHash","sessionId","chainId","requestKind"])changes.push(r=>r.records[0].events.find((e:any)=>e.sequence===event.sequence)[field]="crossed");
 for(const [mutationIndex,change] of changes.entries()){const r=structuredClone(value);change(r);let rejected=false;try{parseMcpProbe(r);}catch{rejected=true;}if(!rejected)throw new Error("HOST_CROSSED_EVIDENCE_"+mutationIndex);}return changes.length;
}
