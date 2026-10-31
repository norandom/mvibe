// Executed only inside the isolated host profile by host-integration.ts.
import { Plugin } from "@opencode/plugin";
import { setupVibe } from "../src/index.js";
import { StdioBridge, BridgeError } from "../src/bridge.js";
import { readFileSync, writeFileSync } from "node:fs";
import assert from "node:assert/strict";
import { HostFixtureCredentials } from "./host-fixture-credentials.js";

export default Plugin.define({ id: "vibe-consumer-integration", async setup(ctx) {
  const evidence = String(ctx.options.evidence), rotation = String(ctx.options.rotation);
  const checks: Record<string, boolean> = {};
  let inference = 0, models = 0, sdkCalls = 0, languageCalls = 0;
  let mode = "text", upstreamFinished = false, cancelled = false;
  let release: (() => void) | undefined;
  const observedCredentials: string[] = [];
  const eventTypes: string[] = [];
  const warnings: string[] = [];
  const credentials = new HostFixtureCredentials();
  const originalWarn = console.warn;
  const originalRequest = StdioBridge.prototype.request;
  StdioBridge.prototype.request = async function(this: StdioBridge, method, params, signal) {
    try {
      const result = await originalRequest.call(this, method, params, signal);
      if (method === "resolve" && "target" in result && "headers" in result) credentials.resolved(result.target, result.headers);
      return result;
    }
    catch (error) { warnings.push("bridge:" + method + ":" + (error instanceof BridgeError ? error.code : "unexpected")); throw error; }
  } as StdioBridge["request"];
  console.warn = (...values: unknown[]) => { warnings.push(values.map(String).join(" ")); };
  const monitor = new AbortController();
  void (async () => { for await (const event of ctx.event.subscribe({ signal: monitor.signal })) eventTypes.push(event.type); })();
  const originalFetch = globalThis.fetch;
  const encoder = new TextEncoder();
  // No fallback: every fetch in this host fixture is synthetic or fails closed.
  globalThis.fetch = Object.assign(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const expected = readFileSync(rotation, "utf8");
    const observed = credentials.assertRequest(request, expected);
    if (request.url.endsWith("/models")) {
      models++;
      return Response.json({ data: [{ id: "synthetic-model", name: "Synthetic", capabilities: { tools: true, input: ["text"], output: ["text"] }, limit: { context: 131072, output: 4096 }, time: { released: 0 }, status: "active", cost: [{ input: 0, output: 0, cache: { read: 0, write: 0 } }] }] });
    }
    assert.ok(request.url.endsWith("/chat/completions"));
    inference++;
    observedCredentials.push(observed);
    const body = await request.json() as Record<string, unknown>;
    if (mode === "401" || mode === "503") return Response.json({ error: { message: "synthetic rejection" } }, { status: Number(mode) });
    if (!body.stream) return Response.json({ id: "synthetic", object: "chat.completion", created: 0, model: "synthetic-model", choices: [{ index: 0, message: { role: "assistant", content: "synthetic answer" }, finish_reason: "stop" }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } });
    upstreamFinished = false;
    const chunk = (delta: unknown, finish_reason: string | null = null) => encoder.encode("data: " + JSON.stringify({ id: "synthetic", object: "chat.completion.chunk", created: 0, model: "synthetic-model", choices: [{ index: 0, delta, finish_reason }] }) + "\n\n");
    return new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(chunk({ role: "assistant", content: "first synthetic chunk" }));
        if (mode === "gated") {
          controller.enqueue(chunk({ tool_calls: [{ index: 0, id: "synthetic-call", type: "function", function: { name: "synthetic_echo", arguments: '{"sentinel":' } }] }));
          controller.enqueue(chunk({ tool_calls: [{ index: 0, function: { arguments: '"preserved"}' } }] }));
        }
        release = () => { upstreamFinished = true; controller.enqueue(chunk({}, "stop")); controller.enqueue(encoder.encode("data: [DONE]\n\n")); controller.close(); };
        request.signal.addEventListener("abort", () => { cancelled = true; try { controller.error(new Error("synthetic cancellation")); } catch {} }, { once: true });
        if (mode !== "gated" && mode !== "cancel") { release(); release = undefined; }
      },
      cancel() { cancelled = true; },
    }), { headers: { "content-type": "text/event-stream" } });
  }, { preconnect() {} });
  let cleanup: (() => Promise<void>) | undefined;
  const registrations: Array<{ dispose(): Promise<void> }> = [];
  const wait = async (predicate: () => boolean, label: string) => {
    for (let i = 0; i < 200 && !predicate(); i++) await new Promise(resolve => setTimeout(resolve, 25));
    assert.ok(predicate(), label);
  };
  cleanup = await setupVibe(ctx);
  const run = async () => {
    let failure: string | undefined;
    try {
    const attempt = await ctx.integration.command.connect({ integrationID: "vibe-oidc", methodID: "vibe-browser", label: "synthetic" });
    let complete = false;
    for (let i = 0; i < 100 && !complete; i++) {
      const status = (await ctx.integration.command.status({ integrationID: "vibe-oidc", attemptID: attempt.data.attemptID })).data;
      assert.notEqual(status.status, "failed"); complete = status.status === "complete";
      if (!complete) await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.ok(complete);
    const connection = await ctx.integration.connection.active("vibe-oidc");
    assert.ok(connection);
    assert.deepEqual(await ctx.integration.connection.resolve(connection), { type: "key", key: "vibe-oidc-connected-v1" });
    checks.markerOnly = true;
    await wait(() => models > 0, "authenticated discovery");
    let visible = false;
    for (let i = 0; i < 100 && !visible; i++) {
      visible = JSON.stringify(await ctx.model.list()).includes("synthetic-model");
      if (!visible) await new Promise(resolve => setTimeout(resolve, 25));
    }
    assert.ok(visible, "authenticated inventory visible through native host model API");
    checks.modelDiscovery = true;
    registrations.push(await ctx.aisdk.hook("sdk", event => { sdkCalls++; assert.equal(event.options.maxRetries, 0); assert.ok(event.sdk); }, { providerID: "vibe-oidc" }));
    registrations.push(await ctx.aisdk.hook("language", event => { languageCalls++; assert.ok(event.language ?? event.sdk); }, { providerID: "vibe-oidc" }));
    registrations.push(await ctx.session.hook("context", event => { event.tools = { synthetic_echo: { description: "Synthetic test tool", input: { type: "object", properties: { sentinel: { type: "string" } }, required: ["sentinel"] } } }; }, { providerID: "vibe-oidc" }));
    registrations.push(await ctx.tool.transform(editor => { editor.add({ name: "synthetic_echo", description: "Synthetic test tool", input: { type: "object", properties: { sentinel: { type: "string" } }, required: ["sentinel"] }, execute: async () => ({ content: "synthetic" }) }); }));
    await ctx.tool.reload();
    const model = { id: "synthetic-model", providerID: "vibe-oidc" };
    for (const revision of ["one", "two"]) {
      writeFileSync(rotation, revision);
      const result = await ctx.experimental.generate.text({ model, prompt: "synthetic" });
      assert.equal(result.text, "first synthetic chunk");
    }
    assert.deepEqual(observedCredentials, ["one", "two"]);
    checks.currentCredentials = true;
    mode = "gated";
    const session = await ctx.session.create({ title: "synthetic integration", model });
    const events = new AbortController();
    let first = false, toolInput = false, nativeToolID = "";
    const observation = (async () => {
      for await (const event of ctx.event.subscribe({ signal: events.signal })) {
        const serialized = JSON.stringify(event);
        if (event.type === "session.text.delta" && serialized.includes("first synthetic chunk")) { assert.equal(upstreamFinished, false); first = true; }
        if (event.type === "session.tool.input.started" && event.data.name === "synthetic_echo") nativeToolID = event.data.id;
        if (event.type === "session.tool.input.ended" && event.data.id === nativeToolID) {
          assert.deepEqual(JSON.parse(event.data.text), { sentinel: "preserved" }); toolInput = true;
        }
      }
    })();
    await ctx.session.prompt({ sessionID: session.id, text: "synthetic" });
    await wait(() => first, "first host stream chunk before upstream completes");
    checks.incrementalStream = true;
    release?.(); release = undefined;
    await wait(() => toolInput, "tool call arguments preserved in native host stream events");
    checks.toolCallPayload = true;
    await ctx.session.interrupt({ sessionID: session.id });
    mode = "cancel"; cancelled = false; first = false;
    const cancellationSession = await ctx.session.create({ title: "synthetic cancellation", model });
    await ctx.session.prompt({ sessionID: cancellationSession.id, text: "synthetic" });
    await wait(() => first, "cancellable native stream started");
    await ctx.session.interrupt({ sessionID: cancellationSession.id });
    await wait(() => cancelled, "host cancellation reaches transport");
    checks.cancellation = true;
    events.abort(); await observation;
    for (const status of ["401", "503"]) {
      mode = status;
      const before = inference;
      await assert.rejects(ctx.experimental.generate.text({ model, prompt: "synthetic" }));
      await new Promise(resolve => setTimeout(resolve, 150));
      assert.equal(inference - before, 1, "host must not replay " + status);
      checks["noReplay" + status] = true;
      const rejectedSession = await ctx.session.create({ title: "synthetic rejection", model });
      const failureEvents = new AbortController();
      let failed = false;
      const observeFailure = (async () => {
        for await (const event of ctx.event.subscribe({ signal: failureEvents.signal })) {
          if (event.type === "session.execution.failed" && event.data.sessionID === rejectedSession.id) { failed = true; break; }
        }
      })();
      const sessionBefore = inference;
      try {
        await ctx.session.prompt({ sessionID: rejectedSession.id, text: "synthetic" });
        await wait(() => failed, "native session must terminate upstream " + status);
        assert.equal(inference - sessionBefore, 1, "native session must not replay " + status);
        checks["noSessionReplay" + status] = true;
      } finally { failureEvents.abort(); await observeFailure; await ctx.session.interrupt({ sessionID: rejectedSession.id }); }
    }
    checks.customSDKCalled = sdkCalls > 0 && languageCalls > 0 && inference >= 5;
    assert.ok(checks.customSDKCalled);
  } catch (error) {
    failure = String(error);
  } finally {
    try {
      try { release?.(); } catch { /* A cancelled synthetic controller is already closed. */ }
      for (const registration of registrations.reverse()) await registration.dispose();
      await cleanup?.(); checks.cleanup = true;
    } catch { failure = "Owned fixture/provider cleanup failed"; }
    globalThis.fetch = originalFetch; console.warn = originalWarn; StdioBridge.prototype.request = originalRequest; monitor.abort();
    writeFileSync(evidence, JSON.stringify({ status: failure ? "failed" : "passed", checks, ...(failure ? { failure, warnings, eventTypes } : {}), counts: { inference, models, sdkCalls, languageCalls } }, null, 2));
  } };
  setTimeout(() => { void run(); }, 50);
  return async () => { await cleanup?.(); globalThis.fetch = originalFetch; };
} });
