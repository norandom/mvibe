import { expect, test } from "bun:test";
import type { StdioBridge } from "../src/bridge.js";
import { createProviderFetch } from "../src/provider.js";
import { registerRequestContext, REQUEST_CONTEXT_HEADER } from "../src/request-context.js";
import type { Plugin, Provider } from "@opencode/plugin";
import type { Params } from "../src/protocol.js";

for (const plan of [undefined, "Team"]) test(`server owns optional user_plan (${plan})`, async () => {
  let body: any;
  const bridge = { request: async (_method: string, params: Params["resolve"]) => ({
    target: params.target_url, headers: {}, expires_at: Date.now() / 1000 + 60, revision: "one",
    metadata: { call_source: "vibe_code", ...(plan ? { user_plan: plan } : {}) },
  }) } as Pick<StdioBridge, "request">;
  const fetch = createProviderFetch(bridge, "https://inference.example", async input => {
    body = await (input as Request).json(); return new Response();
  });
  await fetch("https://inference.example/v1/chat/completions", { method: "POST",
    body: '{"messages":[],"metadata":{"user_plan":"Pro","custom":"kept"}}' });
  expect(body.metadata.user_plan).toBe(plan);
  expect(body.metadata.custom).toBe("kept");
});

test("final serialized body merges helper metadata once and consumes private context", async () => {
  const calls: Params["resolve"][] = [], sent: Request[] = [];
  const bridge = { request: async (_method: string, params: Params["resolve"]) => {
    calls.push(params);
    return { target: params.target_url, expires_at: Date.now() / 1000 + 60, revision: "one", headers: {},
      metadata: { session_id: params.request_context!.session_id!, call_source: "vibe_code", call_type: "main_call" } };
  } } as Pick<StdioBridge, "request">;
  const fetch = createProviderFetch(bridge, "https://inference.example", async input => {
    sent.push(input as Request); return new Response("synthetic");
  });
  const payload = { messages: [{ role: "assistant", tool_calls: [{ function: { arguments: '{ "x": 1 }' } }] }],
    tools: [{ function: { parameters: { type: "object" } } }], stream: true,
    metadata: { custom: "kept", call_source: "caller" } };
  await Promise.all(["one", "two"].map(session_id => fetch("https://inference.example/v1/chat/completions", {
    method: "POST", headers: { [REQUEST_CONTEXT_HEADER]: JSON.stringify({ session_id }), "content-length": String(JSON.stringify(payload).length) }, body: JSON.stringify(payload),
  })));
  expect(calls.map(c => c.request_context?.session_id).sort()).toEqual(["one", "two"]);
  for (const request of sent) {
    expect(request.headers.has(REQUEST_CONTEXT_HEADER)).toBe(false);
    expect(request.headers.has("content-length")).toBe(false);
    const body = await request.json();
    expect(body.messages).toEqual(payload.messages); expect(body.tools).toEqual(payload.tools);
    expect(body.metadata.custom).toBe("kept"); expect(body.metadata.call_source).toBe("vibe_code");
  }
  expect(sent).toHaveLength(2);
});

test("active context hooks isolate sessions, tool iterations and auxiliary purposes", async () => {
  const hooks = new Map<string, (event: any) => void>();
  const ctx = { app: { version: "2.2.5" }, session: {
    hook: async (name: string, callback: (event: any) => void) => { hooks.set(name, callback); return { dispose: async () => {} }; },
  } } as unknown as Pick<Plugin.Context, "session" | "app">;
  const registration = await registerRequestContext(ctx, "vibe-oidc" as Provider.ID);
  hooks.get("context")!({ sessionID: "one", messages: [{ role: "user", id: "m1" }] });
  hooks.get("context")!({ sessionID: "two", messages: [{ role: "user", id: "m2" }] });
  const capture = (sessionID: string, kind: string) => {
    const event = { sessionID, kind, headers: {} as Record<string, string> };
    hooks.get("model.request")!(event);
    return JSON.parse(event.headers[REQUEST_CONTEXT_HEADER]!);
  };
  expect(capture("one", "primary")).toMatchObject({ session_id: "one", message_id: "m1", iteration: 0, purpose: "agent", client_name: "opencode", client_version: "2.2.5" });
  hooks.get("context")!({ sessionID: "one", messages: [{ role: "user", id: "m1" }, { role: "assistant" }, { role: "tool" }] });
  expect(capture("one", "primary")).toMatchObject({ iteration: 1, message_id: "m1" });
  expect(capture("two", "primary")).toMatchObject({ iteration: 0, message_id: "m2" });
  expect(capture("one", "title")).toMatchObject({ purpose: "title" });
  expect(capture("one", "compaction")).toMatchObject({ purpose: "compaction" });
  expect(capture("unknown", "primary").message_id).toBeUndefined();
  await registration.dispose();
});

test("requested metadata missing from helper fails before inference transport", async () => {
  let sent = 0;
  const bridge = { request: async (_method: string, params: Params["resolve"]) => ({
    target: params.target_url, headers: {}, expires_at: Date.now() / 1000 + 60, revision: "one",
  }) } as Pick<StdioBridge, "request">;
  const fetch = createProviderFetch(bridge, "https://inference.example", async () => { sent++; return new Response(); });
  await expect(fetch("https://inference.example/v1/chat/completions", { method: "POST", body: '{}' })).rejects.toThrow("matching");
  expect(sent).toBe(0);
});

test("explicit null metadata gets native helper attribution", async () => {
  let body: unknown;
  const bridge = { request: async (_method: string, params: Params["resolve"]) => ({
    target: params.target_url, headers: {}, expires_at: Date.now() / 1000 + 60, revision: "one",
    metadata: { call_source: "vibe_code", host_kind: "local" },
  }) } as Pick<StdioBridge, "request">;
  const fetch = createProviderFetch(bridge, "https://inference.example", async input => {
    body = await (input as Request).json(); return new Response();
  });
  await fetch("https://inference.example/v1/chat/completions", { method: "POST", body: '{"messages":[],"metadata":null}' });
  expect(body).toEqual({ messages: [], metadata: { call_source: "vibe_code", host_kind: "local" } });
});

test("partial context hook setup rolls back owned registration", async () => {
  let disposed = 0;
  const ctx = { app: { version: "2.2.5" }, session: {
    hook: async (name: string) => {
      if (name === "model.request") throw new Error("synthetic rejection");
      return { dispose: async () => { disposed++; } };
    },
  } } as unknown as Pick<Plugin.Context, "session" | "app">;
  await expect(registerRequestContext(ctx, "vibe-oidc" as Provider.ID)).rejects.toThrow();
  expect(disposed).toBe(1);
});
