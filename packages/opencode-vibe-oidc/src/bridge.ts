import { spawn as nodeSpawn } from "node:child_process";
import type { ChildProcessWithoutNullStreams as Child } from "node:child_process";
import { isAbsolute, basename } from "node:path";
import { join } from "node:path";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { randomUUID } from "node:crypto";
import { WINDOWS_JOB_SOURCE } from "./windows-job.js";
import { MAX_FRAME_BYTES, parseRequest, parseResponse, ProtocolError } from "./protocol.js";
import type { Method, Params, Results } from "./protocol.js";


export class BridgeError extends Error {
  constructor(readonly code: string, message = "Authentication helper failed. Configure an installed uv-managed mvibe-auth runtime and compatible provider versions.") { super(message); this.name = "BridgeError"; }
}
/** Explicit installed runtime, never resolved through a bare python/py alias.
 * For uv use ["run", "--offline", "--no-sync", "--project", installedDirectory,
 * "-m", "mvibe_auth", "bridge"]. The project/environment must already exist.
 * Alternatively supply the absolute interpreter of a uv-created environment,
 * with ["-m", "mvibe_auth", "bridge"]. No automatic runtime installation occurs.
 */
export type HelperCommand = { executable: string; args: readonly string[] };
export type BridgeOptions = { command?: HelperCommand; audience: string; apiMajor?: number };
// mvibe-auth distribution/API version (pyproject.toml), independent of Vibe
// source provenance and the separately versioned NDJSON protocol.
const HELPER_API_MAJOR = 2;
export type BridgeRuntime = {
  spawn: (command: string, args: string[], options: { shell: false; windowsHide: true; detached: boolean; stdio: ["pipe", "pipe", "pipe"] }) => Child;
  terminate: (child: Child) => Promise<void>;
  now: () => number;
  timer: (callback: () => void, ms: number) => ReturnType<typeof setTimeout>;
  clearTimer: (timer: ReturnType<typeof setTimeout>) => void;
};
type SpawnOptions = Parameters<BridgeRuntime["spawn"]>[2] & { env?: NodeJS.ProcessEnv; cwd?: string };
type JobOwnership = { executable: string; stopArgs: string[]; directory: string; options: SpawnOptions };
const jobs = new WeakMap<Child, JobOwnership>();
/** Atomic native job membership retains descendant ownership after root exit. */
export function spawnOwnedHelper(executable: string, args: string[], options: SpawnOptions): Child {
  if (process.platform !== "win32") return nodeSpawn(executable, args, options);
  const uv = /^uv(\.exe)?$/i.test(basename(executable));
  const moduleIndex = args.findIndex(arg => arg === "-m" || arg === "--module");
  if (uv && moduleIndex < 0) throw new BridgeError("setup");
  const prefix = uv ? args.slice(0, moduleIndex) : ["-u"];
  const directory = mkdtempSync(join(tmpdir(), "vibe-owned-helper-"));
  const script = join(directory, "windows-job.py");
  const name = "Local\\vibe-auth-" + randomUUID();
  try {
    writeFileSync(script, WINDOWS_JOB_SOURCE, { mode: 0o600 });
    const child = nodeSpawn(executable, [...prefix, script, "launch", name, executable, ...args], options);
    jobs.set(child, { executable, stopArgs: [...prefix, script, "stop", name], directory, options });
    return child;
  } catch (error) { rmSync(directory, { recursive: true, force: true }); throw error; }
}
/** Address only the owned child tree, never executable names. */
export async function terminateOwnedTree(child: Child): Promise<void> {
  if (!child.pid) return;
  if (process.platform === "win32") {
    const ownership = jobs.get(child);
    const rootExited = child.exitCode !== null || child.signalCode !== null;
    if (rootExited && !ownership) throw new BridgeError("cleanup");
    // Never address a known-exited PID: Windows may already have reused it.
    if (!rootExited) await new Promise<void>((resolve, reject) => {
      const killer = nodeSpawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { shell: false, windowsHide: true, stdio: "ignore" });
      killer.once("error", () => reject(new BridgeError("cleanup")));
      killer.once("exit", code => {
        // An exited root proves nothing about descendants. Only an independently
        // queryable owned job allows proceeding after a missing root PID.
        if (code === 0 || ownership) resolve();
        else reject(new BridgeError("cleanup"));
      });
    });
    if (ownership) {
      await new Promise<void>((resolve, reject) => {
        const cleanup = nodeSpawn(ownership.executable, ownership.stopArgs, { ...ownership.options, stdio: "ignore" });
        const deadline = setTimeout(() => { cleanup.kill("SIGKILL"); reject(new BridgeError("cleanup")); }, 5_000);
        cleanup.once("error", () => { clearTimeout(deadline); reject(new BridgeError("cleanup")); });
        cleanup.once("exit", code => { clearTimeout(deadline); if (code === 0) resolve(); else reject(new BridgeError("cleanup")); });
      });
      jobs.delete(child); rmSync(ownership.directory, { recursive: true, force: true });
    }
  } else {
    try { process.kill(-child.pid, "SIGKILL"); } catch { child.kill("SIGKILL"); }
  }
}
const runtime: BridgeRuntime = { spawn: spawnOwnedHelper, terminate: terminateOwnedTree, now: Date.now, timer: setTimeout, clearTimer: clearTimeout };
type Pending = { method: Method; resolve: (result: unknown) => void; reject: (error: BridgeError) => void; timer: ReturnType<typeof setTimeout>; clean: () => void };
type Session = { child: Child; pending: Map<string, Pending>; bytes: Buffer; dead: boolean; exited: Promise<void> };

