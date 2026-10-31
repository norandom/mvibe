import { test, expect } from "bun:test";
import { Plugin } from "@opencode/plugin";
import plugin, { setupVibe } from "../src/index.js";
import type { RegistrationContext } from "../src/index.js";

function fixture(options: Plugin.Context["options"] = { audience: "https://inference.example.test", helperCommand: { executable: "uv", args: ["run", "--offline", "--no-sync", "--project", "C:/installed runtime", "-m", "mvibe_auth", "bridge"] } }) {
  const providers: unknown[] = [], methods: unknown[] = [], disposed: string[] = [];
  type ProviderEditor = Parameters<Parameters<RegistrationContext["provider"]["transform"]>[0]>[0];
  type IntegrationEditor = Parameters<Parameters<RegistrationContext["integration"]["transform"]>[0]>[0];
  // Only methods touched by setup are supplied; their signatures come from SDK.
  const providerEditor: Pick<ProviderEditor, "add"> = { add: input => { providers.push(input); } };
  const integrationEditor: Pick<IntegrationEditor, "method"> = { method: { update: input => { methods.push(input); }, list: () => [], remove: () => {} } };
  const ctx = {
    options,
    provider: { transform: async (edit: (editor: ProviderEditor) => void) => { edit(providerEditor as ProviderEditor); return { dispose: async () => { disposed.push("provider"); } }; } },
    integration: { transform: async (edit: (editor: IntegrationEditor) => void) => { edit(integrationEditor as IntegrationEditor); return { dispose: async () => { disposed.push("integration"); } }; } },
  } as RegistrationContext;
  return { ctx, providers, methods, disposed };
}

test("distinct production V2 provider registers only browser command, preserves argv", async () => {
  const f = fixture();
  expect(plugin.id).toBe("vibe-oidc");
  const cleanup = await setupVibe(f.ctx);
  expect(f.providers).toMatchObject([{ info: { id: "vibe-oidc", name: "Vibe OIDC" }, models: [] }]);
  expect(f.methods).toEqual([{ integrationID: "vibe-oidc", method: { id: "vibe-browser", type: "command", label: "Vibe OIDC", command: ["uv", "run", "--offline", "--no-sync", "--project", "C:/installed runtime", "-m", "mvibe_auth", "login"] } }]);
  await cleanup();
  await cleanup();
  expect(f.disposed).toEqual(["integration", "provider"]);
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
