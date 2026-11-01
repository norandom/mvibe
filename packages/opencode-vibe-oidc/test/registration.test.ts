import { test, expect, spyOn } from "bun:test";
import { Plugin, Model, Provider } from "@opencode/plugin";
import plugin, { setupVibe } from "../src/index.js";
import type { RegistrationContext } from "../src/index.js";
import { StdioBridge } from "../src/bridge.js";
import { ControlledHTTP } from "./preload.js";
import type { LanguageModelV3 } from "@ai-sdk/provider";

test("direct provider appears as Mistral-Vibe while retaining the credential ID", async () => {
  const f = fixture();
  const cleanup = await setupVibe(f.ctx);
  try { expect(f.providers[0]).toMatchObject({ info: { id: "vibe-oidc", name: "Mistral-Vibe" } }); }
  finally { await cleanup(); }
});

function fixture(options: Plugin.Context["options"] = { audience: "https://inference.example.test", helperCommand: { executable: "uv", args: ["run", "--offline", "--no-sync", "--project", "C:/installed runtime", "-m", "mvibe_auth", "bridge"] } }, lazyProvider = false) {
  const providers: unknown[] = [], methods: unknown[] = [], disposed: string[] = [];
  const hostModels = new Map<string, Model.Info>();
  const hooks: Array<{ name: string; options: unknown; callback: unknown }> = [];
  let active: Awaited<ReturnType<RegistrationContext["integration"]["connection"]["active"]>>;
  let editorCallback: ((editor: ProviderEditor) => void) | undefined;
  let notify: (() => void) | undefined;
  const pending: string[] = [];
  const emit = (type: string) => { pending.push(type); notify?.(); };
  type ProviderEditor = Parameters<Parameters<RegistrationContext["provider"]["transform"]>[0]>[0];
  type IntegrationEditor = Parameters<Parameters<RegistrationContext["integration"]["transform"]>[0]>[0];
  // Only methods touched by setup are supplied; their signatures come from SDK.
  const providerEditor: Pick<ProviderEditor, "add" | "get"> = {
    add: input => { providers.push(input); },
    get: id => id === "mistral" ? { provider: Provider.Info.empty(Provider.ID.make("mistral")), models: hostModels } : undefined,
  };
  const integrationEditor: Pick<IntegrationEditor, "method"> = { method: { update: input => { methods.push(input); }, list: () => [], remove: () => {} } };
  const ctx = {
    options,
    event: { subscribe: async function* (options: { signal?: AbortSignal; types?: string[] }) { while (!options?.signal?.aborted) { if (!pending.length) await new Promise<void>(resolve => { notify = resolve; options?.signal?.addEventListener("abort", () => resolve(), { once: true }); }); if (options?.signal?.aborted) break; const type = pending.shift()!; if (!options.types || options.types.includes(type)) yield { type }; } } },
    aisdk: { hook: async (name: string, callback: unknown, options: unknown) => { hooks.push({ name, callback, options }); return { dispose: async () => { disposed.push("aisdk"); } }; } },
    provider: { reload: async () => { if (!lazyProvider) editorCallback?.(providerEditor as ProviderEditor); }, transform: async (edit: (editor: ProviderEditor) => void) => { editorCallback = edit; if (!lazyProvider) edit(providerEditor as ProviderEditor); return { dispose: async () => { disposed.push("provider"); } }; } },
    integration: { connection: { active: async () => active }, transform: async (edit: (editor: IntegrationEditor) => void) => { edit(integrationEditor as IntegrationEditor); return { dispose: async () => { disposed.push("integration"); } }; } },
  } as unknown as RegistrationContext;
  return { ctx, providers, methods, disposed, hooks, hostModels, readProvider: () => editorCallback?.(providerEditor as ProviderEditor), connect: (event = "integration.updated") => { active = { type: "credential", id: "connection-one", label: "Vibe", method: "key" }; emit(event); }, disconnect: (event = "integration.updated") => { active = undefined; emit("credential.switched"); } };
}

test("distinct production V2 provider registers only browser command, preserves argv", async () => {
  const f = fixture();
  expect(plugin.id).toBe("vibe-oidc");
  const cleanup = await setupVibe(f.ctx);
  expect(f.providers).toMatchObject([{ info: { id: "vibe-oidc", name: "Mistral-Vibe", package: "aisdk:@ai-sdk/openai-compatible" }, models: [] }]);
  expect(f.methods).toEqual([{ integrationID: "vibe-oidc", method: { id: "vibe-browser", type: "command", label: "Mistral-Vibe", command: ["uv", "run", "--offline", "--no-sync", "--project", "C:/installed runtime", "-m", "mvibe_auth", "login"] } }]);
  await cleanup();
  await cleanup();
  expect(f.disposed).toEqual(["aisdk", "integration", "provider"]);
});

