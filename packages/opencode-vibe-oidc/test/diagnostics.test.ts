import { test, expect, spyOn } from "bun:test";
import { createProviderFetch } from "../src/provider.js";
import { BridgeError } from "../src/bridge.js";
import type { StdioBridge } from "../src/bridge.js";

const audience = "https://private-audience.example.test/private-prefix";
const url = audience + "/v1/chat/completions?secret=query";
const bridge = { request: async (_method: string, params: { target_url: string }) => ({ target: params.target_url, headers: { authorization: "Bearer secret-token" }, expires_at: Date.now()/1000+300, revision: "secret-revision", metadata: { call_source: "vibe_code" } }) } as Pick<StdioBridge, "request">;

test.each([false, true])("diagnostics opt in %s: safe stderr status, untouched bodies and no replay", async enabled => {
  const stderr = spyOn(console, "error").mockImplementation(() => {});
  const stdout = spyOn(console, "log").mockImplementation(() => {});
  let sends = 0;
  const response = new Response("secret-upstream-body", { status: 422, headers: { "x-request-id": "secret-upstream-id" } });
  const fetch = createProviderFetch(bridge, audience, async input => { sends++; expect((input as Request).bodyUsed).toBe(false); return response; }, enabled ? { diagnostics: true } : undefined);
  try {
    const result = await fetch(url, { method: "POST", body: '{"model":"secret-model","messages":"secret-prompt"}', headers: { "x-request-id": "secret-caller-id" } });
    expect(result.body).toBe(response.body); expect(response.bodyUsed).toBe(false); expect(sends).toBe(1);
    expect(stdout).not.toHaveBeenCalled(); expect(stderr.mock.calls).toHaveLength(enabled ? 1 : 0);
    if (enabled) {
      const row = JSON.parse(stderr.mock.calls[0]![0]);
      expect(row).toMatchObject({ event: "provider_request", phase: "response_headers", outcome: "upstream_error", status: 422, endpoint: "chat/completions" });
      expect(row.request_id).toMatch(/^[a-f0-9-]{36}$/); expect(row.elapsed_ms).toBeGreaterThanOrEqual(0);
      expect(row.hint).toContain("reasoning"); expect(row.hint).toContain("schema");
      expect(JSON.stringify(row)).not.toMatch(/secret|private|example/);
    }
    await result.body!.cancel();
  } finally { stderr.mockRestore(); stdout.mockRestore(); }
});

test("diagnostic failures cannot break streaming acquisition or consume its body", async () => {
  const stderr = spyOn(console, "error").mockImplementation(() => { throw new Error("secret-output-failure"); });
  let closed = false;
  const response = new Response(new ReadableStream({ cancel() { closed = true; } }), { headers: { "content-type": "text/event-stream" } });
  try {
    const fetch = createProviderFetch(bridge, audience, async () => response, { diagnostics: true });
    const result = await fetch(url);
    expect(result.body).toBe(response.body); expect(response.bodyUsed).toBe(false);
    expect(result.status).toBe(200); expect(result.headers.get("content-type")).toBe("text/event-stream");
    expect(result.headers.get("x-should-retry")).toBe("false");
    await result.body!.cancel(); expect(closed).toBe(true);
  } finally { stderr.mockRestore(); }
});

test.each([200, 401])("safe header outcome and OIDC hint for %s", async status => {
  const stderr = spyOn(console, "error").mockImplementation(() => {});
  try {
    const fetch = createProviderFetch(bridge, audience, async () => new Response(null, { status }), { diagnostics: true });
    await fetch(url);
    const row = JSON.parse(stderr.mock.calls[0]![0]);
    expect(row.outcome).toBe(status === 200 ? "headers_received" : "upstream_error");
    expect(row.hint).toContain(status === 200 ? "completion is not observed" : "OIDC sign-in");
  } finally { stderr.mockRestore(); }
});

test.each(["401", "resolve"])("existing Vibe login gives secret-safe native sign-in guidance for %s", async failure => {
  const stderr = spyOn(console, "error").mockImplementation(() => {});
  const stdout = spyOn(console, "log").mockImplementation(() => {});
  const response = new Response("secret-upstream-body", { status: 401, headers: { "x-request-id": "secret-response-header" } });
  const source = failure === "resolve" ? { request: async () => { throw new BridgeError("auth", "secret-keyring-detail"); } } as Pick<StdioBridge, "request"> : bridge;
  let sends = 0;
  const fetch = createProviderFetch(source, audience, async () => { sends++; return response; }, { diagnostics: true, authSource: "existing-vibe" });
  try {
    const result = fetch(url, { method: "POST", body: '{"model":"secret-model","messages":"secret-prompt"}', headers: { "x-request-id": "secret-caller-header" } });
    if (failure === "resolve") await expect(result).rejects.toBeInstanceOf(BridgeError);
    else {
      const returned = await result;
      expect(returned.body).toBe(response.body); expect(response.bodyUsed).toBe(false);
      await returned.body!.cancel();
    }
    expect(sends).toBe(failure === "resolve" ? 0 : 1);
    expect(stdout).not.toHaveBeenCalled(); expect(stderr).toHaveBeenCalledTimes(1);
    const row = JSON.parse(stderr.mock.calls[0]![0]);
    expect(row).toMatchObject({ phase: failure === "resolve" ? "authentication" : "response_headers", status: failure === "resolve" ? null : 401 });
    expect(row.hint).toBe("Sign in through normal Vibe, then retry; credentials are read from the native keyring.");
    expect(JSON.stringify(row)).not.toMatch(/secret|private|example|OIDC/);
    expect(Object.hasOwn(row, "headers")).toBe(false); expect(Object.hasOwn(row, "body")).toBe(false);
  } finally { stderr.mockRestore(); stdout.mockRestore(); }
});

test.each(["resolve", "transport", "policy", "cancelled"])("safe failure diagnostics: %s", async failure => {
  const stderr = spyOn(console, "error").mockImplementation(() => {});
  let sends = 0;
  const failedBridge = failure === "resolve" ? { request: async () => { throw new BridgeError("oidc", "secret-exception"); } } as Pick<StdioBridge, "request"> : bridge;
  const fetch = createProviderFetch(failedBridge, audience, async () => { sends++; throw new Error("secret-transport"); }, { diagnostics: true });
  const controller = new AbortController(); if (failure === "cancelled") controller.abort();
  try {
    await expect(fetch(failure === "policy" ? "https://secret-host.test/secret" : url, { signal: controller.signal })).rejects.toBeInstanceOf(BridgeError);
    const row = JSON.parse(stderr.mock.calls[0]![0]);
    expect(row.outcome).toBe(failure === "cancelled" ? "cancelled" : "failed");
    expect(JSON.stringify(row)).not.toContain("secret"); expect(row.status).toBe(null);
    expect(sends).toBe(failure === "transport" ? 1 : 0);
  } finally { stderr.mockRestore(); }
});
