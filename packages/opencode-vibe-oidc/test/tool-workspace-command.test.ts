import { test, expect } from "bun:test";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { state } from "./preload.js";
import { createWorkspaceCommands, inspectOwnedWorkspace } from "./tool-compatibility/fixtures/workspace.js";
import { fixtureHash } from "./tool-compatibility/cases.js";
import { offlineEnvironment } from "./tool-compatibility/runner.js";

test("isolated native commands retain deterministic executable resolution and PowerShell exit propagation",()=>{
 const env=offlineEnvironment(state.root,{PATH:"owned-path",PATHEXT:".COM;.EXE;.BAT",SECRET:"private"});
 expect(env.PATHEXT).toBe(".EXE");
 expect(env).not.toHaveProperty("SECRET");
 const f=createWorkspaceCommands(state.root,process.execPath);
 for(const q of f.commands.filter(q=>q.kind!=="invalid")){
  expect(q.input.command).toEndWith("; exit $LASTEXITCODE");
  expect(q.permissionResources).toEqual([String(q.input.command).slice(0,-"; exit $LASTEXITCODE".length)]);
 }
 expect(f.permissions["*"]).toBe("deny");
 const nonzero=f.commands[1]!;
 expect(JSON.parse(nonzero.expected)).toEqual([{type:"text",text:nonzero.stdout+nonzero.stderr},{type:"text",text:"Exited with code 7"}]);
});

test("command recipes freeze trusted stdout stderr and exit expectations before native execution",()=>{
 const f=createWorkspaceCommands(state.root,process.execPath);
 expect(f.commands.map(q=>q.spec.id)).toEqual(["shell.stdout-stderr-exit","shell.execution-error","shell.denied","shell.invalid-input"]);
 for(const q of f.commands){
  expect(q.name).toBe("shell");expect(q.spec.expectedCalls[0]!.inputHash).toBe(fixtureHash(q.input));
  expect(q.inspect()).toBe(q.spec.expectedObservationHash);
  expect(q.spec.positiveRequired).toBe(q.kind==="success");
 }
 const [ok,failed,denied,invalid]=f.commands;
 expect(ok!.stdout).toContain(ok!.nonce);expect(ok!.stderr).toContain(ok!.nonce);
 expect(ok!.exit).toBe(0);expect(failed!.exit).toBe(7);
 expect(failed!.expected).toContain("Exited with code 7");
 expect(denied!.expected).toContain("permission.rejected");expect(invalid!.input).not.toHaveProperty("command");
 expect(f.commands.filter(q=>q.kind==="success"||q.kind==="nonzero").every(q=>f.permissions[q.permissionResources[0]!] === "allow")).toBe(true);
 expect(f.permissions[denied!.permissionResources[0]!]).toBeUndefined();
 const before=inspectOwnedWorkspace(state.root);writeFileSync(join(f.directory,"unexpected"),"side effect");
 expect(()=>f.commands[0]!.inspect()).toThrow("HOST_STATE");expect(fixtureHash(before)).not.toBe(fixtureHash(inspectOwnedWorkspace(state.root)));
});
test("command guard rejects arbitrary commands outside paths altered bounds and shell metacharacters",()=>{
 const f=createWorkspaceCommands(state.root,process.execPath),q=f.commands[0]!;
 expect(f.accepts(q,q.input)).toBe(true);
 for(const input of [{...q.input,command:"echo arbitrary"},{...q.input,workdir:"C:/"},{...q.input,background:true},{...q.input,timeout:0},{...q.input,command:q.input.command+"; echo bypass"},{...q.input,extra:true}])expect(f.accepts(q,input)).toBe(false);
 expect(()=>createWorkspaceCommands("relative",process.execPath)).toThrow("HOST_FIXTURE");
 expect(()=>createWorkspaceCommands(state.root,'C:/bad";evil/bun.exe')).toThrow("HOST_FIXTURE");
});
