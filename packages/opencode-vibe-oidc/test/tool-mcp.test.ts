import { test, expect } from "bun:test";
import { mcpRecipe, MCP_TOOL } from "./tool-compatibility/fixtures/mcp-recipe.js";
test("MCP trusted recipes bind nested Unicode input and preserve negative outcomes",()=>{
 const recipe=mcpRecipe("owned-nonce");
 expect(MCP_TOOL.inputSchema.required).toEqual(["mode","payload"]);
 expect(recipe.success.input.payload).toEqual({nonce:"owned-nonce",labels:["Grüße","東京","🦊"],nested:{enabled:true}});
 expect(JSON.parse(recipe.success.result)).toEqual(recipe.success.structured);
 expect(recipe.error.result).toBe(JSON.stringify({error:{type:"tool.execution",message:"Owned MCP deliberate failure: owned-nonce"},content:[]}));
 expect(recipe.cancel.positiveRequired).toBe(false);
 expect(()=>mcpRecipe("../private")).toThrow("HOST_FIXTURE");
});
import { parseMcpProbe } from "./tool-compatibility/mcp-report.js";
test("MCP artifact checks fail closed for absent or incomplete host evidence",()=>{
 for(const report of [null,{}, {ready:true}, {ready:false,records:[]}])expect(()=>parseMcpProbe(report)).toThrow("HOST_MCP_EVIDENCE");
});
