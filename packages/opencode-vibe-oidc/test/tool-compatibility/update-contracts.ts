/** Offline prepared SDK surfaces. Capture never overwrites the reviewed reference. */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";
import ts from "typescript";

export function normalizeDeclaration(source:string):string {
 const file=ts.createSourceFile("surface.d.ts",source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
 return ts.createPrinter({removeComments:true,newLine:ts.NewLineKind.LineFeed}).printFile(file).trim();
}
/** Resolve both coupled SDK modules from the selected prepared dependency tree. */
export async function loadPreparedHttpApi(root:string){
 const protocolModule=pathToFileURL(Bun.resolveSync("@opencode/protocol/client",root)).href;
 const effectModule=pathToFileURL(Bun.resolveSync("effect/http-api",root)).href;
 const [{ClientApi},{OpenApi}]=await Promise.all([import(protocolModule),import(effectModule)]);
 return {http:OpenApi.fromApi(ClientApi),protocolModule,effectModule};
}
type Difference={kind:"added"|"removed"|"changed";path:string};
export function contractSurface(value:unknown):{http:Record<string,unknown>;plugin:Record<string,string>}{
 const object=(v:unknown):v is Record<string,any>=>v!==null&&typeof v==="object"&&!Array.isArray(v);
 if(!object(value)||!object(value.metadata)||!/^2\.\d+\.\d+$/.test(value.metadata.expected??"")||
    !object(value.http)||typeof value.http.openapi!=="string"||!object(value.http.paths)||!object(value.plugin)||
    !["plugin","provider","integration","aisdk","model","session","tool","mcp","permission","shell"].every(name=>Object.hasOwn(value.plugin,name)&&typeof value.plugin[name]==="string"))
   throw new Error("Invalid contract snapshot; capture the prepared SDK before comparing");
 return {http:value.http,plugin:value.plugin};
}
export function compareContracts(before:unknown,after:unknown,path="$",result:Difference[]=[]):Difference[]{
 if(JSON.stringify(before)===JSON.stringify(after))return result;
 if(before&&after&&typeof before==="object"&&typeof after==="object"&&!Array.isArray(before)&&!Array.isArray(after)){
  const a=before as Record<string,unknown>,b=after as Record<string,unknown>;
  for(const key of [...new Set([...Object.keys(a),...Object.keys(b)])].sort()){
   const location=path+"/"+key.replaceAll("~","~0").replaceAll("/","~1");
   if(!Object.hasOwn(a,key))result.push({kind:"added",path:location});
   else if(!Object.hasOwn(b,key))result.push({kind:"removed",path:location});
   else compareContracts(a[key],b[key],location,result);
  }
 }else result.push({kind:"changed",path});
 return result;
}
export async function captureContracts(host:string,expected="2.2.5"){
 const root=resolve(import.meta.dir,"../..");
 const {requireReferenceHostVersion,checkPreparedDependencies}=await import("./preparation.js");
 checkPreparedDependencies(path=>readFileSync(join(root,path),"utf8"),Bun.version);
 const version=Bun.spawnSync([host,"--version"],{env:{...process.env,OPENCODE_DISABLE_AUTOUPDATE:"true"},timeout:30000});
 if(version.exitCode!==0)throw new Error("Host version check failed");
 requireReferenceHostVersion(version.stdout.toString(),expected);
 const packageVersion=(name:string)=>JSON.parse(readFileSync(join(root,"node_modules",name,"package.json"),"utf8")).version as string;
 const versions=Object.fromEntries(["@opencode/plugin","@opencode/cli","@opencode/protocol","@opencode/client"].map(name=>[name,packageVersion(name)]));
 if(Object.values(versions).some(version=>version!==expected))throw new Error("Prepared SDK/protocol/client/CLI versions must exactly match expected host version");
 const {http,protocolModule,effectModule}=await loadPreparedHttpApi(root);
 const plugin=Object.fromEntries(["plugin","provider","integration","aisdk","model","session","tool","mcp","permission","shell"].map(name=>[name,normalizeDeclaration(readFileSync(join(root,"node_modules/@opencode/plugin/dist/promise",name+".d.ts"),"utf8"))]));
 return {metadata:{expected,versions,providerRoot:root,protocolModule,effectModule,hostSha256:createHash("sha256").update(readFileSync(host)).digest("hex"),platform:process.platform,arch:process.arch,source:"prepared @opencode/protocol ClientApi; not docs-site latest or running-server proof"},http,plugin};
}
if(import.meta.main){try{
 const [action,output,reference]=process.argv.slice(2);
 if(action!=="capture"&&action!=="compare")throw new Error("Choose capture <new-output> or compare <candidate-snapshot> <reviewed-reference>");
 if(!output)throw new Error("Output required");
 if(action==="capture"){
  if(existsSync(output))throw new Error("Capture destination exists; reviewed references are never overwritten");
  if(!process.env.OPENCODE_TEST_BINARY)throw new Error("Explicit prepared host required");
  const capture=await captureContracts(process.env.OPENCODE_TEST_BINARY,process.env.VIBE_EXPECTED_HOST_VERSION??"2.2.5");
  writeFileSync(output,JSON.stringify(capture,null,2)+"\n",{flag:"wx"});
  console.log(`Captured prepared contracts: ${output}`);
 }else{
  if(!reference)throw new Error("Reviewed reference required");
  const a=JSON.parse(readFileSync(reference,"utf8")),b=JSON.parse(readFileSync(output,"utf8"));
  const diff=compareContracts(contractSurface(a),contractSurface(b));
  for(const d of diff)console.log(`${d.kind}: ${d.path}`);
  console.log(`Structural differences: ${diff.length}; schema equality does not prove behavioral compatibility; changes are not automatically breaking`);
  if(diff.length)process.exitCode=1;
 }
}catch(error){console.error(error instanceof Error?error.message:"Contract check failed");process.exitCode=1;}}
