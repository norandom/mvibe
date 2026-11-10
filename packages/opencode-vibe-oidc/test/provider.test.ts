import { test, expect, spyOn } from "bun:test";
import { createProviderFetch, providerBaseURL } from "../src/provider.js";
import type { StdioBridge } from "../src/bridge.js";
import { BridgeError } from "../src/bridge.js";
import type { Results } from "../src/protocol.js";
import { createHttpCapture } from "../src/http-capture.js";
import { REQUEST_CONTEXT_HEADER } from "../src/request-context.js";
import { parseRateLimitOptions, UpstreamGate, type GateDeps } from "../src/rate-limit.js";
const audience = "https://inference.example.test";
const url = audience + "/v1/chat/completions?q=&q=two";
type Gate = Pick<UpstreamGate, "run">;
function fixture({ gate, diagnostics = false, now }: { gate?: Gate; diagnostics?: boolean; now?: () => number } = {}) {
  const resolved: string[] = [], sent: Request[] = [], times: number[] = [];
  let snapshot: ((target: string, signal?: AbortSignal) => Promise<Results["resolve"]>) = async target => ({ target, headers: { Authorization: `Bearer synthetic-${resolved.length}`, "x-external-organization-id": "org", "x-external-workspace-id": "space", "x-evil": "exclude" }, expires_at: Date.now() / 1000 + 300, revision: String(resolved.length), metadata: { call_source: "vibe_code" } });
  const bridge = { request: async (method: string, params: { target_url: string }, signal?: AbortSignal) => { expect(method).toBe("resolve"); resolved.push(params.target_url); return snapshot(params.target_url, signal); } } as Pick<StdioBridge, "request">;
  let response = new Response("unauthorized", { status: 401 });
  // Scripted answers are used one per send, before the fixed response above.
  const answers: (() => Response)[] = [];
  const fetch = createProviderFetch(bridge, audience, async (input, init) => {
    sent.push(new Request(input, init));
    if (now !== undefined) times.push(now());
    // A gated send suspends a macrotask turn, like an upstream round trip.
    if (gate !== undefined) await tick();
    return answers.shift()?.() ?? response;
  }, { ...(gate === undefined ? {} : { gate }), ...(diagnostics ? { diagnostics } : {}) });
  return { fetch, resolved, sent, times, snapshot: (value: typeof snapshot) => { snapshot = value; }, response: (value: Response) => { response = value; }, script: (...steps: (() => Response)[]) => { answers.push(...steps); } };
}
test("per-fetch rotation replaces caller credentials and preserves tool payload, method, query and response", async () => {
  const f = fixture(); const body = '{ "tools": [{"function":{"arguments":"{\\"x\\":1}"}}], "stream":true }';
  for (let i = 1; i <= 2; i++) {
    const result = await f.fetch(new Request(url, { method: "POST", body, headers: { Authorization: "Bearer connection-marker", "x-api-key": "caller-key", "api-key": "caller-key", "x-external-workspace-id": "caller", "content-type": "application/json" } }));
    expect(result.status).toBe(401);
    const request = f.sent.at(-1)!;
    expect(request.url).toBe(url); expect(request.method).toBe("POST"); expect(await request.json()).toEqual({ ...JSON.parse(body), metadata: { call_source: "vibe_code" } });
    expect(request.headers.get("authorization")).toBe(`Bearer synthetic-${i}`);
    expect(request.headers.get("x-external-workspace-id")).toBe("space");
    for (const name of ["x-api-key", "api-key", "x-evil"]) expect(request.headers.has(name)).toBe(false);
    expect(request.redirect).toBe("manual");
  }
  expect(f.resolved).toEqual([url, url]); expect(f.sent).toHaveLength(2);
});
test.each(["User-Agent", "user-agent", "USER-AGENT"])("shared %s metadata replaces caller identity without broadening helper headers", async name => {
  const f = fixture();
  const agent = "mistral-client-python/Mistral-Vibe/3.0.4 mvibe-auth/0.1.0";
  f.snapshot(async target => ({ target, headers: {
    Authorization: "Bearer synthetic-id", "x-external-organization-id": "org", "x-external-workspace-id": "space",
    [name]: agent, "x-evil": "exclude", "call_source": "exclude", "x-affinity": "exclude", "content-type": "evil/type",
  }, expires_at: Date.now()/1000+300, revision: "1", metadata: { call_source: "vibe_code" } }));
  const body = '{ "messages":[], "stream":true }';
  await f.fetch(url, { method: "POST", body, headers: {
    "user-agent": "caller-agent", Authorization: "Bearer caller", "x-external-organization-id": "caller-org",
    "x-external-workspace-id": "caller-space", "content-type": "application/json",
  } });
  const request = f.sent[0]!;
  expect(request.headers.get("user-agent")).toBe(agent);
  expect(request.headers.get("authorization")).toBe("Bearer synthetic-id");
  expect(request.headers.get("x-external-organization-id")).toBe("org");
  expect(request.headers.get("x-external-workspace-id")).toBe("space");
  for (const key of ["x-evil", "call_source", "x-affinity"]) expect(request.headers.has(key)).toBe(false);
  expect(request.headers.get("content-type")).toBe("application/json");
  expect(await request.json()).toEqual({ ...JSON.parse(body), metadata: { call_source: "vibe_code" } });
  expect(request.redirect).toBe("manual");
  expect(f.sent).toHaveLength(1);
});
test("helper snapshots without client metadata preserve caller User-Agent", async () => {
  const f = fixture();
  await f.fetch(url, { headers: { "User-Agent": "caller-agent" } });
  expect(f.sent[0]!.headers.get("user-agent")).toBe("caller-agent");
});
test.each(["https://evil.test/v1/models", audience + "/v1/users/me", audience + "/v1/chat/completions/../users", audience + "/v1/models#fragment", "https://user:pass@inference.example.test/v1/models"])("policy rejects before resolving %s", async target => {
 const f = fixture(); await expect(f.fetch(target)).rejects.toBeInstanceOf(BridgeError); expect(f.resolved).toHaveLength(0); expect(f.sent).toHaveLength(0);
});
test("snapshot target mismatch, expired metadata and helper retirement fail without send/replay", async () => {
 for (const mode of ["target", "expired", "retired"]) {
  const f = fixture(); f.snapshot(async target => { if (mode === "retired") throw new BridgeError("exit"); return { target: mode === "target" ? target + "x" : target, headers: { Authorization: "Bearer synthetic" }, expires_at: mode === "expired" ? 0 : Date.now()/1000+300, revision: "1" }; });
  await expect(f.fetch(url)).rejects.toBeInstanceOf(BridgeError); expect(f.sent).toHaveLength(0); expect(f.resolved).toHaveLength(1);
 }
});
test("abort before and during resolve prevents transport", async () => {
 const f = fixture(); const controller = new AbortController(); controller.abort();
 await expect(f.fetch(url, { signal: controller.signal })).rejects.toBeInstanceOf(BridgeError); expect(f.resolved).toHaveLength(0);
 const active = new AbortController(); f.snapshot(async target => { active.abort(); return { target, headers: {}, expires_at: Date.now()/1000+300, revision: "1" }; });
 await expect(f.fetch(url, { signal: active.signal })).rejects.toBeInstanceOf(BridgeError); expect(f.sent).toHaveLength(0);
});
test("stream response is returned without buffering; signal reaches send and body cancellation closes source", async () => {
 const f = fixture(); let cancelled = false;
 const response = new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode("data: tool-delta\n\n")); }, cancel() { cancelled = true; } }), { headers: { "content-type": "text/event-stream" } });
 f.response(response); const controller = new AbortController();
 const result = await f.fetch(url, { signal: controller.signal }); expect(result.body).toBe(response.body);
 const reader = result.body!.getReader(); expect(new TextDecoder().decode((await reader.read()).value)).toContain("tool-delta");
 controller.abort(); expect(f.sent[0]!.signal.aborted).toBe(true); await reader.cancel(); expect(cancelled).toBe(true);
});

