/** Owned material only: native executors and reports must never supply expectations. */
import { randomBytes, createHash } from "node:crypto";
import { mkdirSync, writeFileSync, readFileSync, readdirSync, lstatSync, realpathSync } from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { DEFAULT_CASE_MANIFEST, fixtureHash, materializeCase } from "../cases.js";
import type { CaseSpec } from "../contracts.js";

export type WorkspaceQuery = Readonly<{name:"read"|"glob"|"grep"; nonce:string; input:Record<string,unknown>; expected:string; spec:CaseSpec}>;
export type WorkspaceCommand=Readonly<{name:"shell";kind:"success"|"nonzero"|"denied"|"invalid";nonce:string;directory:string;model:string;input:Record<string,unknown>;stdout:string;stderr:string;exit:number;expected:string;errorMessage?:string;permissionResources:readonly string[];spec:CaseSpec;inspect():string}>;
export function createWorkspaceCommands(root:string,bun:string,nonce=randomBytes(24).toString("hex"),prepared=false) {
 if(!isAbsolute(root)||realpathSync(root)!==resolve(root)||lstatSync(root).isSymbolicLink()||!isAbsolute(bun)||!/^[-A-Za-z0-9_ .:/\\]+$/.test(bun)||!/^[a-f0-9]{48}$/.test(nonce))throw new Error("HOST_FIXTURE");
 const directory=join(root,"commands-"+nonce);if(!prepared)mkdirSync(directory);
 const manifest=DEFAULT_CASE_MANIFEST(),commands:WorkspaceCommand[]=[],permissions:Record<string,string>={"*":"deny"};
 for(const kind of ["success","nonzero","denied","invalid"] as const){
  const path=join(directory,kind+".cjs"),stdout=`stdout_${kind}_${nonce}\n`,stderr=`stderr_${kind}_${nonce}\n`,exit=kind==="nonzero"?7:0;
  // No user data, paths, subprocesses or writes in this program. Ordered writes
  // make the host's merged stream deterministic while exercising both FDs.
  const source=`const fs=require("node:fs");fs.writeSync(1,${JSON.stringify(stdout)});setTimeout(()=>{fs.writeSync(2,${JSON.stringify(stderr)});process.exit(${exit});},80);\n`;
  if(!prepared)writeFileSync(path,source,{flag:"wx",mode:0o600});
  else if(lstatSync(path).isSymbolicLink()||readFileSync(path,"utf8")!==source)throw new Error("HOST_FIXTURE");
  const objective=`& "${bun.replaceAll("\\","/")}" "${path.replaceAll("\\","/")}"`;
  const command=objective+"; exit $LASTEXITCODE";
  // Pinned 2.2.5 DE/bl/y2 keeps the invocation resource, and skips the exit
  // statement's variable expression. Do not allow the whole command as a glob.
  const resource=objective;
  if(kind==="success"||kind==="nonzero")permissions[resource]="allow";
  const input:Record<string,unknown>=kind==="invalid"?{workdir:directory,timeout:2000,background:false}:{command,workdir:directory,timeout:2000,background:false};
  const errorMessage=kind==="denied"?`Unable to execute command: ${command}`:kind==="invalid"?`Invalid arguments for tool "shell":\n- command: Missing key\n\nArguments provided:\n${JSON.stringify(input,null,2)}\n\nUpdate the arguments and call the tool again.`:undefined;
  // Pinned 2.2.5 MR/uh/jhe. Normal exit is explicit in native output/metadata;
  // nonzero exit additionally appears in the model-visible native text.
  // Iv.toResultValue retains multiple text parts; openai-compatible 2.0.88
  // serializes that content array as JSON. Freeze it before execution.
  const expected=kind==="denied"?JSON.stringify({error:{type:"permission.rejected",message:"Permission denied: shell"},content:[]}):kind==="invalid"?JSON.stringify({error:{type:"tool.execution",message:errorMessage},content:[]}):exit?JSON.stringify([{type:"text",text:stdout+stderr},{type:"text",text:`Exited with code ${exit}`} ]):stdout+stderr;
  const id=kind==="success"?"shell.stdout-stderr-exit":kind==="nonzero"?"shell.execution-error":kind==="denied"?"shell.denied":"shell.invalid-input";
  const template=kind==="invalid"?{...manifest.cases.find(c=>c.spec.id==="filesystem.invalid-input")!,objective:id,calls:["shell"],spec:{...manifest.cases.find(c=>c.spec.id==="filesystem.invalid-input")!.spec,id,toolIds:["shell"],fixtureKind:"shell" as const}}:manifest.cases.find(c=>c.spec.id===id)!;
  const state={repository:"unchanged",process:"foreground-completed",nonce};
  const spec=materializeCase(template,{recipeVersion:1,objective:template.objective,observation:state,calls:[{toolId:"shell",nonce,input,observation:expected}]},manifest.limits);
  const inspect=()=>{if(lstatSync(directory).isSymbolicLink()||realpathSync(directory)!==directory||readdirSync(directory).length!==4)throw new Error("HOST_STATE");for(const c of commands){const p=join(directory,c.kind+".cjs");if(lstatSync(p).isSymbolicLink()||!lstatSync(p).isFile())throw new Error("HOST_STATE");}if(readFileSync(path,"utf8")!==source)throw new Error("HOST_STATE");return fixtureHash(state);};
  commands.push(Object.freeze({name:"shell",kind,nonce,directory,model:"synthetic-model",input:Object.freeze(input),stdout,stderr,exit,expected,...(errorMessage?{errorMessage}:{}),permissionResources:Object.freeze(kind==="invalid"?[]:[resource]),spec,inspect}));
 }
 const accepts=(q:WorkspaceCommand,input:unknown)=>commands.includes(q)&&fixtureHash(input)===fixtureHash(q.input);
 return Object.freeze({directory,nonce,commands:Object.freeze(commands),permissions:Object.freeze(permissions),accepts});
}
/** Bounded independent repository inventory, including untouched files and empty
 * directories. A symlink is rejected before any target can be traversed. */
