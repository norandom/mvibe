import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, existsSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import assert from "node:assert/strict";
import { OpenCode } from "@opencode/client";
import { createHash } from "node:crypto";

const host = process.env.OPENCODE_TEST_BINARY, python = process.env.VIBE_TEST_PYTHON;
if (!host || !python) throw new Error("Prepare OPENCODE_TEST_BINARY and uv-managed VIBE_TEST_PYTHON explicitly");
const report = resolve(process.env.VIBE_HOST_REPORT ?? "../../reports/host-compatibility.json");
const root = mkdtempSync(join(tmpdir(), "vibe-host-integration-"));
const evidence = join(root, "result.json"), rotation = join(root, "rotation");
let version = "unknown";
const sdk = (JSON.parse(readFileSync("node_modules/@opencode/plugin/package.json", "utf8")) as { version: string }).version;
let child: Bun.Subprocess<"ignore", "pipe", "pipe"> | undefined;
let timer: ReturnType<typeof setTimeout> | undefined;
function stopHost() {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") Bun.spawnSync(["taskkill.exe", "/PID", String(child.pid), "/T", "/F"], { stdout: "ignore", stderr: "ignore" });
  else child.kill();
}
function record(result: unknown) {
  mkdirSync(resolve(report, ".."), { recursive: true });
  writeFileSync(report, JSON.stringify({ kind: "offline-host-compatibility", host: version, sdk, hostSha256: createHash("sha256").update(readFileSync(host!)).digest("hex"), live: "unverified", result }, null, 2));
}
try {
  const env = {
    PATH: process.env.PATH ?? "", SystemRoot: process.env.SystemRoot ?? "C:\\Windows",
    USERPROFILE: root, HOME: root, APPDATA: join(root, "roaming"), LOCALAPPDATA: join(root, "local"), TEMP: root, TMP: root,
    XDG_DATA_HOME: join(root, "data"), XDG_CACHE_HOME: join(root, "cache"), XDG_STATE_HOME: join(root, "state"), XDG_CONFIG_HOME: join(root, "config"),
    OPENCODE_SERVER_PASSWORD: "synthetic-local-only", OPENCODE_TEST_HOME: root, OPENCODE_DISABLE_AUTOUPDATE: "true", OPENCODE_DISABLE_MODELS_FETCH: "true",
    PYTHONPATH: root + (process.platform === "win32" ? ";" : ":") + (process.env.VIBE_TEST_PROJECT_ROOT ?? resolve("../..")), VIBE_TEST_ROTATION: rotation,
    ...(process.env.VIBE_TEST_PROVIDER_ENTRY ? { VIBE_TEST_PROVIDER_ENTRY: resolve(process.env.VIBE_TEST_PROVIDER_ENTRY), VIBE_TEST_RESOLUTIONS: join(root, "resolutions.ndjson") } : {}),
  };
  const current = Bun.spawnSync([host, "--version"], { env, timeout: 10_000 });
  assert.equal(current.exitCode, 0); version = current.stdout.toString().trim();
  assert.match(version, /\bv2\./);
  writeFileSync(rotation, "one");
  writeFileSync(join(root, "metadata.txt"), "owned metadata bytes\n");
  copyFileSync(resolve("test/host-integration-sitecustomize.py"), join(root, "sitecustomize.py"));
  const plugin = join(root, "plugins", "integration"); mkdirSync(plugin, { recursive: true });
  writeFileSync(join(plugin, "index.ts"), `export { default } from ${JSON.stringify(resolve("test/host-integration-plugin.ts").replaceAll("\\", "/"))};`);
  writeFileSync(join(root, "opencode.json"), JSON.stringify({ permission: { "*": "allow" }, plugins: [{ package: "./plugins/integration", options: { evidence, rotation, metadataFile: join(root, "metadata.txt"), audience: "https://inference.example.test", helperCommand: { executable: python, args: ["-m", "mvibe_auth", "bridge"] } } }] }));
  const reservation = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: () => new Response() });
  const port = reservation.port!; await reservation.stop(true);
  child = Bun.spawn([host, "serve", "--hostname", "127.0.0.1", "--port", String(port)], { env, cwd: root, stdin: "ignore", stdout: "pipe", stderr: "pipe" });
  timer = setTimeout(stopHost, 45_000);
  const output = new Response(child.stdout).text(), errors = new Response(child.stderr).text();
  const client = OpenCode.make({ baseUrl: `http://127.0.0.1:${port}`, headers: { authorization: "Basic " + btoa("opencode:synthetic-local-only") } });
  let ready = false;
  for (let i = 0; i < 200 && !ready; i++) {
    try { await client.server.info(); ready = true; } catch { await new Promise(resolve => setTimeout(resolve, 25)); }
  }
  assert.ok(ready, "isolated host startup");
  await client.provider.list({ location: { directory: root } });
  for (let i = 0; i < 1200 && !existsSync(evidence); i++) await new Promise(resolve => setTimeout(resolve, 25));
  stopHost();
  const [out, err] = await Promise.all([output, errors, child.exited]);
  clearTimeout(timer);
  const result: unknown = existsSync(evidence) ? JSON.parse(readFileSync(evidence, "utf8")) : { status: "failed", failure: "Host did not write evidence" };
  record(result);
  assert.ok(existsSync(evidence), err + out);
  assert.equal((result as { status: string }).status, "passed", JSON.stringify(result));
  console.log(`Actual ${version} / SDK ${sdk} consumer integration PASS: ${report}`);
} catch (error) {
  if (!existsSync(evidence)) record({ status: "failed", failure: "Isolated host startup/integration did not complete" });
  throw error;
} finally {
  stopHost(); if (child) await child.exited; clearTimeout(timer);
  rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