test("redirect and transport failure each send once without replay", async () => {
 const f = fixture(); const redirect = new Response(null, { status: 307, headers: { location: "https://evil.test/" } }); f.response(redirect);
 const result = await f.fetch(url); expect(result.status).toBe(307); expect(result.headers.get("location")).toBe("https://evil.test/"); expect(f.sent).toHaveLength(1); expect(f.resolved).toHaveLength(1);
 let calls = 0;
 const bridge = { request: async () => ({ target: url, headers: { Authorization: "Bearer synthetic" }, expires_at: Date.now()/1000+300, revision: "1" }) } as Pick<StdioBridge, "request">;
 const fetch = createProviderFetch(bridge, audience, async () => { calls++; throw new Error("SECRET transport detail"); });
 await expect(fetch(url)).rejects.toMatchObject({ code: "request" }); expect(calls).toBe(1);
});

test.each([401, 429, 503])("native host retry control preserves upstream %s status and payload", async status => {
 const f = fixture(); const payload = '{"error":{"message":"synthetic upstream rejection"}}';
 const upstream = new Response(payload, { status, headers: { "content-type": "application/json", "x-should-retry": "true" } });
 f.response(upstream);
 const result = await f.fetch(url);
 expect(result.status).toBe(status); expect(result.headers.get("x-should-retry")).toBe("false");
 expect(result.headers.get("content-type")).toBe("application/json"); expect(await result.text()).toBe(payload);
 expect(upstream.headers.get("x-should-retry")).toBe("true"); expect(f.sent).toHaveLength(1);
});

