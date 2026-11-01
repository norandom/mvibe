/** One explicit prepared offline run; host probes are sequential and reuse their parsers. */
import { readFileSync,writeFileSync,mkdirSync } from "node:fs";
import { resolve,join } from "node:path";
export function baselineStatus(rows:{name:string;status:string}[]):"passed"|"failed"{
 const names=["query","mutation","command","mcp"];
 return rows.length===4&&names.every(name=>rows.filter(row=>row.name===name&&row.status==="passed").length===1)?"passed":"failed";
}
if(import.meta.main){
 const args=process.argv.slice(2), option=(key:string)=>{const index=args.indexOf(key);return index<0?undefined:args[index+1];};
 const provider=resolve(option("--provider-root")??resolve(import.meta.dir,"../.."));
 const project=resolve(option("--project-root")??process.env.VIBE_TEST_PROJECT_ROOT??resolve(import.meta.dir,"../../../.."));
 const expected=option("--expected-version")??"2.2.5";
 const directory=resolve(option("--output")??join(project,"reports/tool-baseline",new Date().toISOString().replaceAll(/[:.]/g,"-")));
 mkdirSync(directory,{recursive:true});
 const env={...process.env,VIBE_EXPECTED_HOST_VERSION:expected,VIBE_TEST_PROJECT_ROOT:project,OPENCODE_DISABLE_AUTOUPDATE:"true",UV_OFFLINE:"true"};
 const execute=(name:string,command:string[])=>{
  const result=Bun.spawnSync([process.execPath,"--no-install",...command],{cwd:provider,env,timeout:180000,maxBuffer:16*1024*1024});
  writeFileSync(join(directory,name+".log"),result.stdout.toString()+result.stderr.toString());
  return {name,status:result.exitCode===0?"passed":"failed",exitCode:result.exitCode,artifact:join(directory,name+".log")};
 };
 const compilation=execute("compile",["run","typecheck"]);
 const capture=execute("contract-capture",["run","test/tool-compatibility/update-contracts.ts","capture",join(directory,"candidate-contracts.json")]);
 const reference=resolve(import.meta.dir,"contracts/opencode-2.2.5.json");
 const contracts=capture.status==="passed"?execute("contract-compare",["run","test/tool-compatibility/update-contracts.ts","compare",join(directory,"candidate-contracts.json"),reference]):{name:"contract-compare",status:"unverified",exitCode:null,artifact:capture.artifact};
 const probes=[];
 for(const [name,file] of [["query","workspace-query-probe"],["mutation","workspace-mutation-probe"],["command","workspace-command-probe"],["mcp","mcp-probe"]]){
  const outcome=execute(name!,["run",`test/tool-compatibility/${file}.ts`]);
  let evidence:any;try{evidence=JSON.parse(readFileSync(outcome.artifact,"utf8").trim());}catch{}
  probes.push({...outcome,status:outcome.status==="passed"&&evidence?.ready===true&&evidence?.cleanup==="verified"&&evidence?.hostVersion===expected?"passed":"failed",hostVersion:evidence?.hostVersion,hashes:evidence?.hashes,model:evidence?.model});
 }
 const status=baselineStatus(probes)==="passed"&&[compilation,capture,contracts].every(r=>r.status==="passed")?"passed":"failed";
 let metadata;try{metadata=JSON.parse(readFileSync(join(directory,"candidate-contracts.json"),"utf8")).metadata;}catch{}
 const result={kind:"offline-tool-baseline",status,live:"unverified",mode:"offline",expected,platform:process.platform,arch:process.arch,metadata,compilation,contracts,probes,limitations:["Synthetic fixture models only; conditional patch uses its recorded profile","MCP codemode=false; owned official-SDK stdio fixture only","Other tools, platforms, live accounts and future updates unverified"]};
 writeFileSync(join(directory,"baseline.json"),JSON.stringify(result,null,2)+"\n");
 console.log(`Offline tool baseline: ${status}; live: unverified; report: ${join(directory,"baseline.json")}`);
 process.exitCode=status==="passed"?0:1;
}
