import { test, expect } from "bun:test";
import { Plugin } from "@opencode/plugin";
import plugin, { setupVibe } from "../src/index.js";
import type { RegistrationContext } from "../src/index.js";

function fixture(options: Plugin.Context["options"] = { audience: "https://inference.example.test", helperCommand: { executable: "uv", args: ["run", "--offline", "--no-sync", "--project", "C:/installed runtime", "-m", "mvibe_auth", "bridge"] } }) {
  const providers: unknown[] = [], methods: unknown[] = [], disposed: string[] = [];
  const hooks: Array<{ name: string; options: unknown; callback: unknown }> = [];
  type ProviderEditor = Parameters<Parameters<RegistrationContext["provider"]["transform"]>[0]>[0];
  type IntegrationEditor = Parameters<Parameters<RegistrationContext["integration"]["transform"]>[0]>[0];
  // Only methods touched by setup are supplied; their signatures come from SDK.
  const providerEditor: Pick<ProviderEditor, "add"> = { add: input => { providers.push(input); } };
  const integrationEditor: Pick<IntegrationEditor, "method"> = { method: { update: input => { methods.push(input); }, list: () => [], remove: () => {} } };
  const ctx = {
    options,
    aisdk: { hook: async (name, callback, options) => { hooks.push({ name, callback, options }); return { dispose: async () => { disposed.push("aisdk"); } }; } },
    provider: { transform: async (edit: (editor: ProviderEditor) => void) => { edit(providerEditor as ProviderEditor); return { dispose: async () => { disposed.push("provider"); } }; } },
    integration: { transform: async (edit: (editor: IntegrationEditor) => void) => { edit(integrationEditor as IntegrationEditor); return { dispose: async () => { disposed.push("integration"); } }; } },
  } as RegistrationContext;
  return { ctx, providers, methods, disposed, hooks };
}

test("distinct production V2 provider registers only browser command, preserves argv", async () => {
  const f = fixture();
  expect(plugin.id).toBe("vibe-oidc");
  const cleanup = await setupVibe(f.ctx);
  expect(f.providers).toMatchObject([{ info: { id: "vibe-oidc", name: "Vibe OIDC" }, models: [] }]);
  expect(f.methods).toEqual([{ integrationID: "vibe-oidc", method: { id: "vibe-browser", type: "command", label: "Vibe OIDC", command: ["uv", "run", "--offline", "--no-sync", "--project", "C:/installed runtime", "-m", "mvibe_auth", "login"] } }]);
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