test("abort reaches a pending resolve and an in-flight transport", async () => {
 const f = fixture(); const controller = new AbortController();
 f.snapshot(async (_target, signal) => new Promise((_resolve, reject) => { expect(signal).toBeDefined(); signal!.addEventListener("abort", () => reject(new BridgeError("cancelled")), { once: true }); controller.abort(); }));
 await expect(f.fetch(url, { signal: controller.signal })).rejects.toMatchObject({ code: "cancelled" }); expect(f.sent).toHaveLength(0);
 const active = new AbortController(); let calls = 0;
 const bridge = { request: async () => ({ target: url, headers: {}, expires_at: Date.now()/1000+300, revision: "1" }) } as Pick<StdioBridge, "request">;
 const fetch = createProviderFetch(bridge, audience, async input => new Promise((_resolve, reject) => { calls++; const request = input as Request; request.signal.addEventListener("abort", () => reject(new DOMException("Abort", "AbortError")), { once: true }); active.abort(); }));
 await expect(fetch(url, { signal: active.signal })).rejects.toBeInstanceOf(BridgeError); expect(calls).toBe(1);
});

test("canonical URL and audience prefix are checked before resolving", async () => {
 const f = fixture(); await f.fetch("https://INFERENCE.example.test:443/v1/models?blank=&again=x&again=y");
 expect(f.resolved).toEqual([audience + "/v1/models?blank=&again=x&again=y"]);
 expect(providerBaseURL("http://127.0.0.1:1234/base/")).toBe("http://127.0.0.1:1234/base/v1");
 for (const invalid of ["http://remote.test", "https://user@remote.test", "https://remote.test/?key=x", "not-a-url"]) expect(() => providerBaseURL(invalid)).toThrow(BridgeError);
});

// --- re-sendable attempts and gate wiring (tasks 4.1, 4.2) -----------------------

