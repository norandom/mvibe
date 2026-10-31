/** Offline lifecycle only. Importing this module starts no process or server. */
import { isAbsolute, relative, resolve, sep, join } from "node:path";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, lstatSync, realpathSync, rmSync, existsSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { createHash, randomUUID } from "node:crypto";
import { spawn, spawnSync } from "node:child_process";
import { OpenCode } from "@opencode/client";
import { WINDOWS_JOB_SOURCE } from "../../src/windows-job.js";
import { requireReferenceHostVersion, checkPreparedDependencies } from "./preparation.js";
import { parseWorkspaceQueryProbe } from "./workspace-query-report.js";
import { parseCapabilityReport } from "./capability.js";

export function requireOwnedPath(root: string, target: string): string {
  const part = relative(root, target);
  if (!isAbsolute(root) || !isAbsolute(target) || part === "" || part === ".." || part.startsWith(".." + sep) || isAbsolute(part)) throw new Error("HOST_ISOLATION");
  return resolve(target);
}
export function requireLocation(expected: string, actual: string): void {
  if (resolve(expected) !== resolve(actual)) throw new Error("HOST_LOCATION");
}
export function offlineEnvironment(root: string, inherited: NodeJS.ProcessEnv): Record<string, string> {
  return { PATH: inherited.PATH ?? "", SystemRoot: inherited.SystemRoot ?? "C:\\Windows", HOME: root, USERPROFILE: root,
    APPDATA: join(root, "roaming"), LOCALAPPDATA: join(root, "local"), TEMP: join(root, "tmp"), TMP: join(root, "tmp"), TMPDIR: join(root, "tmp"),
    XDG_DATA_HOME: join(root, "data"), XDG_CONFIG_HOME: join(root, "config"), XDG_CACHE_HOME: join(root, "cache"), XDG_STATE_HOME: join(root, "state"),
    OPENCODE_TEST_HOME: root, OPENCODE_DISABLE_AUTOUPDATE: "true", OPENCODE_DISABLE_MODELS_FETCH: "true" };
}

export function requireBudget(ms: number): void {
  if (!Number.isSafeInteger(ms) || ms < 100 || ms > 60_000) throw new Error("HOST_BUDGET");
}
export function validateDebugPaths(root: string, paths: unknown): void {
  if (!paths || typeof paths !== "object" || Array.isArray(paths) || Object.keys(paths).length === 0) throw new Error("HOST_ISOLATION");
  for (const path of Object.values(paths)) {
    if (typeof path !== "string") throw new Error("HOST_ISOLATION");
    if (resolve(path) === resolve(root)) continue; // CLI reports its test home as well.
    requireOwnedPath(root, path);
  }
}
export function parseDebugPaths(output: string): Record<string, string> {
  const paths: Record<string, string> = {};
  for (const line of output.trim().split(/\r?\n/)) {
    const match = /^(home|data|cache|config|state|tmp|bin|log|repos|db)\s+(.+)$/.exec(line);
    if (!match || paths[match[1]!]) throw new Error("HOST_ISOLATION");
    paths[match[1]!] = match[2]!;
  }
  if (Object.keys(paths).length !== 10) throw new Error("HOST_ISOLATION");
  return paths;
}
function verifyTree(root: string, target: string): void {
  requireOwnedPath(root, target);
  let path = target;
  while (path !== root) {
    if (existsSync(path) && lstatSync(path).isSymbolicLink()) throw new Error("HOST_ISOLATION");
    path = resolve(path, "..");
  }
  if (realpathSync(root) !== root) throw new Error("HOST_ISOLATION");
}
function verifyRemoval(root: string): void {
  const inspect = (directory: string) => {
    for (const entry of readdirSync(directory)) {
      const target = join(directory, entry); verifyTree(root, target);
      if (lstatSync(target).isDirectory()) inspect(target);
    }
  };
  inspect(root);
}
export type OfflineHostOptions = { host: string; bun: string; python: string; startupMs: number; executionMs: number; cleanupMs: number; capabilityProbe?: boolean; workspaceProbe?: boolean };
/** Default readiness has no tool claims; the explicit capability probe adds its
 * bounded native observations, without granting all-tool compatibility. */
