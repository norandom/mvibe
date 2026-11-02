import { test, expect } from "bun:test";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { StdioBridge, BridgeError } from "../src/bridge.js";
import type { BridgeRuntime } from "../src/bridge.js";
const audience = "https://inference.example.test";
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function fixture() {
  let now = 1_700_000_000_000, tick = 0, kills = 0, autoHello = true;
  const timers = new Map<number, { fn: () => void; at: number }>();
  const children: ReturnType<typeof child>[] = [], calls: unknown[] = [];
  function child() {
    const emitter = new EventEmitter(), stdout = new PassThrough(), stderr = new PassThrough();
    const requests: any[] = [];
    const stdin = new Writable({ write(data, _encoding, done) {
      const request = JSON.parse(data.toString()); requests.push(request);
      if (request.method === "hello" && autoHello) reply(request.id, { protocol: 1, api_version: "1.0.0", audience });
      done();
    } });
    function raw(value: unknown) { stdout.write(Buffer.from(JSON.stringify(value) + "\n")); }
    function reply(id: string, result: unknown) { raw({ version: 1, id, ok: true, result }); }
    return { process: Object.assign(emitter, { pid: 100, stdin, stdout, stderr, kill: () => true }), requests, raw, reply, stdout };
  }
  const runtime: BridgeRuntime = {
    spawn: (...args) => { calls.push(args); const c = child(); children.push(c); return c.process as unknown as ChildProcessWithoutNullStreams; },
    terminate: async () => { kills++; }, now: () => now,
    timer: (fn, ms) => { const id = ++tick; timers.set(id, { fn, at: now + ms }); return id as unknown as ReturnType<typeof setTimeout>; },
    clearTimer: id => { timers.delete(id as unknown as number); },
  };
  const bridge = new StdioBridge({ audience, command: { executable: "uv", args: ["run", "--offline", "--no-sync", "-m", "mvibe_auth", "bridge"] } }, runtime);
  const advance = async (ms: number) => { now += ms; for (const [id, t] of [...timers]) if (t.at <= now) { timers.delete(id); t.fn(); } await flush(); };
  const start = async () => { const p = bridge.request("status", {}); await flush(); const c = children.at(-1)!; c.reply(c.requests.at(-1).id, { signed_in: false, config_match: true }); await p; return c; };
  return { bridge, runtime, children, calls, advance, start, setHello: (value: boolean) => { autoHello = value; }, get kills() { return kills; } };
}
test("lazy installed argv helper validates hello and splits frames", async () => {
  const f = fixture(); expect(f.children).toHaveLength(0);
  const c = await f.start(); expect(f.calls).toHaveLength(1);
  expect((f.calls[0] as any[])[2]).toMatchObject({ shell: false, windowsHide: true });
  const p = f.bridge.request("status", {}); await flush();
  const frame = Buffer.from(JSON.stringify({ version: 1, id: c.requests.at(-1).id, ok: true, result: { signed_in: true, config_match: true } }) + "\n");
  c.stdout.write(frame.subarray(0, 8)); c.stdout.write(frame.subarray(8));
  expect(await p).toEqual({ signed_in: true, config_match: true });
});
test("setup missing runtime and bare Python are actionable", async () => {
  for (const command of [undefined, { executable: "python", args: [] }, { executable: "py", args: [] }]) {
    const bridge = new StdioBridge({ audience, ...(command ? { command } : {}) }, fixture().runtime);
    await expect(bridge.request("status", {})).rejects.toMatchObject({ code: "setup" });
  }
});
test("handshake has ten second deadline", async () => {
  const f = fixture(); f.setHello(false); const p = f.bridge.request("status", {}).catch(e => e); await flush();
  await f.advance(9999); expect(f.kills).toBe(0); await f.advance(1);
  expect(await p).toMatchObject({ code: "timeout" }); expect(f.kills).toBe(1);
});
for (const invalid of ["wrong-version", "wrong-api", "wrong-audience", "unknown-id", "bad-result", "malformed", "oversize", "duplicate"]) test("safe retirement: " + invalid, async () => {
  const f = fixture(); if (invalid.startsWith("wrong")) f.setHello(false);
  const p = f.bridge.request("status", {}).catch(e => e); await flush();
  const c = f.children[0]!, id = c.requests.at(-1).id;
  if (invalid === "wrong-version") c.raw({ version: 2, id, ok: true, result: {} });
  else if (invalid === "wrong-api" || invalid === "wrong-audience") c.reply(id, { protocol: 1, api_version: invalid === "wrong-api" ? "4.0.0" : "1.0.0", audience: invalid === "wrong-audience" ? "https://wrong.test" : audience });
  else if (invalid === "unknown-id") c.reply("unknown", {});
  else if (invalid === "bad-result") c.reply(id, { headers: { authorization: "synthetic-secret" } });
  else if (invalid === "malformed") c.stdout.write("{synthetic-secret\n");
  else if (invalid === "oversize") c.stdout.write(Buffer.alloc(1_048_577, 32));
  else { c.reply(id, { signed_in: false, config_match: true }); c.reply(id, { signed_in: false, config_match: true }); }
  const result = await p; await flush();
  if (invalid !== "duplicate") { expect(result).toBeInstanceOf(BridgeError); expect(String(result)).not.toContain("synthetic-secret"); }
  expect(f.kills).toBe(1);
});
test("resolve timeout rejects siblings; lazy restart never starts login", async () => {
  const f = fixture(); const c = await f.start();
  const resolve = f.bridge.request("resolve", { target_url: audience }).catch(e => e); await flush(); await f.advance(175000);
  const sibling = f.bridge.request("status", {}).catch(e => e); await flush();
  await f.advance(4999); expect(f.kills).toBe(0); await f.advance(1);
  expect(await resolve).toMatchObject({ code: "timeout" }); expect(await sibling).toMatchObject({ code: "timeout" });
  expect(f.children).toHaveLength(1); await f.start(); expect(f.children).toHaveLength(2);
  expect(c.requests.map(r => r.method)).not.toContain("login.start");
});
test("resolve abort retires owned helper and rejects pending", async () => {
  const f = fixture(); await f.start(); const controller = new AbortController();
  const a = f.bridge.request("resolve", { target_url: audience }, controller.signal).catch(e => e);
  const b = f.bridge.request("status", {}).catch(e => e); await flush(); controller.abort();
  expect(await a).toMatchObject({ code: "cancelled" }); expect(await b).toMatchObject({ code: "cancelled" }); expect(f.kills).toBe(1);
});
test("reserve cancellation slot within 32 active requests", async () => {
  const f = fixture(); const c = await f.start();
  const pending = Array.from({ length: 31 }, () => f.bridge.request("status", {}).catch(e => e)); await flush();
  await expect(f.bridge.request("status", {})).rejects.toMatchObject({ code: "busy" });
  const cancel = f.bridge.request("login.cancel", { attempt_id: "a" }).catch(e => e); await flush(); expect(c.requests.at(-1).method).toBe("login.cancel");
  const close = f.bridge.dispose(); await f.advance(5000); await close; await Promise.all([...pending, cancel]);
});
test("login wait deadline remaining expiry plus five seconds", async () => {
  const f = fixture(); const c = await f.start();
  const start = f.bridge.request("login.start", { force: false }); await flush();
  c.reply(c.requests.at(-1).id, { attempt_id: "a", authorization_url: "https://identity.test/auth", expires_at: 1_700_000_020 }); await start;
  await f.advance(10000);
  const wait = f.bridge.request("login.wait", { attempt_id: "a" }).catch(e => e); await flush();
  await f.advance(14999); expect(f.kills).toBe(0); await f.advance(1); expect(await wait).toMatchObject({ code: "timeout" });
});
test("dispose preserves graceful drain until exit or five-second bound", async () => {
  for (const graceful of [true, false]) {
    const f = fixture(); const c = await f.start(); const close = f.bridge.dispose(); await flush();
    expect(c.requests.at(-1).method).toBe("shutdown"); await f.advance(4999); expect(f.kills).toBe(0);
    if (graceful) { c.reply(c.requests.at(-1).id, { closed: true }); c.process.emit("exit", 0); } else await f.advance(1);
    await close; expect(f.kills).toBe(1); expect(f.bridge.dispose()).toBe(close);
    await expect(f.bridge.request("status", {})).rejects.toMatchObject({ code: "closed" });
  }
});
test("child errors and exit are sanitized", async () => {
  for (const event of ["error", "exit"]) {
    const f = fixture(); await f.start(); const pending = f.bridge.request("status", {}).catch(e => e); await flush();
    f.children[0]!.process.emit(event, new Error("synthetic-secret"));
    const result = await pending; expect(result).toBeInstanceOf(BridgeError); expect(String(result)).not.toContain("synthetic-secret");
  }
});