const CHAT_BODY = '{"model":"mistral-large-latest","messages":[{"role":"user","content":"secret-prompt"}],"stream":true,"metadata":{"custom":"kept","user_plan":"Pro"}}';
const RATE_LIMITED_BODY = '{"object":"error","message":"Rate limit exceeded","type":"rate_limited"}';
const EXHAUSTED_HINT = "Upstream rate limit persisted beyond rateLimit.maxWaitMs or rateLimit.maxQueue; lower session concurrency or configure rateLimit.requestsPerSecond.";
const T0 = 1_700_000_000_000;
type Sleep = NonNullable<GateDeps["sleep"]>;
type Row = Record<string, unknown>;

/** One macrotask turn: every pending microtask runs first. */
function tick(): Promise<void> {
  return new Promise(resolve => setImmediate(() => resolve()));
}

/** Bound an await on wall-clock time, so a scheduling regression fails instead of hanging. */
async function bounded<T>(promise: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error("the fetch did not settle within 5 s")), 5000);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

/** Mark a fetch as handled at once, so one rejected before the test awaits it is never unhandled. */
function handled<T>(promise: Promise<T>): Promise<T> {
  promise.catch(() => undefined);
  return promise;
}

async function failureOf(promise: Promise<unknown>): Promise<unknown> {
  return bounded(promise).then(() => undefined, (caught: unknown) => caught);
}

/** A fake monotonic clock (as in test/rate-limit.test.ts): its sleep advances the clock by
 * exactly the delay asked for, records it and yields a macrotask turn. */
class Clock {
  time = T0;
  readonly sleeps: number[] = [];
  readonly now = (): number => this.time;
  readonly sleep: Sleep = async ms => {
    this.sleeps.push(ms);
    this.time += ms;
    await tick();
  };
}

/** A sleep that stays pending until its signal aborts, then rejects with BridgeError("cancelled")
 * as the gate's default sleep does. `started` settles when the first sleep begins. */
