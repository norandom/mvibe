/** Explicit process integration harness; never imported by bun test's default-deny suite. */
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, existsSync, rmSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import assert from "node:assert/strict";
import { StdioBridge, terminateOwnedTree, spawnOwnedHelper } from "../src/bridge.js";
import type { BridgeRuntime } from "../src/bridge.js";
const python = process.env.VIBE_TEST_UV_PYTHON;
if (!python) throw new Error("Supply VIBE_TEST_UV_PYTHON as the absolute uv-managed virtualenv interpreter.");
const root = mkdtempSync(join(tmpdir(), "vibe-bridge-process-"));
const fixture = resolve("test/synthetic-bridge.py");
const env: NodeJS.ProcessEnv = {
  PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, TEMP: root, TMP: root,
  HOME: root, USERPROFILE: root, PYTHONPATH: root, APPDATA: join(root, "appdata"), LOCALAPPDATA: join(root, "local"),
  XDG_CONFIG_HOME: join(root, "config"), XDG_DATA_HOME: join(root, "data"), XDG_CACHE_HOME: join(root, "cache"),
};
for (const key of ["APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"]) mkdirSync(env[key]!, { recursive: true });
const spawned: ReturnType<typeof spawn>[] = [];
const io: BridgeRuntime = {
  spawn: (file, args, options) => { const child = spawnOwnedHelper(file, args, { ...options, env, cwd: root }); spawned.push(child); return child; },
  terminate: terminateOwnedTree, now: Date.now, timer: setTimeout, clearTimer: clearTimeout,
};
const bridge = new StdioBridge({ audience: "https://inference.example.test", command: { executable: python, args: ["-u", fixture, "main", root] } }, io);
const sentinel = spawn(python, ["-u", fixture, "sentinel", root], { env, cwd: root, windowsHide: true, shell: false, detached: process.platform !== "win32", stdio: ["pipe", "pipe", "pipe"] });
const sentinelPID = await new Promise<number>((ok, fail) => { sentinel.stdout.once("data", data => ok(Number(data.toString().trim()))); sentinel.once("error", fail); });
const alive = (pid: number) => { try { process.kill(pid, 0); return true; } catch { return false; } };
async function until(predicate: () => boolean) {
  const deadline = Date.now() + 5000;
  while (!predicate()) { if (Date.now() > deadline) throw new Error("Synthetic process deadline"); await Bun.sleep(20); }
}
try {
  assert.deepEqual(await bridge.request("status", {}), { signed_in: false, config_match: true });
  const controller = new AbortController();
  const resolving = bridge.request("resolve", { target_url: "https://inference.example.test/v1/models" }, controller.signal).catch(e => e);
  await until(() => existsSync(join(root, "resolving")));
  const firstPids = JSON.parse(readFileSync(join(root, "pids.jsonl"), "utf8").split("\n")[0]!) as number[];
  controller.abort(); assert.equal((await resolving).code, "cancelled");
  // Lazy helper creation waits for tree termination; worker reacquires the same native lock.
  assert.deepEqual(await bridge.request("status", {}), { signed_in: true, config_match: true });
  await until(() => firstPids.every(pid => !alive(pid)));
  assert.ok(alive(sentinelPID), "unrelated interpreter remains alive");
  const start = Date.now(); await bridge.dispose();
  assert.ok(existsSync(join(root, "drained")), "graceful persistence drain completed");
  assert.ok(Date.now() - start < 5000);
  const allPids = readFileSync(join(root, "pids.jsonl"), "utf8").trim().split("\n").flatMap(line => JSON.parse(line) as number[]);
  await until(() => allPids.every(pid => !alive(pid)));
  assert.ok(alive(sentinelPID));
  const stubbornRoot = join(root, "stubborn"); mkdirSync(stubbornRoot);
  const stubborn = new StdioBridge({ audience: "https://inference.example.test", command: { executable: python, args: ["-u", fixture, "stubborn", stubbornRoot] } }, io);
  try {
    await stubborn.request("status", {});
    const stubbornPids = JSON.parse(readFileSync(join(stubbornRoot, "pids.jsonl"), "utf8").trim()) as number[];
    const closingAt = Date.now(); await stubborn.dispose();
    assert.ok(Date.now() - closingAt >= 4900, "force only after graceful window");
    assert.ok(Date.now() - closingAt < 7000, "forced tree cleanup stays bounded");
    await until(() => stubbornPids.every(pid => !alive(pid)));
    assert.ok(alive(sentinelPID));
  } finally { await stubborn.dispose(); }
  const uvRoot = join(root, "uv"); mkdirSync(uvRoot); copyFileSync(fixture, join(root, "synthetic_bridge.py"));
  const viaUv = new StdioBridge({ audience: "https://inference.example.test", command: { executable: "uv", args: ["run", "--offline", "--no-sync", "--project", resolve("../.."), "-m", "synthetic_bridge", "main", uvRoot] } }, io);
  try { assert.deepEqual(await viaUv.request("status", {}), { signed_in: false, config_match: true }); }
  finally { await viaUv.dispose(); }
  console.log("Managed bridge real uv-venv launcher/runtime/descendant cleanup, unrelated process survival, unknown-commit reload and graceful drain PASS");
} finally {
  await bridge.dispose();
  for (const child of spawned) if (child.exitCode === null && child.signalCode === null) await terminateOwnedTree(child as Parameters<typeof terminateOwnedTree>[0]);
  await terminateOwnedTree(sentinel);
  await until(() => !alive(sentinelPID));
  rmSync(root, { recursive: true, force: true });
}