export function inspectOwnedWorkspace(root:string):Readonly<Record<string,string>> {
 if(!isAbsolute(root)||realpathSync(root)!==resolve(root)||lstatSync(root).isSymbolicLink())throw new Error("HOST_STATE");
 const snapshot:Record<string,string>={};let count=0,bytes=0;
 const walk=(directory:string)=>{for(const name of readdirSync(directory).sort()){
  if(++count>256)throw new Error("HOST_STATE");
  const path=join(directory,name),stat=lstatSync(path),key=relative(root,path).replaceAll("\\","/");
  if(stat.isSymbolicLink()||realpathSync(path)!==path)throw new Error("HOST_STATE");
  if(stat.isDirectory()){snapshot["d:"+key]="directory";walk(path);}
  else if(stat.isFile()){bytes+=stat.size;if(bytes>1048576)throw new Error("HOST_STATE");snapshot["f:"+key]=createHash("sha256").update(readFileSync(path)).digest("hex");}
  else throw new Error("HOST_STATE");
 }};walk(root);return Object.freeze(snapshot);
}
export function inspectMutationTransition(root:string,before:Readonly<Record<string,string>>,q:WorkspaceMutation):Readonly<Record<string,string>> {
 const expected={...before};
 for(const change of q.expectedDiff){
  const key="f:"+relative(root,join(q.directory,change.file)).replaceAll("\\","/");
  if(change.after===null)delete expected[key];else expected[key]=createHash("sha256").update(change.after).digest("hex");
 }
 const actual=inspectOwnedWorkspace(root);
 if(fixtureHash(actual)!==fixtureHash(expected))throw new Error("HOST_STATE");
 q.inspect();return actual;
}
export type WorkspaceMutation = Readonly<{name:"write"|"edit"|"patch"; nonce:string; directory:string; model:string; errorMessage?:string; input:Record<string,unknown>; expected:string; spec:CaseSpec; before:Readonly<Record<string,string>>; after:Readonly<Record<string,string>>; permissionResources:readonly string[]; expectedDiff:readonly {file:string;before:string|null;after:string|null}[]; inspect():string; diff():unknown}>;
export function createWorkspaceMutations(root:string) {
  if(!isAbsolute(root)||realpathSync(root)!==resolve(root)||lstatSync(root).isSymbolicLink())throw new Error("HOST_FIXTURE");
  const nonce=randomBytes(24).toString("hex"),directory=join(root,"mutations-"+nonce);
  mkdirSync(directory);
  const manifest=DEFAULT_CASE_MANIFEST();
  const mutations:WorkspaceMutation[]=[];
  for(const name of ["write","edit","patch"] as const){
    const dir=join(directory,name);mkdirSync(dir);
    const before:Record<string,string>={"sentinel.txt":`Untouched ${nonce}\n`};
    if(name==="edit")before["edited.txt"]=`before café 雪 ${nonce}\r\nretained\r\n`;
    if(name==="patch"){before["first.txt"]=`first before ${nonce}\n`;before["second.txt"]=`second before ${nonce}\n`;}
    const after:Record<string,string>={...before};
    if(name==="write")after["created.txt"]=`Created café 雪 ${nonce}\nlast\n`;
    if(name==="edit")after["edited.txt"]=`after café 雪 ${nonce}\r\nretained\r\n`;
    if(name==="patch"){after["first.txt"]=`first after ${nonce}\n`;after["second.txt"]=`second after ${nonce}\n`;}
    for(const [file,bytes] of Object.entries(before))writeFileSync(join(dir,file),bytes,{flag:"wx",mode:0o600});
    const rel=(file:string)=>relative(root,join(dir,file)).replaceAll("\\","/");
    const target=name==="write"?"created.txt":"edited.txt";
    const input:Record<string,unknown>=name==="write"?{path:join(dir,target),content:after[target]}:name==="edit"?{path:join(dir,target),oldString:"before",newString:"after",replaceAll:false}:{patchText:`*** Begin Patch\n*** Update File: ${rel("first.txt")}\n@@\n-first before ${nonce}\n+first after ${nonce}\n*** Update File: ${rel("second.txt")}\n@@\n-second before ${nonce}\n+second after ${nonce}\n*** End Patch`};
    const resources=name==="patch"?[rel("first.txt"),rel("second.txt")]:[rel(target)];
    // Native 2.2.5 jF/edit formatter/pB, constructed solely from fixture inputs.
    const expected=name==="write"?`Created file successfully: ${rel(target)}`:name==="edit"?`Edited ${rel(target)} (1 replacement)`:`Success. Updated the following files:\nM ${rel("first.txt")}\nM ${rel("second.txt")}`;
    const template=manifest.cases.find(c=>c.spec.id==="filesystem."+name)!;
    const spec=materializeCase(template,{recipeVersion:1,objective:template.objective,observation:after,calls:[{toolId:name,nonce,input,observation:expected}]},manifest.limits);
    const expectedDiff=Object.keys(after).filter(file=>after[file]!==before[file]).map(file=>Object.freeze({file,before:before[file]??null,after:after[file]??null}));
    const inspect=()=>{
      if(lstatSync(directory).isSymbolicLink()||realpathSync(directory)!==directory||lstatSync(dir).isSymbolicLink()||realpathSync(dir)!==dir)throw new Error("HOST_STATE");
      if(JSON.stringify(readdirSync(dir).sort())!==JSON.stringify(Object.keys(after).sort()))throw new Error("HOST_STATE");
      for(const [file,bytes] of Object.entries(after)){
        const path=join(dir,file);
        if(!lstatSync(path).isFile()||lstatSync(path).isSymbolicLink()||!readFileSync(path).equals(Buffer.from(bytes)))throw new Error("HOST_STATE");
      }
      return fixtureHash(after);
    };
    const diff=()=>{inspect();return expectedDiff.map(d=>({...d,after:readFileSync(join(dir,d.file),"utf8")}));};
    mutations.push(Object.freeze({name,nonce,directory:dir,model:name==="patch"?"fixture-gpt-mutation":"synthetic-model",input:Object.freeze(input),expected,spec,before:Object.freeze(before),after:Object.freeze(after),permissionResources:Object.freeze(resources),expectedDiff:Object.freeze(expectedDiff),inspect,diff}));
  }
  const dir=join(directory,"invalid");mkdirSync(dir);
  const before=Object.freeze({"untouched.txt":`before ${nonce}\n`});
  writeFileSync(join(dir,"untouched.txt"),before["untouched.txt"],{flag:"wx",mode:0o600});
  const input=Object.freeze({path:join(dir,"untouched.txt"),oldString:"before"});
  // Native wx/_x argument error formatter. Missing required newString is never
  // repaired to another operation; expected text exists before host execution.
  const errorMessage=`Invalid arguments for tool "edit":\n- newString: Missing key\n\nArguments provided:\n${JSON.stringify(input,null,2)}\n\nUpdate the arguments and call the tool again.`;
  const expected=JSON.stringify({error:{type:"tool.execution",message:errorMessage},content:[]});
  const template=manifest.cases.find(c=>c.spec.id==="filesystem.invalid-input")!;
  const spec=materializeCase(template,{recipeVersion:1,objective:template.objective,observation:before,calls:[{toolId:"edit",nonce,input,observation:expected}]},manifest.limits);
  const inspect=()=>{
    if(lstatSync(directory).isSymbolicLink()||realpathSync(directory)!==directory||lstatSync(dir).isSymbolicLink()||realpathSync(dir)!==dir||JSON.stringify(readdirSync(dir))!==JSON.stringify(["untouched.txt"]))throw new Error("HOST_STATE");
    const path=join(dir,"untouched.txt");if(!lstatSync(path).isFile()||lstatSync(path).isSymbolicLink()||!readFileSync(path).equals(Buffer.from(before["untouched.txt"])))throw new Error("HOST_STATE");
    return fixtureHash(before);
  };
  const invalid:WorkspaceMutation=Object.freeze({name:"edit",nonce,directory:dir,model:"synthetic-model",errorMessage,input,expected,spec,before,after:before,permissionResources:Object.freeze([]),expectedDiff:Object.freeze([]),inspect,diff:()=>{inspect();return [];}});
  return Object.freeze({directory,mutations:Object.freeze(mutations),invalid});
}
export function createWorkspaceQueries(root:string) {
  if(!isAbsolute(root)||realpathSync(root)!==resolve(root)||lstatSync(root).isSymbolicLink())throw new Error("HOST_FIXTURE");
  const nonce=randomBytes(24).toString("hex"), directory=join(root,"queries-"+nonce), target=join(directory,"owned.match.txt");
  mkdirSync(directory);
  const needle="needle_"+nonce;
  const files:Record<string,string>={"owned.match.txt":`Owned café 雪\n${needle}\nlast line\n`,"excluded.skip.txt":`${needle}\n`,"unrelated.txt":"no matching observation\n"};
  for(const [name,content] of Object.entries(files))writeFileSync(join(directory,name),content,{flag:"wx",mode:0o600});
  // Reference 2.2.5 executable: fR (ReadTool), IB (GlobTool), CB (GrepTool).
  // Grep forwards ripgrep lines.text verbatim, including its trailing LF.
  // These formatters are built BEFORE execution from trusted bytes and paths.
  const recipes=[
    {name:"read" as const,input:{path:target},expected:`Read file ${target}, lines 1-3\n1: Owned café 雪\n2: ${needle}\n3: last line`},
    {name:"glob" as const,input:{path:directory,pattern:"**/*.match.txt"},expected:target},
    {name:"grep" as const,input:{path:directory,pattern:needle,include:"*.match.txt",literal:true},expected:`Found 1 matches\n${target}:\n  Line 2: ${needle}\n`},
  ];
  const manifest=DEFAULT_CASE_MANIFEST();
  const queries:readonly WorkspaceQuery[]=recipes.map(r=>{
    const template=manifest.cases.find(c=>c.spec.id==="filesystem."+r.name)!;
    const spec=materializeCase(template,{recipeVersion:1,objective:template.objective,observation:files,calls:[{toolId:r.name,nonce,input:r.input,observation:r.expected}]},manifest.limits);
    return Object.freeze({...r,nonce,spec,input:Object.freeze(r.input)});
  });
  const inspect=()=>{
    const part=relative(root,directory);
    if(part===""||part.startsWith(".."+sep)||isAbsolute(part)||lstatSync(directory).isSymbolicLink()||realpathSync(directory)!==directory)throw new Error("HOST_STATE");
    const names=readdirSync(directory).sort();
    if(JSON.stringify(names)!==JSON.stringify(Object.keys(files).sort()))throw new Error("HOST_STATE");
    for(const [name,expected] of Object.entries(files)){
      const path=join(directory,name);
      if(!lstatSync(path).isFile()||lstatSync(path).isSymbolicLink()||readFileSync(path,"utf8")!==expected)throw new Error("HOST_STATE");
    }
    return fixtureHash(files);
  };
  return Object.freeze({directory,queries:Object.freeze(queries),inspect});
}