class PendingSleep {
  readonly calls: number[] = [];
  #begin: () => void = () => undefined;
  readonly started = new Promise<void>(resolve => { this.#begin = () => resolve(); });
  readonly sleep: Sleep = (ms, signal) => new Promise((_resolve, reject) => {
    this.calls.push(ms);
    if (signal?.aborted) { reject(new BridgeError("cancelled")); return; }
    signal?.addEventListener("abort", () => reject(new BridgeError("cancelled")), { once: true });
    this.#begin();
  });
}

/** A real UpstreamGate on the fake clock; a jitter draw of 0.5 makes every backoff exactly its base. */
function gateOn(clock: Clock, raw: unknown = undefined, sleep: Sleep = clock.sleep): UpstreamGate {
  return new UpstreamGate(parseRateLimitOptions(raw), { now: clock.now, wallNow: clock.now, sleep, random: () => 0.5 });
}

/** A gate stand-in that sends every request twice and answers with the second response. */
function twice(): { gate: Gate; keys: string[]; signals: (AbortSignal | undefined)[] } {
  const keys: string[] = [], signals: (AbortSignal | undefined)[] = [];
  const gate: Gate = { run: async (key, attempt, signal) => {
    keys.push(key); signals.push(signal);
    await (await attempt()).body?.cancel();
    return attempt();
  } };
  return { gate, keys, signals };
}

/** A gate stand-in that sends once and records the key it was given. */
function keyed(): { gate: Gate; keys: string[] } {
  const keys: string[] = [];
  return { gate: { run: async (key, attempt) => { keys.push(key); return attempt(); } }, keys };
}

function ok(): Response {
  return new Response('{"ok":true}', { status: 200, headers: { "content-type": "application/json" } });
}

/** A Mistral-shaped upstream 429. */
function rejection(headers: Record<string, string> = {}): Response {
  return new Response(RATE_LIMITED_BODY, { status: 429, headers: { "content-type": "application/json", ...headers } });
}

async function bytesOf(request: Request): Promise<Uint8Array> {
  return new Uint8Array(await request.arrayBuffer());
}

function rowsOf(stderr: { mock: { calls: unknown[][] } }): Row[] {
  return stderr.mock.calls.map(call => JSON.parse(String(call[0])) as Row);
}

function chat(init: RequestInit = {}): RequestInit {
  return { method: "POST", body: CHAT_BODY, ...init, headers: { "content-type": "application/json", ...init.headers } };
}

test("each attempt resolves a fresh snapshot and re-sends the chat bytes attributed once (4.1, 4.6)", async () => {
  const stub = twice(); const f = fixture({ gate: stub.gate });
  // The snapshot rotates per resolve; only the first one's metadata may reach the body.
  f.snapshot(async target => ({ target, headers: { Authorization: `Bearer synthetic-${f.resolved.length}`, "x-external-workspace-id": "space", "User-Agent": `agent-${f.resolved.length}` }, expires_at: Date.now() / 1000 + 300, revision: String(f.resolved.length), metadata: { call_source: "vibe_code", attempt: String(f.resolved.length) } }));
  f.script(() => rejection(), ok);
  const controller = new AbortController();
  const result = await bounded(f.fetch(url, chat({ signal: controller.signal, headers: { Authorization: "Bearer caller", "x-api-key": "caller-key", "content-length": "999", [REQUEST_CONTEXT_HEADER]: '{"session_id":"s"}' } })));
  expect(result.status).toBe(200); expect(result.headers.get("x-should-retry")).toBe("false"); expect(await result.text()).toBe('{"ok":true}');
  expect(f.resolved).toEqual([url, url]); expect(f.sent).toHaveLength(2);
  const expected = new TextEncoder().encode(JSON.stringify({ ...JSON.parse(CHAT_BODY), metadata: { custom: "kept", call_source: "vibe_code", attempt: "1" } }));
  const first = await bytesOf(f.sent[0]!), second = await bytesOf(f.sent[1]!);
  expect(first).toEqual(expected); expect(second).toEqual(first);
  expect(f.sent.map(request => request.headers.get("authorization"))).toEqual(["Bearer synthetic-1", "Bearer synthetic-2"]);
  expect(f.sent.map(request => request.headers.get("user-agent"))).toEqual(["agent-1", "agent-2"]);
  for (const request of f.sent) {
    expect(request.url).toBe(url); expect(request.method).toBe("POST"); expect(request.redirect).toBe("manual");
    expect(request.headers.get("content-type")).toBe("application/json");
    for (const name of ["content-length", "x-api-key", REQUEST_CONTEXT_HEADER]) expect(request.headers.has(name)).toBe(false);
    expect(request.signal.aborted).toBe(false);
  }
  expect(stub.keys).toEqual(["mistral-large-latest"]);
  controller.abort();
  expect(stub.signals[0]?.aborted).toBe(true);
  for (const request of f.sent) expect(request.signal.aborted).toBe(true);
});

test("a second attempt sends the caller's original embeddings bytes unchanged (4.1)", async () => {
  const stub = twice(); const f = fixture({ gate: stub.gate });
  f.script(ok, ok);
  // Not valid UTF-8: a decode and re-encode would change these bytes.
  const original = new Uint8Array([0x7b, 0x22, 0x69, 0x6e, 0x22, 0x3a, 0x22, 0xff, 0xfe, 0x00, 0xc3, 0x22, 0x7d]);
  const result = await bounded(f.fetch(audience + "/v1/embeddings", { method: "POST", body: original, headers: { "content-type": "application/json", Authorization: "Bearer caller" } }));
  expect(result.status).toBe(200); expect(result.headers.get("x-should-retry")).toBe("false");
  expect(f.resolved).toHaveLength(2); expect(f.sent).toHaveLength(2);
  for (const request of f.sent) {
    expect(await bytesOf(request)).toEqual(original);
    expect(request.method).toBe("POST"); expect(request.redirect).toBe("manual");
    expect(request.headers.get("content-type")).toBe("application/json");
  }
  expect(f.sent.map(request => request.headers.get("authorization"))).toEqual(["Bearer synthetic-1", "Bearer synthetic-2"]);
  expect(stub.keys).toEqual(["embeddings"]);
});

const ORIGINAL_CHAT_BODIES: [name: string, body: Uint8Array<ArrayBuffer>, key: string][] = [
  ["invalid JSON", new Uint8Array([0x7b, 0x22, 0x6d, 0xff, 0x22]), "default"],
  ["a JSON array", new TextEncoder().encode('[{"model":"m"}]'), "default"],
  ["array metadata", new TextEncoder().encode('{"model":"m","metadata":[1]}'), "m"],
];
test.each(ORIGINAL_CHAT_BODIES)("a chat body with %s keeps its original bytes on every attempt", async (_name, original, key) => {
  const stub = twice(); const f = fixture({ gate: stub.gate });
  f.script(ok, ok);
  await bounded(f.fetch(url, { method: "POST", body: original }));
  expect(f.sent).toHaveLength(2);
  for (const request of f.sent) expect(await bytesOf(request)).toEqual(original);
  expect(stub.keys).toEqual([key]);
});

test("a bodiless GET is rebuilt without a body on every attempt", async () => {
  const stub = twice(); const f = fixture({ gate: stub.gate });
  f.script(ok, ok);
  await bounded(f.fetch(audience + "/v1/models", { headers: { "User-Agent": "caller-agent" } }));
  expect(f.sent).toHaveLength(2); expect(f.resolved).toHaveLength(2);
  for (const request of f.sent) {
    expect(request.method).toBe("GET"); expect(request.body).toBeNull(); expect(request.redirect).toBe("manual");
    expect(request.headers.get("user-agent")).toBe("caller-agent");
  }
  expect(stub.keys).toEqual(["models"]);
});

test("capture writes one request record per attempt, each with the same attributed body", async () => {
  const records: Row[] = [];
  const capture = createHttpCapture({ captureDirectory: process.cwd(), captureBody: true }, (_name, data) => { records.push(JSON.parse(data) as Row); });
  let resolves = 0;
  const bridge = { request: async (_method: string, params: { target_url: string }) => {
    resolves++;
    return { target: params.target_url, headers: { Authorization: `Bearer opaque-credential-${resolves}` }, expires_at: Date.now() / 1000 + 300, revision: "1", metadata: { call_source: "vibe_code" } };
  } } as Pick<StdioBridge, "request">;
  const answers = [rejection(), ok()];
  const fetch = createProviderFetch(bridge, audience, async () => answers.shift()!, { capture, gate: twice().gate });
  expect((await bounded(fetch(url, chat()))).status).toBe(200);
  expect(records.map(record => record.event)).toEqual(["request", "response", "request", "response"]);
  expect(records[1]!.request_id).toBe(records[0]!.request_id); expect(records[3]!.request_id).toBe(records[2]!.request_id);
  expect(records[2]!.request_id).not.toBe(records[0]!.request_id);
  expect(records[1]!.status).toBe(429); expect(records[3]!.status).toBe(200);
  expect(records[0]!.body).toMatchObject({ model: "mistral-large-latest", metadata: { custom: "kept", call_source: "vibe_code" } });
  expect(records[2]!.body).toEqual(records[0]!.body);
  expect(JSON.stringify(records)).not.toContain("opaque-credential");
});

test("a gated fetch holds an upstream 429 and resolves with the re-sent 200 (4.1, 4.6, 5.1)", async () => {
  const clock = new Clock(); const f = fixture({ gate: gateOn(clock), diagnostics: true, now: clock.now });
  f.script(() => rejection(), ok);
  const stderr = spyOn(console, "error").mockImplementation(() => {});
  try {
    const result = await bounded(f.fetch(url, chat()));
    expect(result.status).toBe(200); expect(result.headers.get("x-should-retry")).toBe("false"); expect(await result.text()).toBe('{"ok":true}');
    expect(f.sent).toHaveLength(2); expect(f.resolved).toEqual([url, url]);
    expect(f.times).toEqual([T0, T0 + 1000]); expect(clock.sleeps).toEqual([1000]);
    expect(await bytesOf(f.sent[1]!)).toEqual(await bytesOf(f.sent[0]!));
    expect(f.sent.map(request => request.headers.get("authorization"))).toEqual(["Bearer synthetic-1", "Bearer synthetic-2"]);
    const rows = rowsOf(stderr);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ event: "provider_request", endpoint: "chat/completions", model: "mistral-large-latest", phase: "response_headers", outcome: "headers_received", status: 200, queued_ms: 1000, rate_limit_rejections: 1 });
    expect(JSON.stringify(rows)).not.toMatch(/Rate limit exceeded|secret-prompt|Bearer|x-ratelimit/);
  } finally { stderr.mockRestore(); }
});

