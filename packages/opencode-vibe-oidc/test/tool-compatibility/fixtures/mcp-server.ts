/** Official SDK stdio server. Stdout contains MCP protocol only. */
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema,ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { appendFileSync, statSync } from "node:fs";
import { fixtureHash } from "../cases.js";
import { MCP_TOOL,mcpRecipe } from "./mcp-recipe.js";
if(import.meta.main){
 const [log,nonce]=process.argv.slice(2);if(!log||!nonce)throw new Error("HOST_FIXTURE");
 const recipe=mcpRecipe(nonce);let sequence=0;
 const record=(kind:string,extra:Record<string,unknown>={})=>{if(sequence>=24||statSync(log).size>32768)throw new Error("HOST_CAPTURE");appendFileSync(log,JSON.stringify({sequence:++sequence,kind,pid:process.pid,...extra})+"\n");};
 const server=new Server({name:"owned-baseline",version:"1.0.0"},{capabilities:{tools:{}}});
 const transport=new StdioServerTransport();
 server.oninitialized=()=>record("initialized",{client:server.getClientVersion(),capabilities:server.getClientCapabilities()});
 server.setRequestHandler(ListToolsRequestSchema,async()=>{record("discovered",{schemaHash:fixtureHash(MCP_TOOL)});return {tools:[MCP_TOOL]};});
 server.setRequestHandler(CallToolRequestSchema,async(request,extra)=>{
  const mode=request.params.arguments?.mode;
  if(request.params.name!==MCP_TOOL.name||!Object.hasOwn(recipe,String(mode)))throw new Error("HOST_FIXTURE");
  const expected=recipe[mode as keyof typeof recipe];
  if(fixtureHash(request.params.arguments)!==fixtureHash(expected.input))throw new Error("HOST_FIXTURE");
  record("called",{requestId:extra.requestId,mode,inputHash:fixtureHash(expected.input)});
  if(mode==="cancel")return await new Promise<never>((_,reject)=>{const stop=()=>{record("cancelled",{requestId:extra.requestId,mode});reject(new Error("Owned cancelled"));};extra.signal.addEventListener("abort",stop,{once:true});if(extra.signal.aborted)stop();});
  const result=mode==="error"?{isError:true,content:[{type:"text" as const,text:recipe.error.message}]}:{structuredContent:recipe.success.structured,content:[{type:"text" as const,text:recipe.success.result}]};
  record("returned",{requestId:extra.requestId,mode,resultHash:fixtureHash(result),isError:mode==="error"});return result;
 });
 // Observe negotiated initialize on real transport without replacing protocol handling.
 const send=transport.send.bind(transport);transport.send=async message=>{if("result" in message&&message.result&&"protocolVersion" in message.result)record("negotiated",{result:message.result});await send(message);};
 await server.connect(transport);
 const onmessage=transport.onmessage!;transport.onmessage=(message)=>{if("method" in message&&message.method==="initialize")record("initialize",{params:message.params});onmessage(message);};
 process.stdin.on("end",()=>{record("closed");void server.close();});
}
