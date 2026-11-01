import { expect, test } from "bun:test";
import { compareContracts, normalizeDeclaration, loadPreparedHttpApi, contractSurface } from "./tool-compatibility/update-contracts.js";
import { mkdtempSync,mkdirSync,writeFileSync,rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { baselineStatus } from "./tool-compatibility/baseline.js";
import { requireReferenceHostVersion } from "./tool-compatibility/preparation.js";

test("removed HTTP operation and changed plugin hook produce located drift", () => {
 const before={http:{paths:{"/session":{get:{operationId:"session.list"}}}},plugin:{session:normalizeDeclaration("export interface Hook { before(): void }")}};
 const after={http:{paths:{"/session":{}}},plugin:{session:normalizeDeclaration("/** docs */ export interface Hook { after(): void }")}};
 const frozen=JSON.stringify(before), diff=compareContracts(before,after);
 expect(diff.some(d=>d.kind==="removed"&&d.path.includes("get"))).toBe(true);
 expect(diff.some(d=>d.kind==="changed"&&d.path.includes("session"))).toBe(true);
 expect(JSON.stringify(before)).toBe(frozen);
 expect(normalizeDeclaration("// comment\nexport interface Hook{before():void}")).toBe(normalizeDeclaration("export interface Hook { before(): void }"));
});
test("explicit candidate host must match exact prepared stable 2.x version",()=>{
 expect(requireReferenceHostVersion("v2.3.1","2.3.1")).toBe("2.3.1");
 expect(()=>requireReferenceHostVersion("v2.3.2","2.3.1")).toThrow();
 expect(()=>requireReferenceHostVersion("v2.3.1")).toThrow();
});
test("partial or failed baseline never passes",()=>{
 expect(baselineStatus([{name:"query",status:"passed"}])).toBe("failed");
 expect(baselineStatus(["query","mutation","command","mcp"].map(name=>({name,status:name==="mcp"?"failed":"passed"})))).toBe("failed");
});
test("HTTP capture binds both protocol and Effect to selected candidate tree",async()=>{
 const root=mkdtempSync(join(tmpdir(),"vibe-contract-candidate-"));
 try{
  for(const [name,entry,source] of [["@opencode/protocol","client","export const ClientApi={candidateOnly:true};"],["effect","http-api","export const OpenApi={fromApi:api=>({candidateEffect:api.candidateOnly})};"]]){
   const directory=join(root,"node_modules",name!);mkdirSync(directory,{recursive:true});
   writeFileSync(join(directory,"package.json"),JSON.stringify({name,type:"module",exports:{["./"+entry]:"./entry.js"}}));
   writeFileSync(join(directory,"entry.js"),source!);
  }
  const captured=await loadPreparedHttpApi(root);
  expect(captured.http).toEqual({candidateEffect:true});
  expect(captured.protocolModule).toContain("vibe-contract-candidate-");
  expect(captured.effectModule).toContain("vibe-contract-candidate-");
 }finally{rmSync(root,{recursive:true,force:true});}
});
test("empty or malformed snapshots cannot compare as compatible",()=>{
 for(const value of [{},{http:{},plugin:{}},{http:{openapi:"3.1.0",paths:[]},plugin:{}}])expect(()=>contractSurface(value)).toThrow("Invalid contract snapshot");
 const before=JSON.parse('{"constructor":{"operationId":"a"},"toString":{"operationId":"b"}}');
 expect(compareContracts(before,{})).toEqual([{kind:"removed",path:"$/constructor"},{kind:"removed",path:"$/toString"}]);
});
