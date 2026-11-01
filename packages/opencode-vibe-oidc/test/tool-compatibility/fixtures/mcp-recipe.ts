export const MCP_TOOL={name:"owned_nested",description:"Owned nested Unicode baseline fixture",inputSchema:{type:"object" as const,properties:{mode:{type:"string",enum:["success","error","cancel"]},payload:{type:"object",properties:{nonce:{type:"string"},labels:{type:"array",items:{type:"string"}},nested:{type:"object",properties:{enabled:{type:"boolean"}},required:["enabled"],additionalProperties:false}},required:["nonce","labels","nested"],additionalProperties:false}},required:["mode","payload"],additionalProperties:false}};
/** Seeded before host execution; expected serialization follows pinned 2.2.5 MCP
 * adapter (text content; isError becomes tool.execution error with empty content). */
export function mcpRecipe(nonce:string){
 if(!/^[a-zA-Z0-9-]{1,80}$/.test(nonce))throw new Error("HOST_FIXTURE");
 const payload={nonce,labels:["Grüße","東京","🦊"],nested:{enabled:true}};
 const structured={receipt:nonce,payload};
 const message="Owned MCP deliberate failure: "+nonce;
 return {success:{input:{mode:"success",payload},structured,result:JSON.stringify(structured),positiveRequired:true},error:{input:{mode:"error",payload},message,result:JSON.stringify({error:{type:"tool.execution",message},content:[]}),positiveRequired:false},cancel:{input:{mode:"cancel",payload},positiveRequired:false}};
}