test("V2 SDK hook is provider-scoped and replaces host key options with V3 custom SDK", async () => {
  const f = fixture(); const cleanup = await setupVibe(f.ctx);
  expect(f.hooks).toHaveLength(1); const hook = f.hooks[0]!;
  expect(hook.name).toBe("sdk"); expect(hook.options).toEqual({ providerID: "vibe-oidc" });
  // The SDK event model is not read: metadata belongs to task 7.4.
  type SDKEvent = Extract<Parameters<Parameters<RegistrationContext["aisdk"]["hook"]>[1]>[0], { package: string }>;
  const event = { model: undefined, package: "@ai-sdk/openai-compatible", options: { apiKey: "connection-marker" }, sdk: undefined } as unknown as SDKEvent;
  await (hook.callback as (event: SDKEvent) => void | Promise<void>)(event);
  expect(event.options.maxRetries).toBe(0); expect(typeof event.options.fetch).toBe("function");
  const sdk: unknown = event.sdk;
  expect(typeof sdk).toBe("function");
  const model = (sdk as (id: string) => { specificationVersion: string; provider: string })("synthetic-model");
  expect(model.specificationVersion).toBe("v3"); expect(model.provider).toContain("vibe-oidc");
  await cleanup();
});

test("registered SDK rewrites Mistral reasoning history before authenticated transport", async () => {
  const f = fixture();
  Object.assign(f.ctx.options, { diagnostics: true });
  const diagnostic = spyOn(console, "error").mockImplementation(() => {});
  let cleanup: (() => Promise<void>) | undefined;
  type SDKEvent = Extract<Parameters<Parameters<RegistrationContext["aisdk"]["hook"]>[1]>[0], { package: string }>;
  const event = { model: undefined, package: "@ai-sdk/openai-compatible", options: {}, sdk: undefined } as unknown as SDKEvent;
  const http = new ControlledHTTP();
  const target = "https://inference.example.test/v1/chat/completions";
  http.queue("POST", target, Response.json({ choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: "Done" } }] }));
  const request = spyOn(StdioBridge.prototype, "request").mockImplementation((async () => ({ target, revision: "synthetic", expires_at: Date.now() / 1000 + 60, headers: { authorization: "Bearer synthetic" } })) as StdioBridge["request"]);
  const transport = spyOn(globalThis, "fetch").mockImplementation(Object.assign(http.fetch, { preconnect: () => {} }));
  try {
    cleanup = await setupVibe(f.ctx);
    await (f.hooks[0]!.callback as (event: SDKEvent) => Promise<void>)(event);
    const sdk = event.sdk as unknown as (id: string) => LanguageModelV3;
    await sdk("mistral-large-4").doGenerate({ prompt: [{ role: "assistant", content: [{ type: "reasoning", text: "Retain this reasoning" }, { type: "text", text: "Answer" }] }] });
    expect(http.requests).toHaveLength(1);
    expect((await http.requests[0]!.json()).messages).toEqual([{ role: "assistant", content: [{ type: "thinking", thinking: [{ type: "text", text: "Retain this reasoning" }] }, { type: "text", text: "Answer" }] }]);
    expect(http.requests[0]!.headers.get("authorization")).toBe("Bearer synthetic");
    expect(diagnostic).toHaveBeenCalledTimes(1);
    expect(JSON.parse(diagnostic.mock.calls[0]![0])).toMatchObject({ event: "provider_request", status: 200, outcome: "headers_received" });
  } finally { diagnostic.mockRestore(); request.mockRestore(); transport.mockRestore(); await cleanup?.(); }
});

test.each([{}, { helperCommand: { executable: "python", args: [] }, audience: "x" }, { helperCommand: { executable: "uv", args: ["run", "-m", "mvibe_auth", "bridge"] }, audience: "x" }])("missing/unsafe runtime is actionable before registration %j", async options => {
  const f = fixture(options);
  await expect(setupVibe(f.ctx)).rejects.toThrow("installed uv-managed");
  expect(f.providers).toEqual([]);
});

test("registration failure rolls back earlier provider and sanitizes host errors", async () => {
  const f = fixture();
  Object.assign(f.ctx.integration, { transform: async () => { throw new Error("SECRET host detail"); } });
  await expect(setupVibe(f.ctx)).rejects.toThrow("compatible OpenCode V2");
  expect(f.disposed).toEqual(["provider"]);
});

