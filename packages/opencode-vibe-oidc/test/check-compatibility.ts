/** Explicit ONLINE preparation action. Never imported by offline verification. */
import { cpSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { createHash } from "node:crypto";
import assert from "node:assert/strict";

const action = process.argv[2];
if (action !== "--discover-current" && action !== "--prepare-current") throw new Error("Choose --discover-current or --prepare-current (explicit network-enabled actions)");
async function current(name: string) {
  const response = await fetch(`https://registry.npmjs.org/${encodeURIComponent(name)}/latest`);
  assert.equal(response.status, 200, "current release discovery failed");
  const value: unknown = await response.json();
  assert.ok(value && typeof value === "object" && "version" in value && typeof value.version === "string" && /^2\.\d+\.\d+$/.test(value.version), "Expected a stable OpenCode V2 release");
  return value.version;
}
const [sdk, cli] = await Promise.all([current("@opencode/plugin"), current("@opencode/cli")]);
const report = { checkedAt: new Date().toISOString(), sdk, cli, compatibility: "unverified" };
mkdirSync(resolve("../../reports"), { recursive: true });
writeFileSync("../../reports/current-v2-discovery.json", JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
if (action === "--prepare-current") {
  assert.equal(sdk, cli, "Current host and SDK releases differ; prepare an explicitly reviewed compatible pair");
  const directory = resolve(".compat", sdk, "provider");
  assert.ok(!existsSync(directory), "Preparation directory already exists; reuse its frozen lock for offline checks or select a fresh destination manually");
  mkdirSync(directory, { recursive: true });
  for (const path of ["src", "test", "scripts", "tsconfig.json", "tsconfig.build.json"]) cpSync(path, join(directory, path), { recursive: true });
  const manifest = JSON.parse(readFileSync("package.json", "utf8"));
  manifest.dependencies["@opencode/plugin"] = sdk; manifest.devDependencies["@opencode/cli"] = cli;
  writeFileSync(join(directory, "package.json"), JSON.stringify(manifest, null, 2));
  const install = Bun.spawnSync([process.execPath, "install", "--ignore-scripts"], { cwd: directory, stdout: "inherit", stderr: "inherit" });
  assert.equal(install.exitCode, 0, "Explicit preparation failed; offline verification must not install missing dependencies");
  assert.equal(process.platform, "win32", "Configure the corresponding prepared host artifact for this platform");
  const host = join(directory, "node_modules", "@opencode", `cli-windows-${process.arch}`, "bin", "opencode.exe");
  assert.ok(existsSync(host), "Prepared platform-specific host is missing");
  const version = Bun.spawnSync([host, "--version"]);
  assert.equal(version.exitCode, 0); assert.ok(version.stdout.toString().includes("v" + cli));
  const project=resolve("../.."),output=resolve("../../reports/current-v2-tool-baseline");
  writeFileSync(join(directory, "prepared-pair.json"), JSON.stringify({ sdk, cli, sha256: createHash("sha256").update(readFileSync(host)).digest("hex"), host, projectRoot:project, offlineCommand: `bun --no-install run test:tool-baseline --provider-root "${directory}" --project-root "${project}" --expected-version ${sdk} --output "${output}"` }, null, 2));
  const outcomes=[];
  for (const args of [["run", "build"], ["run", "test/host-integration.ts"], ["run", "test:tool-baseline", "--provider-root",directory,"--project-root",project,"--expected-version",sdk,"--output",output]]) {
    const check = Bun.spawnSync([process.execPath,"--no-install", ...args], { cwd: directory, env: { ...process.env, OPENCODE_TEST_BINARY: host, VIBE_EXPECTED_HOST_VERSION:sdk, VIBE_TEST_PROJECT_ROOT:project, VIBE_HOST_REPORT: resolve("../../reports/current-v2-compatibility.json") }, stdout: "inherit", stderr: "inherit" });
    outcomes.push(check.exitCode);
  }
  assert.ok(outcomes.every(code=>code===0), "Current V2 compatibility is unverified/failed; inspect build, host, contract drift and baseline results separately");
}