const HELD_REQUESTS: [name: string, target: string, init: RequestInit, endpoint: string][] = [
  ["chat", url, chat(), "chat/completions"],
  ["discovery", audience + "/v1/models", { method: "GET" }, "models"],
];
test.each(HELD_REQUESTS)("aborting a held %s request rejects as cancelled and never re-sends (4.2, 4.5)", async (_name, target, init, endpoint) => {
  const held = new PendingSleep(); const f = fixture({ gate: gateOn(new Clock(), undefined, held.sleep), diagnostics: true });
  f.script(() => rejection());
  const stderr = spyOn(console, "error").mockImplementation(() => {});
  try {
    const controller = new AbortController();
    const pending = handled(f.fetch(target, { ...init, signal: controller.signal }));
    await bounded(held.started);
    expect(f.sent).toHaveLength(1);
    controller.abort();
    const error = await failureOf(pending);
    expect(error).toBeInstanceOf(BridgeError); expect(error).toMatchObject({ code: "cancelled" });
    await tick();
    expect(f.sent).toHaveLength(1); expect(f.resolved).toHaveLength(1); expect(held.calls).toEqual([1000]);
    const rows = rowsOf(stderr);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ endpoint, outcome: "cancelled", status: null, rate_limit_rejections: 1 });
    expect(typeof rows[0]!.queued_ms).toBe("number");
  } finally { stderr.mockRestore(); }
});