export async function smokeOfflineHost(options: OfflineHostOptions, signal?: AbortSignal) {
  if (options.capabilityProbe && options.workspaceProbe) throw new Error("HOST_PREFLIGHT");
  if (signal?.aborted) throw new Error("HOST_CANCELLED");
  for (const ms of [options.startupMs, options.executionMs, options.cleanupMs]) requireBudget(ms);
  for (const binary of [options.host, options.bun, options.python]) if (!isAbsolute(binary) || !lstatSync(binary).isFile()) throw new Error("HOST_RUNTIME");
  const venvConfig = resolve(options.python, "../..", "pyvenv.cfg");
  if (!existsSync(venvConfig) || !/^uv\s*=\s*\S+/m.test(readFileSync(venvConfig, "utf8"))) throw new Error("HOST_RUNTIME");
  const packageRoot = resolve(import.meta.dir, "../..");
  checkPreparedDependencies(path => readFileSync(join(packageRoot, path), "utf8"), Bun.version);
  const root = realpathSync(mkdtempSync(join(tmpdir(), "vibe-tool-host-")));
  const cwd = join(root, "workspace");
  const env = offlineEnvironment(root, process.env);
  const password = randomUUID();
  env.OPENCODE_SERVER_PASSWORD = password;
  env.PYTHONPATH = root + (process.platform === "win32" ? ";" : ":") + resolve(packageRoot, "../..");
  env.VIBE_TEST_ROTATION = join(root, "rotation");
  let child: ReturnType<typeof spawn> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const abort = new AbortController();
  const cancel = () => abort.abort();
  signal?.addEventListener("abort", cancel, { once: true });
  const jobName = "Local\\vibe-tool-" + randomUUID();
  const jobScript = join(root, "windows-job.py");
  const run = (binary: string, args: string[]) => {
    const result = spawnSync(binary, args, { env, cwd, timeout: options.startupMs, maxBuffer: 64 * 1024, windowsHide: true });
    if (result.status !== 0 || result.error) throw new Error("HOST_PREFLIGHT");
    return result.stdout.toString().trim();
  };
  try {
    for (const directory of [cwd, ...["data", "cache", "state", "config", "tmp", "local", "roaming", "plugins/readiness"].map(p => join(root, p))]) mkdirSync(directory, { recursive: true });
    writeFileSync(join(root, "rotation"), "one");
    writeFileSync(join(cwd, "readiness.txt"), randomUUID(), { mode: 0o600 });
    writeFileSync(jobScript, WINDOWS_JOB_SOURCE);
    writeFileSync(join(root, "sitecustomize.py"), readFileSync(resolve(packageRoot, "test/host-integration-sitecustomize.py")));
    const hostVersion = requireReferenceHostVersion(run(options.host, ["--version"]));
    if (run(options.bun, ["--version"]) !== "1.4.4") throw new Error("HOST_RUNTIME");
    const pythonVersion = run(options.python, ["-c", "import sys; print('.'.join(map(str,sys.version_info[:3])))"]);
    const guardProbe = run(options.python, ["-c", "import sitecustomize as s; import keyring, socket, webbrowser; import mvibe_auth.bridge as b; assert b.create_session is s.Session; assert keyring.get_password is s.deny; assert webbrowser.open is s.deny; assert socket.create_connection is s.deny; print('offline-guards-ready')"]);
    if (guardProbe !== "offline-guards-ready") throw new Error("HOST_GUARDS");
    const hashes = Object.fromEntries(Object.entries({ host: options.host, bun: options.bun, python: options.python }).map(([key, binary]) => [key, createHash("sha256").update(readFileSync(binary)).digest("hex")]));
    let paths: unknown;
    try { paths = parseDebugPaths(run(options.host, ["debug", "paths"])); } catch { throw new Error("HOST_ISOLATION"); }
    validateDebugPaths(root, paths);
    for (const value of Object.values(paths as Record<string, string>)) if (resolve(value) !== root) verifyTree(root, value);
    const readiness = join(root, "ready.json");
    const plugin = resolve(import.meta.dir, options.workspaceProbe ? "workspace-host-plugin.ts" : options.capabilityProbe ? "host-plugin.ts" : "readiness-plugin.ts").replaceAll("\\", "/");
    writeFileSync(join(root, "plugins/readiness/index.ts"), `export { default } from ${JSON.stringify(plugin)};`);
    writeFileSync(join(cwd, "opencode.json"), JSON.stringify({ permission: options.workspaceProbe ? {"*":"deny",read:{"*":"deny","queries-*/*.match.txt":"allow"},glob:{"*":"deny","**/*.match.txt":"allow"},grep:{"*":"deny","needle_*":"allow"}} : options.capabilityProbe ? { "*":"deny", read: { "*":"deny", "capability-allowed.txt":"allow" } } : { "*": "deny" }, plugins: [{ package: "../plugins/readiness", options: { readiness, helperCommand: { executable: options.python, args: ["-m", "mvibe_auth", "bridge"] }, audience: "https://inference.example.test" } }] }));
    const reservation = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: () => new Response(null, { status: 403 }) });
    const port = reservation.port!; await reservation.stop(true);
    const args = [options.host, "serve", "--hostname", "127.0.0.1", "--port", String(port)];
    child = process.platform === "win32" ? spawn(options.python, ["-u", jobScript, "launch", jobName, ...args], { env, cwd, windowsHide: true, stdio: "ignore" }) : spawn(options.host, args.slice(1), { env, cwd, detached: true, stdio: "ignore" });
    child.on("error", cancel);
    timer = setTimeout(cancel, options.startupMs);
    if (signal?.aborted) cancel();
    const client = OpenCode.make({ baseUrl: `http://127.0.0.1:${port}`, headers: { authorization: "Basic " + btoa("opencode:" + password) } });
    const requestOptions = { signal: abort.signal };
    while (true) {
      if (abort.signal.aborted || child.exitCode !== null) throw new Error("HOST_READINESS");
      try { await client.server.info(requestOptions); break; } catch { if (abort.signal.aborted) throw new Error("HOST_READINESS"); await new Promise(resolve => setTimeout(resolve, 25)); }
    }
    const location = { directory: cwd };
    const actual = await client.location.get({ location }, requestOptions);
    requireLocation(cwd, actual.directory);
    await client.provider.list({ location }, requestOptions);
    clearTimeout(timer); timer = setTimeout(cancel, options.executionMs);
    while (!existsSync(readiness)) {
      if (abort.signal.aborted || child.exitCode !== null) throw new Error("HOST_READINESS");
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    if(lstatSync(readiness).size>65536)throw new Error("HOST_CAPTURE");
    const ready: unknown = JSON.parse(readFileSync(readiness, "utf8"));
    if (!options.capabilityProbe && !options.workspaceProbe && JSON.stringify(ready) !== '{"ready":true}') throw new Error("HOST_MODEL");
    const capability = options.capabilityProbe ? parseCapabilityReport(ready) : undefined;
    return { hostVersion, bunVersion: "1.4.4", pythonVersion, hashes, model: "vibe-oidc/synthetic-model", ready: capability ? capability.ready === true : true, live: "unverified", ...(capability ? { capability } : {}), ...(options.workspaceProbe ? {workspace:parseWorkspaceQueryProbe(ready),ready:(ready as {ready?:boolean}).ready===true} : {}) };
  } finally {
    const cleanupDeadline = Date.now() + options.cleanupMs;
    clearTimeout(timer); abort.abort(); signal?.removeEventListener("abort", cancel);
    if (child) {
      if (process.platform === "win32") {
        // Retire the still-owned supervisor first, including the launch-before-job
        // window. Closing its non-inherited job handle also retires descendants.
        if (child.exitCode === null && child.signalCode === null && child.pid) {
          spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { timeout: Math.max(1, cleanupDeadline - Date.now()), windowsHide: true, stdio: "ignore" });
        }
        const stopped = spawnSync(options.python, ["-u", jobScript, "stop", jobName], { env, cwd, timeout: options.cleanupMs, windowsHide: true, stdio: "ignore" });
        if (stopped.status !== 0) throw new Error("HOST_CLEANUP");
      } else if (child.pid) { try { process.kill(-child.pid, "SIGKILL"); } catch { if (child.exitCode === null) throw new Error("HOST_CLEANUP"); } }
      if (child.exitCode === null) await new Promise<void>((done, reject) => {
        const deadline = setTimeout(() => reject(new Error("HOST_CLEANUP")), Math.max(1, cleanupDeadline - Date.now()));
        child!.once("exit", () => { clearTimeout(deadline); done(); });
      });
    }
    verifyRemoval(root);
    rmSync(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 50 });
    if (existsSync(root) || Date.now() > cleanupDeadline) throw new Error("HOST_CLEANUP");
  }
}
