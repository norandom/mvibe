/** Owned material only: native executors and reports must never supply expectations. */
import { randomBytes } from "node:crypto";
import { mkdirSync, writeFileSync, readFileSync, readdirSync, lstatSync, realpathSync } from "node:fs";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { DEFAULT_CASE_MANIFEST, fixtureHash, materializeCase } from "../cases.js";
import type { CaseSpec } from "../contracts.js";

export type WorkspaceQuery = Readonly<{name:"read"|"glob"|"grep"; nonce:string; input:Record<string,unknown>; expected:string; spec:CaseSpec}>;
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
