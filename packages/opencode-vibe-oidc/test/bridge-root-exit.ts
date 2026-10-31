/** Regression for cleanup authority after the helper's original runtime exits. */
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, existsSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import assert from "node:assert/strict";
import { StdioBridge, terminateOwnedTree, spawnOwnedHelper } from "../src/bridge.js";
import type { BridgeRuntime } from "../src/bridge.js";
const python = process.env.VIBE_TEST_UV_PYTHON;
if (!python) throw new Error("Supply an absolute uv-managed VIBE_TEST_UV_PYTHON.");
const root = mkdtempSync(join(tmpdir(), "bridge-root-exit-"));
const owned = join(root, "owned"); mkdirSync(owned);
const env: NodeJS.ProcessEnv = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, TEMP: root, TMP: root, HOME: root, USERPROFILE: root, APPDATA: root, LOCALAPPDATA: root };
const fixture = resolve("test/synthetic-bridge.py");
const options = { env, cwd: root, shell: false as const, windowsHide: true as const, detached: process.platform !== "win32", stdio: ["pipe", "pipe", "pipe"] as ["pipe", "pipe", "pipe"] };
const child = spawnOwnedHelper(python, ["-u", fixture, "exit-first", owned], options);
const sentinel = spawn(python, ["-u", fixture, "sentinel", root], options);
const alive = (pid: number) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const sentinelPID = await new Promise<number>((ok, fail) => { sentinel.stdout.once("data", data => ok(Number(data.toString().trim()))); sentinel.once("error", fail); });
let workerPID: number | undefined;
try {
  await new Promise<void>((ok, fail) => { child.once("exit", () => ok()); child.once("error", fail); });
  assert.ok(existsSync(join(owned, "pids.jsonl")));
  const [rootPID, descendant] = JSON.parse(readFileSync(join(owned, "pids.jsonl"), "utf8").trim()) as number[];
  workerPID = descendant!;
  assert.ok(!alive(rootPID!));
  await terminateOwnedTree(child);
  assert.ok(!alive(workerPID), "owned descendant survives cleanup after original root exited");
  assert.ok(alive(sentinelPID), "unrelated interpreter must survive");
  const restartRoot = join(root, "restart"); mkdirSync(restartRoot);
  const children: ReturnType<typeof spawnOwnedHelper>[] = [];
  const io: BridgeRuntime = {
    spawn: (file, args, opts) => { const ownedChild = spawnOwnedHelper(file, args, { ...opts, env, cwd: root }); children.push(ownedChild); return ownedChild; },
    terminate: terminateOwnedTree, now: Date.now, timer: setTimeout, clearTimer: clearTimeout,
  };
  const bridge = new StdioBridge({ audience: "https://inference.example.test", command: { executable: python, args: ["-u", fixture, "exit-after-status", restartRoot] } }, io);
  try {
    assert.deepEqual(await bridge.request("status", {}), { signed_in: false, config_match: true });
    await new Promise<void>(ok => { const first = children[0]!; if (first.exitCode !== null) ok(); else first.once("exit", () => ok()); });
    const firstPids = JSON.parse(readFileSync(join(restartRoot, "pids.jsonl"), "utf8").trim()) as number[];
    assert.ok(firstPids.every(pid => !alive(pid)), "all owned runtimes gone before replacement");
    assert.deepEqual(await bridge.request("status", {}), { signed_in: true, config_match: true });
    assert.equal(children.length, 2);
    assert.ok(alive(sentinelPID));
  } finally { await bridge.dispose(); }
  console.log("Root-exits-first owned descendant cleanup and unrelated interpreter survival PASS");
} finally {
  for (const pid of [workerPID, sentinelPID]) if (pid && alive(pid)) {
    if (process.platform === "win32") Bun.spawnSync(["taskkill.exe", "/PID", String(pid), "/T", "/F"], { windowsHide: true, stdout: "ignore", stderr: "ignore" });
    else process.kill(pid, "SIGKILL");
  }
  await new Promise(resolve => setTimeout(resolve, 100));
  rmSync(root, { recursive: true, force: true });
}

