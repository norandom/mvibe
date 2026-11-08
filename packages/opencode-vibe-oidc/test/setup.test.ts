import { test, expect, spyOn } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import type { Plugin } from "@opencode/plugin";
import { Model, Provider } from "@opencode/plugin";

import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { setupVibe } from "../src/index.js";
import type { RegistrationContext } from "../src/index.js";
import { BridgeError, StdioBridge } from "../src/bridge.js";
import fixture from "./synthetic-plugin.js";

test("adapter model uses the V3 contract required by the V2 host", () => {
  const model = createOpenAICompatible({ name: "setup", baseURL: "http://127.0.0.1:1" }).chatModel("synthetic");
  expect(model.specificationVersion).toBe("v3");
});

test("synthetic setup returns cleanup disposing each registration", async () => {
  const root = mkdtempSync(join(tmpdir(), "vibe-sdk-contract-"));
  const disposed: string[] = [];
  const registration = (name: string) => Promise.resolve({ dispose: async () => { disposed.push(name); } });
  const context = {
    options: { evidence: join(root, "events") },
    provider: { transform: () => registration("provider") },
    integration: { transform: () => registration("integration") },
    aisdk: { hook: (_name: string, _callback: unknown, options: { providerID: string }) => {
      expect(options.providerID).toBe("vibe-setup-synthetic");
      return registration("sdk");
    } },
  } as unknown as Plugin.Context;
  try {
    const cleanup = await fixture.setup(context);
    expect(typeof cleanup).toBe("function");
    if (cleanup) await cleanup();
    expect(disposed).toEqual(["sdk", "integration", "provider"]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

/** Fake Plugin.Context construction mirroring the registration-test fixture:
 * only host surfaces touched by setup are supplied; signatures come from SDK. */
function registrationFixture(options: Plugin.Context["options"] = { audience: "https://inference.example.test", helperCommand: { executable: "uv", args: ["run", "--offline", "--no-sync", "--project", "C:/installed runtime", "-m", "mvibe_auth", "bridge"] } }, lazyProvider = false) {
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
  const integrations = new Map<string, Parameters<Parameters<IntegrationEditor["update"]>[1]>[0]>();
  const providerEditor: Pick<ProviderEditor, "add" | "get"> = {
    add: input => { providers.push(input); },
    get: id => id === "mistral" ? { provider: Provider.Info.empty(Provider.ID.make("mistral")), models: hostModels } : undefined,
  };
  const integrationEditor: Pick<IntegrationEditor, "method" | "update"> = {
    method: { update: input => { methods.push(input); if (!integrations.has(input.integrationID)) integrations.set(input.integrationID, { id: input.integrationID, name: input.integrationID }); }, list: () => [], remove: () => {} },
    update: (id, update) => { const integration = integrations.get(id); if (integration) update(integration); },
  };
  const ctx = {
    options,
    app: { version: "2.2.5" },
    session: { hook: async () => ({ dispose: async () => {} }) },
    event: { subscribe: async function* (options: { signal?: AbortSignal; types?: string[] }) { while (!options?.signal?.aborted) { if (!pending.length) await new Promise<void>(resolve => { notify = resolve; options?.signal?.addEventListener("abort", () => resolve(), { once: true }); }); if (options?.signal?.aborted) break; const type = pending.shift()!; if (!options.types || options.types.includes(type)) yield { type }; } } },
    aisdk: { hook: async (name: string, callback: unknown, options: unknown) => { hooks.push({ name, callback, options }); return { dispose: async () => { disposed.push("aisdk"); } }; } },
    provider: { reload: async () => { if (!lazyProvider) editorCallback?.(providerEditor as ProviderEditor); }, transform: async (edit: (editor: ProviderEditor) => void) => { editorCallback = edit; if (!lazyProvider) edit(providerEditor as ProviderEditor); return { dispose: async () => { disposed.push("provider"); } }; } },
    integration: { connection: { active: async () => active }, transform: async (edit: (editor: IntegrationEditor) => void) => { edit(integrationEditor as IntegrationEditor); return { dispose: async () => { disposed.push("integration"); } }; } },
  } as unknown as RegistrationContext;
  return { ctx, providers, methods, disposed, hooks, hostModels, integrations, readProvider: () => editorCallback?.(providerEditor as ProviderEditor), connect: (event = "integration.updated") => { active = { type: "credential", id: "connection-one", label: "Vibe", method: "key" }; emit(event); }, disconnect: (event = "integration.updated") => { active = undefined; emit("credential.switched"); } };
}

test("setup resolves while the first model-catalog refresh is gated at the helper boundary", async () => {
  const f = registrationFixture();
  f.connect("initial");
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const helperRequests: string[] = [];
  // The helper process is reached only through bridge.request, so this spy is
  // the spawn boundary: any registration-path spawn attempt is recorded here
  // (and the offline test preload blocks real process creation outright).
  const request = spyOn(StdioBridge.prototype, "request").mockImplementation(((method: string) => {
    helperRequests.push(method);
    return gate.then(() => ({ signed_in: false, config_match: true }));
  }) as StdioBridge["request"]);
  const warn = spyOn(console, "warn").mockImplementation(() => {});
  let bounded!: ReturnType<typeof setTimeout>;
  const unresolved = new Promise<"unresolved">(resolve => { bounded = setTimeout(() => resolve("unresolved"), 500); });
  let helperRequestsAtSetup: string[] | undefined;
  const setup = setupVibe(f.ctx).then(cleanup => { helperRequestsAtSetup = [...helperRequests]; return cleanup; });
  let cleanup: (() => Promise<void>) | undefined;
  try {
    const outcome = await Promise.race([setup.then(() => "resolved" as const), unresolved]);
    expect(outcome).toBe("resolved");
    // No helper round-trip — therefore no helper process — before setup returned.
    expect(helperRequestsAtSetup).toEqual([]);
    // Registration completed with the pending empty catalog (1.3 pre-publish state).
    expect(f.providers).toMatchObject([{ info: { id: "vibe-oidc", name: "Mistral-Vibe" }, models: [] }]);
    expect(warn.mock.calls.some(call => String(call[0]).includes("Model discovery pending"))).toBe(true);
    // Setup has already resolved while the started first refresh stays parked
    // on the gated helper status round-trip.
    for (let i = 0; i < 100 && helperRequests.length < 1; i++) await Promise.resolve();
    expect(helperRequests).toEqual(["status"]);
    release();
    for (let i = 0; i < 100 && !warn.mock.calls.some(call => String(call[0]).includes("connect using Vibe OIDC")); i++) await Promise.resolve();
    // Degraded mode (4.1): the auth diagnostic is reported by the catalog and
    // the provider stays registered; setup itself never waited nor failed.
    expect(f.providers).toMatchObject([{ info: { id: "vibe-oidc", name: "Mistral-Vibe" } }]);
    cleanup = await setup;
    await cleanup();
    await cleanup();
    expect(f.disposed).toEqual(["aisdk", "integration", "provider"]);
  } finally {
    clearTimeout(bounded);
    release();
    await setup.then(done => done(), () => {});
    request.mockRestore();
    warn.mockRestore();
  }
});

test("unexpected first-refresh rejection is reported without abandoning registration", async () => {
  const f = registrationFixture();
  f.connect("initial");
  const base = "https://inference.example.test/v1/models";
  const request = spyOn(StdioBridge.prototype, "request").mockImplementation((async (method: string) => method === "status" ? { signed_in: true, config_match: true } : { target: base, revision: "synthetic", expires_at: Date.now() / 1000 + 60, metadata: { call_source: "vibe_code" }, headers: { authorization: "Bearer synthetic" } }) as StdioBridge["request"]);
  const transport = spyOn(globalThis, "fetch").mockImplementation(Object.assign(async () => Response.json({ data: [{ id: "available" }] }), { preconnect: () => {} }));
  // A host-side reload failure escapes the catalog's own reporting contract —
  // exactly the unexpected refresh-loop rejection the defensive catch reports.
  Object.assign(f.ctx.provider, { reload: async () => { throw new Error("SECRET reload failure"); } });
  const warn = spyOn(console, "warn").mockImplementation(() => {});
  const reported = () => warn.mock.calls.some(call => String(call[0]).includes("refresh failed unexpectedly"));
  let cleanup: (() => Promise<void>) | undefined;
  try {
    cleanup = await setupVibe(f.ctx);
    expect(f.providers).toMatchObject([{ info: { id: "vibe-oidc", name: "Mistral-Vibe" }, models: [] }]);
    for (let i = 0; i < 200 && !reported(); i++) await Promise.resolve();
    expect(reported()).toBe(true);
    expect(warn.mock.calls.map(call => String(call[0])).join()).not.toContain("SECRET");
    // The provider stays registered and teardown stays clean (4.1).
    expect(f.providers).toHaveLength(1);
    await cleanup();
    expect(f.disposed).toEqual(["aisdk", "integration", "provider"]);
  } finally { await cleanup?.(); request.mockRestore(); transport.mockRestore(); warn.mockRestore(); }
});

/** Captures the rejection of a setup promise that must fail (fail-fast if it resolves). */
async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(() => { throw new Error("expected setupVibe to reject"); }, error => error);
}

/** 3.1/3.3: the thrown BridgeError names the failing registration phase, stays
 * single-line, and keeps the underlying sentinel error as its cause. */
function expectPhaseFailure(failure: unknown, phase: string, sentinel: Error): void {
  expect(failure).toBeInstanceOf(BridgeError);
  if (!(failure instanceof BridgeError)) throw new Error(`expected a BridgeError naming the ${phase} phase`);
  expect(failure.code).toBe("setup");
  expect(failure.message).toContain(phase);
  expect(failure.message).toContain(sentinel.message);
  expect(failure.message).not.toContain("\n");
  expect(failure.cause).toBe(sentinel);
}

test("request-context phase failure names the phase and attaches the cause", async () => {
  const f = registrationFixture();
  const sentinel = new Error("synthetic request-context rejection");
  Object.assign(f.ctx, { session: { hook: async () => { throw sentinel; } } });
  const warn = spyOn(console, "warn").mockImplementation(() => {});
  try {
    expectPhaseFailure(await rejectionOf(setupVibe(f.ctx)), "request-context", sentinel);
    // Cleanup semantics unchanged: no registration completed before the failure.
    expect(f.disposed).toEqual([]);
  } finally { warn.mockRestore(); }
});

test("provider-transform phase failure names the phase and attaches the cause", async () => {
  const f = registrationFixture();
  const sentinel = new Error("synthetic provider-transform rejection");
  Object.assign(f.ctx, { session: { hook: async () => ({ dispose: async () => { f.disposed.push("request-context"); } }) } });
  Object.assign(f.ctx.provider, { transform: async () => { throw sentinel; } });
  const warn = spyOn(console, "warn").mockImplementation(() => {});
  try {
    expectPhaseFailure(await rejectionOf(setupVibe(f.ctx)), "provider-transform", sentinel);
    // Earlier registrations are still disposed in reverse order (cleanup
    // unchanged); request-context registers two session hooks.
    expect(f.disposed).toEqual(["request-context", "request-context"]);
  } finally { warn.mockRestore(); }
});

test("integration-transform phase failure names the phase and attaches the cause", async () => {
  const f = registrationFixture();
  const sentinel = new Error("synthetic integration-transform rejection");
  Object.assign(f.ctx, { session: { hook: async () => ({ dispose: async () => { f.disposed.push("request-context"); } }) } });
  Object.assign(f.ctx.integration, { transform: async () => { throw sentinel; } });
  const warn = spyOn(console, "warn").mockImplementation(() => {});
  try {
    expectPhaseFailure(await rejectionOf(setupVibe(f.ctx)), "integration-transform", sentinel);
    expect(f.disposed).toEqual(["provider", "request-context", "request-context"]);
  } finally { warn.mockRestore(); }
});

test("aisdk-hook phase failure names the phase and attaches the cause", async () => {
  const f = registrationFixture();
  const sentinel = new Error("synthetic aisdk-hook rejection");
  Object.assign(f.ctx, { session: { hook: async () => ({ dispose: async () => { f.disposed.push("request-context"); } }) } });
  Object.assign(f.ctx.aisdk, { hook: async () => { throw sentinel; } });
  const warn = spyOn(console, "warn").mockImplementation(() => {});
  try {
    expectPhaseFailure(await rejectionOf(setupVibe(f.ctx)), "aisdk-hook", sentinel);
    expect(f.disposed).toEqual(["integration", "provider", "request-context", "request-context"]);
  } finally { warn.mockRestore(); }
});