test("setup waits for credential event discovery that supersedes its initial request", async () => {
  const f = fixture();
  f.connect("initial");
  Object.assign(f.ctx.options, { modelMetadata: { available: { name: "Synthetic", capabilities: { tools: false, input: ["text"], output: ["text"] }, limit: { context: 2048, output: 256 }, time: { released: 1 }, status: "active", cost: [{ input: 1, output: 2, cache: { read: 0, write: 0 } }] } } });
  const base = "https://inference.example.test/v1/models";
  const releases: Array<() => void> = [];
  const request = spyOn(StdioBridge.prototype, "request").mockImplementation((async (method: string) => method === "status" ? { signed_in: true, config_match: true } : { target: base, revision: "synthetic", expires_at: Date.now() / 1000 + 60, headers: { authorization: "Bearer synthetic" } }) as StdioBridge["request"]);
  const transport = spyOn(globalThis, "fetch").mockImplementation(Object.assign(async () => { await new Promise<void>(resolve => releases.push(resolve)); return Response.json({ data: [{ id: "available" }] }); }, { preconnect: () => {} }));
  let settled = false;
  const setup = setupVibe(f.ctx).then(cleanup => { settled = true; return cleanup; });
  try {
    while (releases.length < 1) await Promise.resolve();
    f.connect("credential.updated");
    while (releases.length < 2) await Promise.resolve();
    releases[0]!();
    for (let i = 0; i < 30; i++) await Promise.resolve();
    expect(settled).toBe(false);
    releases[1]!();
    const cleanup = await setup;
    expect(f.providers.at(-1)).toMatchObject({ models: [{ id: "available" }], sourceConnection: { id: "connection-one" } });
    await cleanup();
  } finally {
    for (const release of releases) release();
    await (await setup)();
    request.mockRestore(); transport.mockRestore();
  }
});

test.each(["integration.updated", "credential.updated", "credential.switched", "lazy-first-read"])("native V2 %s publishes authenticated models through synchronous transform and reload", async event => {
  const lazy = event === "lazy-first-read";
  const f = fixture(undefined, lazy); const http = new ControlledHTTP();
  const base = "https://inference.example.test/v1/models";
  f.hostModels.set("available", { ...Model.Info.default(Provider.ID.make("mistral"), Model.ID.make("available")), name: "Synthetic", capabilities: { tools: false, input: ["text"], output: ["text"] }, limit: { context: 2048, output: 256 }, time: { released: 1 }, status: "active", cost: [{ input: 1 as Model.Cost["input"], output: 2 as Model.Cost["output"], cache: { read: 0 as Model.Cost["cache"]["read"], write: 0 as Model.Cost["cache"]["write"] } }] });
  http.queue("GET", base, Response.json({ data: [{ id: "available" }, { id: "not-in-host-catalog" }] }));
  const request = spyOn(StdioBridge.prototype, "request").mockImplementation((async (method: string) => method === "status" ? { signed_in: true, config_match: true } : { target: base, revision: "synthetic", expires_at: Date.now() / 1000 + 60, headers: { authorization: "Bearer synthetic" } }) as StdioBridge["request"]);
  const transport = spyOn(globalThis, "fetch").mockImplementation(Object.assign(http.fetch, { preconnect: () => {} }));
  let cleanup: (() => Promise<void>) | undefined;
  try {
    if (lazy) f.connect("initial");
    cleanup = await setupVibe(f.ctx);
    if (lazy) {
      expect(f.providers).toEqual([]);
      f.readProvider();
      expect(http.requests).toHaveLength(1);
    } else {
      expect(request).not.toHaveBeenCalled(); expect(http.requests).toEqual([]);
      f.connect(event);
    }
    for (let i = 0; i < 100 && f.providers.length < 2; i++) await Promise.resolve();
    expect(f.providers.at(-1)).toMatchObject({ models: [{ id: "available", capabilities: { tools: false }, limit: { context: 2048, output: 256 } }], sourceConnection: { id: "connection-one" } });
    expect(http.requests[0]!.headers.get("authorization")).toBe("Bearer synthetic");
    // Resolving an SDK for a selected model must not reload the provider or
    // temporarily erase that model. Fetch resolves fresh credentials itself.
    const providerCount = f.providers.length;
    const helperCount = request.mock.calls.length;
    const hook = f.hooks[0]!;
    type SDKEvent = Extract<Parameters<Parameters<RegistrationContext["aisdk"]["hook"]>[1]>[0], { package: string }>;
    const sdkEvent = { model: undefined, package: "@ai-sdk/openai-compatible", options: {}, sdk: undefined } as unknown as SDKEvent;
    await (hook.callback as (event: SDKEvent) => void | Promise<void>)(sdkEvent);
    expect(f.providers).toHaveLength(providerCount);
    expect(request.mock.calls).toHaveLength(helperCount);
    expect(http.requests).toHaveLength(1);
    // Let the watcher return to its next event before disconnection.
    await Promise.resolve(); f.disconnect();
    for (let i = 0; i < 100; i++) await Promise.resolve();
    if (lazy) f.readProvider();
    expect(f.providers.at(-1)).toMatchObject({ models: [] });
  } finally { await cleanup?.(); request.mockRestore(); transport.mockRestore(); }
});