test("exhaustion returns the upstream 429 with its own body and headers plus x-should-retry false (4.3, 4.4, 5.2)", async () => {
  const clock = new Clock(); const f = fixture({ gate: gateOn(clock, { maxWaitMs: 1000 }), diagnostics: true, now: clock.now });
  const limits = { "retry-after": "30", "x-ratelimit-limit-req-minute": "5", "x-ratelimit-remaining-req-minute": "0" };
  f.script(() => rejection(limits));
  const stderr = spyOn(console, "error").mockImplementation(() => {});
  try {
    const result = await bounded(f.fetch(url, chat()));
    expect(result.status).toBe(429); expect(result.headers.get("x-should-retry")).toBe("false");
    expect(result.headers.get("content-type")).toBe("application/json");
    for (const [name, value] of Object.entries(limits)) expect(result.headers.get(name)).toBe(value);
    expect(await result.text()).toBe(RATE_LIMITED_BODY);
    expect(f.sent).toHaveLength(1); expect(f.resolved).toHaveLength(1); expect(clock.sleeps).toEqual([]);
    const rows = rowsOf(stderr);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ phase: "response_headers", outcome: "rate_limited", status: 429, queued_ms: 0, rate_limit_rejections: 1, hint: EXHAUSTED_HINT });
    for (const setting of ["rateLimit.maxWaitMs", "rateLimit.maxQueue", "rateLimit.requestsPerSecond"]) expect(String(rows[0]!.hint)).toContain(setting);
    expect(JSON.stringify(rows)).not.toMatch(/Rate limit exceeded|x-ratelimit|secret-prompt/);
  } finally { stderr.mockRestore(); }
});

