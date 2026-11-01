import { Plugin, Provider } from "@opencode/plugin";
import { basename, isAbsolute } from "node:path";
import { BridgeError, StdioBridge } from "./bridge.js";
import type { HelperCommand } from "./bridge.js";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { createProviderFetch, providerBaseURL } from "./provider.js";
import { createModelCatalog, metadataFromHostCatalog } from "./models.js";
import { mistralRequestBody } from "./reasoning.js";

import { registerRequestContext } from "./request-context.js";

export type RegistrationContext = Pick<Plugin.Context, "provider" | "integration" | "options" | "aisdk" | "event" | "session" | "app">;

function settings(options: Plugin.Context["options"]): { command: HelperCommand; audience: string } {
  const value = options.helperCommand;
  const audience = options.audience;
  if (!value || typeof value !== "object" || Array.isArray(value) || typeof audience !== "string" || !audience.trim()) throw new BridgeError("setup");
  const command = value as Record<string, unknown>;
  if (typeof command.executable !== "string" || !Array.isArray(command.args) || !command.args.every((arg: unknown) => typeof arg === "string")) throw new BridgeError("setup");
  const args: string[] = command.args;
  const name = basename(command.executable);
  const uv = /^uv(\.exe)?$/i.test(name);
  if (uv ? args[0] !== "run" || !args.includes("--offline") || !args.includes("--no-sync") : !isAbsolute(command.executable) || !/^python(?:\d+(?:\.\d+)*)?(\.exe)?$/i.test(name) || /windowsapps/i.test(command.executable)) throw new BridgeError("setup");
  if (args.length < 3 || !["-m", "--module"].includes(args.at(-3)!) || args.at(-2) !== "mvibe_auth" || args.at(-1) !== "bridge") throw new BridgeError("setup", "Configure helperCommand with an installed uv-managed runtime ending in -m mvibe_auth bridge; browser sign-in uses the matching login command.");
  return { command: { executable: command.executable, args: [...args] }, audience };
}

export async function setupVibe(ctx: RegistrationContext) {
  const { command, audience } = settings(ctx.options);
  const baseURL = providerBaseURL(audience);
  const bridge = new StdioBridge({ command, audience });
  const providerID = Provider.ID.make("vibe-oidc");
  const fetch = createProviderFetch(bridge, audience, globalThis.fetch, { diagnostics: ctx.options.diagnostics === true, ...(typeof ctx.options.captureDirectory === "string" ? { captureDirectory: ctx.options.captureDirectory } : {}), captureBody: ctx.options.captureBody === true });
  let hostModels: ReadonlyMap<string, unknown> = new Map();
  const catalog = createModelCatalog({ active: () => ctx.integration.connection.active(providerID), reload: () => ctx.provider.reload(), bridge, fetch, baseURL, metadata: () => metadataFromHostCatalog(hostModels, ctx.options.modelMetadata ?? {}), report: message => console.warn(message) });
  const events = new AbortController();
  const registrations: Array<{ dispose(): Promise<void> }> = [];
  let closing: Promise<void> | undefined;
  const cleanup = () => closing ??= (async () => {
    events.abort(); catalog.close();
    const results = await Promise.allSettled([bridge.dispose(), ...[...registrations].reverse().map(registration => registration.dispose())]);
    if (results.some(result => result.status === "rejected")) throw new BridgeError("cleanup", "Vibe OIDC cleanup failed; restart the provider before retrying.");
  })();
  try {
    registrations.push(await registerRequestContext(ctx, providerID));
    registrations.push(await ctx.provider.transform(editor => {
      hostModels = editor.get?.("mistral")?.models ?? new Map();
      editor.add({ info: { ...Provider.Info.empty(providerID), name: "Mistral-Vibe", activation: "enabled", package: "aisdk:@ai-sdk/openai-compatible" }, ...catalog.snapshot });
    }));
    registrations.push(await ctx.integration.transform(editor => {
      editor.method.update({ integrationID: providerID, method: { id: "vibe-browser", type: "command", label: "Mistral-Vibe", command: [command.executable, ...command.args.slice(0, -1), "login"] } });
    }));
    registrations.push(await ctx.aisdk.hook("sdk", async event => {
      event.options.maxRetries = 0;
      event.options.fetch = fetch;
      event.sdk = createOpenAICompatible({ name: "vibe-oidc", baseURL, fetch, transformRequestBody: mistralRequestBody });
    }, { providerID }));
    const updates = ctx.event.subscribe({ types: ["integration.updated", "credential.updated", "credential.switched"], signal: events.signal });
    void (async () => {
      try { for await (const _event of updates) { if (events.signal.aborted) break; await catalog.refresh(); } }
      catch { if (!events.signal.aborted) console.warn("Vibe OIDC catalog updates unavailable; verify compatible OpenCode V2 versions and reload the provider."); }
    })();
    await catalog.refresh();
  } catch {
    try { await cleanup(); } catch { /* Report the fixed setup diagnostic below. */ }
    throw new BridgeError("setup", "Vibe OIDC registration failed; install a compatible OpenCode V2 host/plugin pair and configure an installed uv-managed helper runtime.");
  }
  return cleanup;
}

export default Plugin.define({ id: "vibe-oidc", setup: setupVibe });
