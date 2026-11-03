// Executed only inside the isolated host profile by host-integration.ts.
import { Plugin } from "@opencode/plugin";
import { setupVibe as sourceSetupVibe } from "../src/index.js";
import { pathToFileURL } from "node:url";

// The explicit release gate exercises the extracted distribution in the real
// host loader; ordinary offline tests retain the source-provider path.
const setupVibe: typeof sourceSetupVibe = process.env.VIBE_TEST_PROVIDER_ENTRY
  ? (await import(pathToFileURL(process.env.VIBE_TEST_PROVIDER_ENTRY).href)).setupVibe
  : sourceSetupVibe;
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
  const nativeRequests: Array<{ metadata: Record<string, string>; toolResult: boolean }> = [];
  const efforts: unknown[] = [];
  const optionKeys: string[] = [];
  const metadataSessionIDs = new Set<string>();
  let toolExecutions = 0;
  const eventTypes: string[] = [];
  const warnings: string[] = [];
  const credentials = new HostFixtureCredentials();
  let resolvedLines = 0;
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
  void (async () => { for await (const event of ctx.event.subscribe({ signal: monitor.signal })) {
    eventTypes.push(event.type);
  } })();
  const originalFetch = globalThis.fetch;
  const encoder = new TextEncoder();
  // No fallback: every fetch in this host fixture is synthetic or fails closed.
  globalThis.fetch = Object.assign(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    if (process.env.VIBE_TEST_RESOLUTIONS) {
      const lines = readFileSync(process.env.VIBE_TEST_RESOLUTIONS, "utf8").trim().split("\n");
      for (const line of lines.slice(resolvedLines)) {
        const item = JSON.parse(line) as { target: string; revision: string };
        credentials.resolved(item.target, { Authorization: "Bearer synthetic-" + item.revision });
      }
      resolvedLines = lines.length;
    }
    const expected = readFileSync(rotation, "utf8");
    const observed = credentials.assertRequest(request, expected);
    assert.equal(request.headers.get("user-agent"), "mistral-client-python/Mistral-Vibe/3.0.4");
    assert.equal(request.headers.get("x-mvibe-request-context"), null, "private context must not reach upstream");
    if (request.url.endsWith("/models")) {
      models++;
      const entry = (id: string) => ({ id, name: id, capabilities: { tools: true, input: ["text"], output: ["text"] }, limit: { context: 131072, output: 4096 }, time: { released: 0 }, status: "active" as const, cost: [{ input: 0, output: 0, cache: { read: 0, write: 0 } }] });
      return Response.json({ data: [entry("synthetic-model"), entry("mistral-large-4"), entry("mistral-medium-latest")] });
    }
    assert.ok(request.url.endsWith("/chat/completions"));
    inference++;
    observedCredentials.push(observed);
    const body = await request.json() as Record<string, unknown>;
    assert.ok(body.metadata && typeof body.metadata === "object" && !Array.isArray(body.metadata), "native completion metadata is required");
    const metadata = body.metadata as Record<string, string>;
    assert.equal(metadata.call_source, "vibe_code");
    assert.equal(metadata.host_kind, "local");
    assert.equal(metadata.version, "3.0.4");
    assert.ok(Object.values(metadata).every(value => typeof value === "string"));
    assert.ok(metadata.os && metadata.arch && metadata.call_type);
    assert.equal(metadata.user_plan, "Pro", "plan comes from the synthetic account-context lookup");
    const toolResult = Array.isArray(body.messages) && body.messages.some(message => message?.role === "tool");
    nativeRequests.push({ metadata, toolResult });
    efforts.push(body.reasoning_effort);
    if (mode === "metadata") {
      if (!toolResult) await wait(() => [...metadataSessionIDs].every(id => nativeRequests.some(item => item.metadata.session_id === id)), "both session requests must overlap");
      if (toolResult) assert.ok(JSON.stringify(body.messages).includes("owned metadata bytes"), "native read result reaches continuation");
      const frame = (delta: unknown, finish_reason: string | null) => "data: " + JSON.stringify({ id: "native-metadata", model: "synthetic-model", choices: [{ index: 0, delta, finish_reason }] }) + "\n\n";
      const delta = toolResult ? { role: "assistant", content: "metadata done" } : { role: "assistant", tool_calls: [{ index: 0, id: "meta00001", type: "function", function: { name: "read", arguments: JSON.stringify({ path: String(ctx.options.metadataFile) }) } }] };
      return new Response(frame(delta, null) + frame({}, toolResult ? "stop" : "tool_calls") + "data: [DONE]\n\n", { headers: { "content-type": "text/event-stream" } });
    }
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
  const wait = async (predicate: () => boolean, label: string, timeoutMs = 5_000) => {
    const deadline = Date.now() + timeoutMs;
    while (!predicate() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 25));
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
    // Cold packaged-host/helper startup can exceed the ordinary event budget.
    await wait(() => models > 0, "authenticated discovery", 20_000);
    let visible = false;
    for (let i = 0; i < 100 && !visible; i++) {
      visible = JSON.stringify(await ctx.model.list()).includes("synthetic-model");
      if (!visible) await new Promise(resolve => setTimeout(resolve, 25));
    }
    assert.ok(visible, "authenticated inventory visible through native host model API");
    checks.modelDiscovery = true;
    const listed = await ctx.model.list();
    const large4 = listed.data.find(model => String(model.id) === "mistral-large-4");
    assert.ok(large4, "curated model visible through native host model API");
    assert.deepEqual(large4!.variants.map(variant => String(variant.id)), ["low", "high", "max"], "variants offered");
    assert.deepEqual(large4!.variants.map(variant => variant.settings), [{ reasoningEffort: "low" }, { reasoningEffort: "high" }, { reasoningEffort: "max" }], "variant settings overlays");
    assert.ok(!large4!.variants.some(variant => variant.settings && "reasoningEffort" in variant.settings && variant.settings.reasoningEffort === "none"), "large-4 rejects none, so no off variant");
    registrations.push(await ctx.aisdk.hook("sdk", event => { sdkCalls++; assert.equal(event.options.maxRetries, 0); assert.ok(event.sdk); optionKeys.push(JSON.stringify(Object.keys(event.options ?? {}))); }, { providerID: "vibe-oidc" }));
    registrations.push(await ctx.aisdk.hook("language", event => { languageCalls++; assert.ok(event.language ?? event.sdk); }, { providerID: "vibe-oidc" }));
    registrations.push(await ctx.session.hook("context", event => {
      if (mode === "metadata") { assert.ok(event.tools.read, "native read is offered"); event.tools = { read: event.tools.read }; }
      else event.tools = { synthetic_echo: { description: "Synthetic test tool", input: { type: "object", properties: { sentinel: { type: "string" } }, required: ["sentinel"] } } };
    }, { providerID: "vibe-oidc" }));
    registrations.push(await ctx.tool.transform(editor => { editor.add({ name: "synthetic_echo", description: "Synthetic test tool", input: { type: "object", properties: { sentinel: { type: "string" } }, required: ["sentinel"] }, execute: async () => ({ content: "synthetic" }) }); }));
    registrations.push(await ctx.tool.hook("execute.after", event => {
      if (metadataSessionIDs.has(event.sessionID)) {
        assert.equal(event.tool, "read"); assert.equal(event.status, "completed"); toolExecutions++;
      }
    }));
    await ctx.tool.reload();
    const model = { id: "synthetic-model", providerID: "vibe-oidc" };
    for (const revision of ["one", "two"]) {
      writeFileSync(rotation, revision);
      const result = await ctx.experimental.generate.text({ model, prompt: "synthetic" });
      assert.equal(result.text, "first synthetic chunk");
    }
    assert.deepEqual(observedCredentials, ["one", "two"]);
    checks.currentCredentials = true;
    for (const [id, variant, expected] of [
      ["mistral-large-4", "low", "low"],
      ["mistral-large-4", "high", "high"],
      ["mistral-large-4", "max", "max"],
      ["mistral-medium-latest", "off", "none"],
      ["mistral-large-4", undefined, undefined],
    ] as const) {
      const effortBefore = efforts.length;
      const variantSession = await ctx.session.create({ title: "variant overlay", model: { id, providerID: "vibe-oidc", ...(variant ? { variant } : {}) } });
      await ctx.session.prompt({ sessionID: variantSession.id, text: "synthetic" });
      await ctx.session.wait({ sessionID: variantSession.id });
      assert.equal(efforts.length, effortBefore + 1, "one upstream request for the variant session");
      assert.equal(efforts[efforts.length - 1], expected, "host merges the selected variant settings overlay and the SDK emits reasoning_effort");
    }
    checks.variantSettingsOverlay = true;
    mode = "metadata";
    const metadataSessions: Array<Awaited<ReturnType<typeof ctx.session.create>>> = [];
    for (const label of ["one", "two"]) {
      metadataSessions.push(await ctx.session.create({ title: "owned metadata " + label, model }));
    }
    metadataSessions.forEach(item => metadataSessionIDs.add(item.id));
    for (const item of metadataSessions) {
      await ctx.session.prompt({ sessionID: item.id, text: "synthetic metadata tool continuation" });
      await wait(() => nativeRequests.some(request => request.metadata.session_id === item.id), "session starts its metadata request");
    }
    await wait(() => metadataSessions.every(item => nativeRequests.some(request => request.metadata.session_id === item.id && request.toolResult)), "each concurrent session must reach a correlated tool-result request");
    await Promise.all(metadataSessions.map(item => ctx.session.wait({ sessionID: item.id })));
    assert.equal(toolExecutions, 2, "both real native tools executed");
    for (const item of metadataSessions) {
      const requests = nativeRequests.filter(request => request.metadata.session_id === item.id);
      assert.equal(requests.length, 2);
      assert.equal(requests[0]!.metadata.call_type, "main_call");
      assert.equal(requests[1]!.metadata.call_type, "secondary_call");
      assert.equal(requests[0]!.toolResult, false);
      assert.equal(requests[1]!.toolResult, true);
      assert.ok(requests[0]!.metadata.message_id, "native message context must be present");
      assert.equal(requests[0]!.metadata.client_name, "opencode");
      assert.equal(requests[0]!.metadata.client_version, ctx.app.version);
      assert.equal(requests[0]!.metadata.agent_version, ctx.app.version);
      assert.equal(requests[0]!.metadata.message_id, requests[1]!.metadata.message_id);
    }
    assert.equal(new Set(metadataSessions.map(item => nativeRequests.find(request => request.metadata.session_id === item.id)!.metadata.message_id)).size, 2, "message context is isolated between sessions");
    mode = "text";
    const auxiliaryStart = nativeRequests.length;
    const auxiliary = await ctx.session.generate({ sessionID: metadataSessions[0]!.id, prompt: "owned auxiliary generation" });
    assert.equal(auxiliary.text, "first synthetic chunk");
    const auxiliaryRequests = nativeRequests.slice(auxiliaryStart);
    assert.equal(auxiliaryRequests.length, 1);
    assert.equal(auxiliaryRequests[0]!.metadata.session_id, metadataSessions[0]!.id);
    assert.equal(auxiliaryRequests[0]!.metadata.call_type, "secondary_call");
    assert.equal(auxiliaryRequests[0]!.metadata.client_name, "opencode");
    checks.nativeRequestMetadata = true;
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
    writeFileSync(evidence, JSON.stringify({ status: failure ? "failed" : "passed", checks, ...(failure ? { failure, warnings, eventTypes } : {}), counts: { inference, models, sdkCalls, languageCalls }, optionKeys }, null, 2));
  } };
  setTimeout(() => { void run(); }, 50);
  return async () => { await cleanup?.(); globalThis.fetch = originalFetch; };
} });
