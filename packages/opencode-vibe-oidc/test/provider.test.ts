import { test, expect } from "bun:test";
import { createProviderFetch, providerBaseURL } from "../src/provider.js";
import type { StdioBridge } from "../src/bridge.js";
import { BridgeError } from "../src/bridge.js";
import type { Results } from "../src/protocol.js";
const audience = "https://inference.example.test";
const url = audience + "/v1/chat/completions?q=&q=two";
function fixture() {
  const resolved: string[] = [], sent: Request[] = [];
  let snapshot: ((target: string, signal?: AbortSignal) => Promise<Results["resolve"]>) = async target => ({ target, headers: { Authorization: `Bearer synthetic-${resolved.length}`, "x-external-organization-id": "org", "x-external-workspace-id": "space", "x-evil": "exclude" }, expires_at: Date.now() / 1000 + 300, revision: String(resolved.length) });
  const bridge = { request: async (method: string, params: { target_url: string }, signal?: AbortSignal) => { expect(method).toBe("resolve"); resolved.push(params.target_url); return snapshot(params.target_url, signal); } } as Pick<StdioBridge, "request">;
  let response = new Response("unauthorized", { status: 401 });
  const fetch = createProviderFetch(bridge, audience, async (input, init) => { sent.push(new Request(input, init)); return response; });
  return { fetch, resolved, sent, snapshot: (value: typeof snapshot) => { snapshot = value; }, response: (value: Response) => { response = value; } };
}
test("per-fetch rotation replaces caller credentials and preserves tool bytes, method, query and response", async () => {
  const f = fixture(); const body = '{ "tools": [{"function":{"arguments":"{\\"x\\":1}"}}], "stream":true }';
  for (let i = 1; i <= 2; i++) {
    const result = await f.fetch(new Request(url, { method: "POST", body, headers: { Authorization: "Bearer connection-marker", "x-api-key": "caller-key", "api-key": "caller-key", "x-external-workspace-id": "caller", "content-type": "application/json" } }));
    expect(result.status).toBe(401);
    const request = f.sent.at(-1)!;
    expect(request.url).toBe(url); expect(request.method).toBe("POST"); expect(await request.text()).toBe(body);
    expect(request.headers.get("authorization")).toBe(`Bearer synthetic-${i}`);
    expect(request.headers.get("x-external-workspace-id")).toBe("space");
    for (const name of ["x-api-key", "api-key", "x-evil"]) expect(request.headers.has(name)).toBe(false);
    expect(request.redirect).toBe("manual");
  }
  expect(f.resolved).toEqual([url, url]); expect(f.sent).toHaveLength(2);
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