/** One lazy helper per provider instance; retired operations are never replayed. */
export class StdioBridge {
  private session: Session | undefined;
  private ready: Promise<Session> | undefined;
  private retirement: Promise<void> = Promise.resolve();
  private cleanupFailed = false;
  private closed = false;
  private closing: Promise<void> | undefined;
  private sequence = 0;
  private expiries = new Map<string, number>();
  constructor(private readonly options: BridgeOptions, private readonly io: BridgeRuntime = runtime) {}
  private command(): HelperCommand {
    const command = this.options.command;
    if (!command || !command.executable || command.args.some(arg => typeof arg !== "string")) throw new BridgeError("setup");
    const name = basename(command.executable).toLowerCase();
    const uv = /^(uv)(\.exe)?$/.test(name);
    if (uv && (command.args[0] !== "run" || !command.args.includes("--offline") || !command.args.includes("--no-sync"))) throw new BridgeError("setup");
    if (!uv && !(isAbsolute(command.executable) && /^python(?:\d+(?:\.\d+)*)?(\.exe)?$/.test(name) && !/windowsapps/i.test(command.executable))) throw new BridgeError("setup");
    return command;
  }
  private async ensure(signal?: AbortSignal): Promise<Session> {
    if (this.closed) throw new BridgeError("closed", "Authentication helper is disposed.");
    if (this.ready) return this.ready;
    const starting = this.start(signal); this.ready = starting;
    try { return await starting; } catch (error) { if (this.ready === starting) this.ready = undefined; throw error; }
  }
  private async start(signal?: AbortSignal): Promise<Session> {
    await this.retirement;
    if (signal?.aborted) throw new BridgeError("cancelled");
    if (this.cleanupFailed) throw new BridgeError("cleanup", "Authentication helper cleanup failed; restart the provider before retrying.");
    if (this.closed) throw new BridgeError("closed");
    const command = this.command();
    let child: Child;
    try { child = this.io.spawn(command.executable, [...command.args], { shell: false, windowsHide: true, detached: process.platform !== "win32", stdio: ["pipe", "pipe", "pipe"] }); }
    catch { throw new BridgeError("setup"); }
    let finish!: () => void;
    const exited = new Promise<void>(resolve => { finish = resolve; });
    const session: Session = { child, pending: new Map(), bytes: Buffer.alloc(0), dead: false, exited };
    this.session = session;
    child.stdout.on("data", (chunk: Buffer) => this.receive(session, chunk));
    child.stdout.on("error", () => this.retire(session, new BridgeError("transport")));
    child.stdin.on("error", () => this.retire(session, new BridgeError("transport")));
    child.stderr.on("error", () => this.retire(session, new BridgeError("transport")));
    child.stderr.resume();
    child.once("error", () => { finish(); this.retire(session, new BridgeError("setup")); });
    child.once("exit", () => { finish(); this.retire(session, new BridgeError("exit")); });
    try {
      const hello = await this.send(session, "hello", {}, 10_000);
      if (hello.protocol !== 1 || !new RegExp("^" + (this.options.apiMajor ?? HELPER_API_MAJOR) + "\\.\\d+\\.\\d+(?:[.+-].*)?$").test(hello.api_version) || hello.audience !== this.options.audience) throw new BridgeError("incompatible", "Authentication helper protocol, API version or audience is incompatible; install compatible versions and check audience configuration.");
      return session;
    } catch (error) { this.retire(session, error instanceof BridgeError ? error : new BridgeError("incompatible")); throw error; }
  }
  private receive(session: Session, chunk: Buffer): void {
    if (session.dead) return;
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(10, offset);
      const end = newline < 0 ? chunk.length : newline + 1;
      if (session.bytes.length + end - offset > MAX_FRAME_BYTES) { this.retire(session, new BridgeError("frame_too_large")); return; }
      session.bytes = Buffer.concat([session.bytes, chunk.subarray(offset, end)]); offset = end;
      if (newline < 0) return;
      const frame = session.bytes; session.bytes = Buffer.alloc(0);
      try {
        const envelope: unknown = JSON.parse(frame.toString("utf8"));
        const id = typeof envelope === "object" && envelope !== null && "id" in envelope ? envelope.id : undefined;
        const pending = typeof id === "string" ? session.pending.get(id) : undefined;
        if (!pending) throw new ProtocolError();
        const response = parseResponse(frame, pending.method);
        session.pending.delete(response.id); this.io.clearTimer(pending.timer); pending.clean();
        if (response.ok) pending.resolve(response.result);
        else pending.reject(new BridgeError(response.error.code, "Authentication operation failed; retry or sign in explicitly if needed."));
      } catch (error) { this.retire(session, new BridgeError(error instanceof ProtocolError ? error.code : "invalid_frame")); return; }
    }
  }
  private send<M extends Method>(session: Session, method: M, params: Params[M], ms: number, signal?: AbortSignal): Promise<Results[M]> {
    if (session.dead || this.closed && method !== "shutdown") return Promise.reject(new BridgeError("closed"));
    const ordinary = [...session.pending.values()].filter(p => p.method !== "login.cancel" && p.method !== "shutdown").length;
    if (session.pending.size >= 32 || method !== "login.cancel" && method !== "shutdown" && ordinary >= 31) return Promise.reject(new BridgeError("busy", "Authentication helper is busy; retry later."));
    const id = String(++this.sequence);
    let frame: Buffer;
    try {
      frame = Buffer.from(JSON.stringify({ version: 1, id, method, params }) + "\n");
      parseRequest(frame);
    } catch { return Promise.reject(new BridgeError("invalid_frame")); }
    if (signal?.aborted) { this.retire(session, new BridgeError("cancelled")); return Promise.reject(new BridgeError("cancelled")); }
    return new Promise((resolve, reject) => {
      const abort = () => this.retire(session, new BridgeError("cancelled"));
      const timer = this.io.timer(() => this.retire(session, new BridgeError("timeout", "Authentication helper timed out; retry to reload stored credentials.")), Math.max(0, ms));
      session.pending.set(id, { method, resolve: value => resolve(value as Results[M]), reject, timer, clean: () => signal?.removeEventListener("abort", abort) });
      signal?.addEventListener("abort", abort, { once: true });
      try { session.child.stdin.write(frame); } catch { this.retire(session, new BridgeError("transport")); }
    });
  }
  private retire(session: Session, error: BridgeError): void {
    if (session.dead) return;
    session.dead = true;
    if (this.session === session) { this.session = undefined; this.ready = undefined; this.expiries.clear(); }
    for (const pending of session.pending.values()) { this.io.clearTimer(pending.timer); pending.clean(); pending.reject(error); }
    session.pending.clear(); session.bytes = Buffer.alloc(0);
    session.child.stdin.destroy(); session.child.stdout.destroy(); session.child.stderr.destroy();
    this.retirement = Promise.resolve().then(() => this.io.terminate(session.child)).catch(() => { this.cleanupFailed = true; });
  }
  async request<M extends Exclude<Method, "hello" | "shutdown">>(method: M, params: Params[M], signal?: AbortSignal): Promise<Results[M]> {
    if (signal?.aborted) throw new BridgeError("cancelled");
    const abortStartup = () => { if (this.session) this.retire(this.session, new BridgeError("cancelled")); };
    signal?.addEventListener("abort", abortStartup, { once: true });
    let session: Session;
    try { session = await this.ensure(signal); }
    finally { signal?.removeEventListener("abort", abortStartup); }
    const expiry = method === "login.wait" ? this.expiries.get((params as Params["login.wait"]).attempt_id) : undefined;
    if (method === "login.wait" && expiry === undefined) throw new BridgeError("invalid", "Start a login attempt before waiting for it.");
    const result = await this.send(session, method, params, method === "resolve" ? 180_000 : method === "login.wait" ? Math.max(0, expiry! * 1000 - this.io.now()) + 5_000 : 10_000, signal);
    if (method === "login.start" && !session.dead) { const login = result as Results["login.start"]; this.expiries.set(login.attempt_id, login.expires_at); }
    if (method === "login.wait" || method === "login.cancel") this.expiries.delete((params as Params["login.wait"]).attempt_id);
    return result;
  }
  dispose(): Promise<void> {
    if (this.closing) return this.closing;
    this.closed = true;
    this.closing = this.close(); return this.closing;
  }
  private async close(): Promise<void> {
    const session = this.session;
    if (!session) { await this.retirement; return; }
    await new Promise<void>(resolve => {
      const deadline = this.io.timer(() => { this.retire(session, new BridgeError("closed")); resolve(); }, 5_000);
      session.exited.then(() => { this.io.clearTimer(deadline); resolve(); });
      this.send(session, "shutdown", {}, 5_000).then(() => { session.child.stdin.end(); }, () => { session.child.stdin.end(); });
    });
    this.retire(session, new BridgeError("closed")); await this.retirement;
  }
}