test("out of order IDs and safe remote errors preserve method correlation", async () => {
  const f = fixture(); const c = await f.start();
  const a = f.bridge.request("status", {}), b = f.bridge.request("resolve", { target_url: audience }).catch(e => e); await flush();
  const [status, resolve] = c.requests.slice(-2);
  c.raw({ version: 1, id: resolve.id, ok: false, error: { code: "transient", message: "synthetic-secret traceback", retryable: true } });
  c.reply(status.id, { signed_in: true, config_match: true });
  expect(await a).toMatchObject({ signed_in: true });
  const error = await b; expect(error).toMatchObject({ code: "transient" }); expect(String(error)).not.toContain("synthetic-secret"); expect(f.kills).toBe(0);
});
test("failed owned-tree cleanup prevents replacement helper", async () => {
  const f = fixture(); f.runtime.terminate = async () => { throw new Error("synthetic-secret"); }; await f.start();
  const controller = new AbortController(); const p = f.bridge.request("resolve", { target_url: audience }, controller.signal).catch(e => e);
  await flush(); controller.abort(); await p; await flush();
  await expect(f.bridge.request("status", {})).rejects.toMatchObject({ code: "cleanup" }); expect(f.children).toHaveLength(1);
});
test("uv requests cannot trigger online installation or synchronization", async () => {
  for (const args of [["run", "-m", "mvibe_auth", "bridge"], ["run", "--offline", "-m", "mvibe_auth", "bridge"]]) {
    const f = fixture(); const bridge = new StdioBridge({ audience, command: { executable: "uv", args } }, f.runtime);
    await expect(bridge.request("status", {})).rejects.toMatchObject({ code: "setup" }); expect(f.children).toHaveLength(0);
  }
});
test("resolve abort during handshake retires without waiting ten seconds", async () => {
  const f = fixture(); f.setHello(false); const controller = new AbortController();
  const p = f.bridge.request("resolve", { target_url: audience }, controller.signal).catch(e => e); await flush();
  controller.abort(); expect(await p).toMatchObject({ code: "cancelled" }); await flush(); expect(f.kills).toBe(1);
});
test("already-aborted request never spawns helper", async () => {
  const f = fixture(), controller = new AbortController(); controller.abort();
  await expect(f.bridge.request("resolve", { target_url: audience }, controller.signal)).rejects.toMatchObject({ code: "cancelled" });
  expect(f.children).toHaveLength(0);
});
test("invalid and oversized outbound frames never reach the child", async () => {
  const f = fixture(); const c = await f.start(); const before = c.requests.length;
  await expect(f.bridge.request("resolve", { target_url: "x".repeat(1_048_576) })).rejects.toMatchObject({ code: "invalid_frame" });
  const circular: Record<string, unknown> = {}; circular.self = circular;
  await expect(f.bridge.request("status", circular as Record<string, never>)).rejects.toMatchObject({ code: "invalid_frame" });
  expect(c.requests).toHaveLength(before);
});