test("a full gate answers its own 429 without resolving or sending, still with x-should-retry false (4.3)", async () => {
  const held = new PendingSleep(); const f = fixture({ gate: gateOn(new Clock(), { maxQueue: 1 }, held.sleep), diagnostics: true });
  f.script(() => rejection());
  const stderr = spyOn(console, "error").mockImplementation(() => {});
  try {
    const controller = new AbortController();
    const first = handled(f.fetch(url, chat({ signal: controller.signal })));
    await bounded(held.started);
    const result = await bounded(f.fetch(url, chat()));
    expect(result.status).toBe(429); expect(result.headers.get("x-should-retry")).toBe("false");
    expect(result.headers.get("retry-after")).toBe("1");
    expect(JSON.parse(await result.text())).toMatchObject({ error: { type: "rate_limit_error", code: "rate_limited" } });
    expect(f.resolved).toHaveLength(1); expect(f.sent).toHaveLength(1);
    expect(rowsOf(stderr)).toEqual([expect.objectContaining({ outcome: "rate_limited", status: 429, hint: EXHAUSTED_HINT, queued_ms: 0, rate_limit_rejections: 0 })]);
    controller.abort();
    expect(await failureOf(first)).toMatchObject({ code: "cancelled" });
    expect(f.sent).toHaveLength(1);
  } finally { stderr.mockRestore(); }
});

test("discovery GET /v1/models is never paced while chat requests start 2000 ms apart (3.1, 3.5, 4.5)", async () => {
  const clock = new Clock(); const f = fixture({ gate: gateOn(clock, { requestsPerSecond: 0.5 }), now: clock.now });
  f.script(ok, ok, ok, ok, ok);
  const models = audience + "/v1/models";
  const discovered = await bounded(Promise.all([f.fetch(models), f.fetch(models)]));
  expect(discovered.map(response => response.status)).toEqual([200, 200]);
  expect(f.times).toEqual([T0, T0]); expect(clock.sleeps).toEqual([]);
  const answered = await bounded(Promise.all([f.fetch(url, chat()), f.fetch(url, chat())]));
  expect(answered.map(response => response.status)).toEqual([200, 200]);
  expect(f.times.slice(2)).toEqual([T0, T0 + 2000]); expect(clock.sleeps).toEqual([2000]);
  await bounded(f.fetch(models));
  expect(f.times.at(-1)).toBe(T0 + 2000); expect(clock.sleeps).toEqual([2000]);
});

const GATE_KEYS: [name: string, target: string, body: string | undefined, key: string][] = [
  ["a chat model", url, '{"model":"mistral-large-latest"}', "mistral-large-latest"],
  ["control and format characters dropped", url, JSON.stringify({ model: "mistral-\u0000large\n-latest‮ " }), "mistral-large-latest"],
  ["ASCII space kept, other separators dropped", url, JSON.stringify({ model: "my model 　x" }), "my modelx"],
  ["capped at 128 characters", url, JSON.stringify({ model: "m".repeat(200) }), "m".repeat(128)],
  ["capped by code point", url, JSON.stringify({ model: "\u{1F642}".repeat(130) }), "\u{1F642}".repeat(128)],
  ["nothing printable", url, JSON.stringify({ model: "\u0000\n\t" }), "default"],
  ["a non-string model", url, '{"model":42}', "default"],
  ["no model", url, '{"messages":[]}', "default"],
  ["invalid JSON", url, "{not json", "default"],
  ["a bodiless chat GET", url, undefined, "default"],
  ["embeddings", audience + "/v1/embeddings", '{"model":"mistral-embed","input":"x"}', "embeddings"],
  ["discovery", audience + "/v1/models", undefined, "models"],
];
test.each(GATE_KEYS)("gate key: %s", async (_name, target, body, key) => {
  const stub = keyed(); const f = fixture({ gate: stub.gate });
  f.script(ok);
  await bounded(f.fetch(target, body === undefined ? {} : { method: "POST", body }));
  expect(stub.keys).toEqual([key]); expect(f.sent).toHaveLength(1);
});
