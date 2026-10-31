import { afterEach, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, writeFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { installConfig, selectConfigPath, runInstaller } from "../scripts/install.js";
import type { InstallerRuntime } from "../scripts/install.js";
const dirs: string[] = [];
function fixture() { const dir = mkdtempSync(join(tmpdir(), "vibe installer spaces ")); dirs.push(dir); return dir; }
afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }); });
const options = { packagePath: "D:/checkout with spaces/packages/opencode-vibe-oidc", projectPath: "D:/checkout with spaces", uvPath: "C:/tools with spaces/uv.exe" };
test("preserves comments, legacy plugins, unrelated data and backup; reinstall is inert", async () => {
 const dir=fixture(), file=join(dir,"opencode.jsonc");
 const before='{\n // personal comment\n "plugin": ["legacy"],\n "theme": "private-value",\n "plugins": [{"package":"other", "options":{"x":1}}],\n}\n';
 writeFileSync(file,before); const first=await installConfig(file,options);
 expect(first.changed).toBe(true); expect(readFileSync(first.backup!,"utf8")).toBe(before);
 const content=readFileSync(file,"utf8"); expect(content).toContain("// personal comment"); expect(content).toContain('"plugin": ["legacy"]'); expect(content).toContain('"theme": "private-value"'); expect(content).toContain('"package": "other"');
 expect(content).toContain('"--offline"'); expect(content).toContain('"--no-sync"');
 expect((await installConfig(file,options)).changed).toBe(false); expect(readdirSync(dir).length).toBe(2);
});
test("updates exact target once and retains operator metadata", async () => {
 const dir=fixture(), file=join(dir,"opencode.jsonc"); writeFileSync(file,JSON.stringify({plugins:[{package:options.packagePath,options:{modelMetadata:{a:1},audience:"old"}},{package:options.packagePath}]}));
 await installConfig(file,options); const parsed=JSON.parse(readFileSync(file,"utf8")); expect(parsed.plugins.length).toBe(1); expect(parsed.plugins[0].options.modelMetadata).toEqual({a:1}); expect(parsed.plugins[0].options.audience).toBe("https://api.mistral.ai");
});
test.each(['{broken','{"plugins": {}}','[]','{"plugins": [42]}','{"plugins": [{"package":"other", "options":42}]}'])("rejects invalid config without mutation: %s",async before => {
 const dir=fixture(), file=join(dir,"opencode.jsonc"); writeFileSync(file,before); await expect(installConfig(file,options)).rejects.toThrow(); expect(readFileSync(file,"utf8")).toBe(before); expect(readdirSync(dir)).toEqual(["opencode.jsonc"]);
});
test("prefers existing JSONC, then JSON, then creates JSONC",() => {
 const dir=fixture(); expect(selectConfigPath(dir)).toBe(join(dir,"opencode.jsonc")); writeFileSync(join(dir,"opencode.json"),"{}"); expect(selectConfigPath(dir)).toBe(join(dir,"opencode.json")); writeFileSync(join(dir,"opencode.jsonc"),"{}"); expect(selectConfigPath(dir)).toBe(join(dir,"opencode.jsonc"));
});
test("creates missing config with no backup and absolute offline helper",async () => {
 const dir=fixture(), file=join(dir,"nested","opencode.jsonc"); const result=await installConfig(file,options); expect(result.backup).toBeUndefined(); const parsed=JSON.parse(readFileSync(file,"utf8")); expect(parsed.plugins[0].options.helperCommand).toEqual({executable:options.uvPath,args:["run","--offline","--no-sync","--project",options.projectPath,"-m","mvibe_auth","bridge"]});
});
test("preserves comments within unrelated plugins and retained operator options",async () => {
 const dir=fixture(), file=join(dir,"opencode.jsonc");
 writeFileSync(file,`{"plugins":[\n// unrelated plugin\n{"package":"another", "options":{ /* private option */ "x":true}},\n{"package":${JSON.stringify(options.packagePath)},"options":{ /* keep catalog */ "modelMetadata":{} }}]}`);
 await installConfig(file,options); const content=readFileSync(file,"utf8"); expect(content).toContain("// unrelated plugin"); expect(content).toContain("/* private option */"); expect(content).toContain("/* keep catalog */");
});
test("CLI help and invalid options run offline without dependency preparation",async () => {
 await expect(runInstaller(["--help"])).resolves.toBeUndefined();
 await expect(runInstaller(["--unsupported"])).rejects.toThrow("Unknown or incomplete");
});
test("missing executables fail before configuration mutation without exposing config values",async () => {
 const dir=fixture(), file=join(dir,"opencode.jsonc"); writeFileSync(file,'{"private":"secret-synthetic-value"}');
 await expect(runInstaller(["--config-path",file,"--uv",join(dir,"absent-uv"),"--skip-prepare"],{which:()=>null,run:()=>{throw new Error("unexpected child");}})).rejects.toThrow("Missing native uv");
 expect(readdirSync(dir)).toEqual(["opencode.jsonc"]);
});
function commandFixture(version="opencode v2.2.5", fail?: string) {
 const dir=fixture(), executable=join(dir,"native.exe"), config=join(dir,"opencode.jsonc"); writeFileSync(executable,Buffer.from("MZsynthetic")); const calls:string[][]=[];
 const runtime:InstallerRuntime={which:()=>executable,run:(command)=>{calls.push(command); const args=command.slice(1); return {exitCode:args.includes(fail??"never")?1:0,stdout:args[0]==="--version"?(calls.length===1?"uv 0.9.8":calls.length===2?"1.4.4":version):args[0]==="debug"?dir:""};}};
 return {dir,executable,config,calls,runtime};
}
test.each(["opencode v2.2.5","2.2.5","opencode 2.2.6"])("accepts host format %s and resolves native config directory",async version=>{
 const f=commandFixture(version); await runInstaller(["--skip-prepare"],f.runtime); expect(f.calls[3]!.slice(1)).toEqual(["debug","paths","config"]); expect(JSON.parse(readFileSync(f.config,"utf8")).plugins[0].options.helperCommand.executable).toBe(f.executable);
});
test("rejects incompatible host and preparation failure before configuration mutation",async ()=>{
 const old=commandFixture("opencode v1.9.0"); await expect(runInstaller(["--skip-prepare"],old.runtime)).rejects.toThrow("major version 2"); expect(readdirSync(old.dir)).toEqual(["native.exe"]);
 const f=commandFixture(undefined,"sync"); await expect(runInstaller(["--config-path",f.config],f.runtime)).rejects.toThrow("preparation failed"); expect(readdirSync(f.dir)).toEqual(["native.exe"]);
});
test("prepares locked dependencies and validates helper before editing explicitly selected config",async ()=>{
 const f=commandFixture(); await runInstaller(["--config-path",f.config],f.runtime);
 expect(f.calls.map(c=>c.slice(1))).toEqual([["--version"],["--version"],["--version"],["sync","--locked","--python","3.12"],["install","--frozen-lockfile","--ignore-scripts"],["run","typecheck"],["run","build"],["run","--offline","--no-sync","--project",expect.any(String),"python","-c",expect.any(String)]]);
 expect(JSON.parse(readFileSync(f.config,"utf8")).plugins).toHaveLength(1);
});
