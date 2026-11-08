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

function settings(options: Plugin.Context["options"]): { command: HelperCommand; audience: string; existingVibe: boolean } {
  if (options.authSource !== undefined && options.authSource !== "oidc" && options.authSource !== "existing-vibe") throw new BridgeError("setup", "Configure authSource as existing-vibe or oidc.");
  const existingVibe = options.authSource === "existing-vibe";
  const value = options.helperCommand;
  const audience = options.audience;
  if (!value || typeof value !== "object" || Array.isArray(value) || typeof audience !== "string" || !audience.trim()) throw new BridgeError("setup");
  const command = value as Record<string, unknown>;
  if (typeof command.executable !== "string" || !Array.isArray(command.args) || !command.args.every((arg: unknown) => typeof arg === "string")) throw new BridgeError("setup");
  const args: string[] = command.args;
  const name = basename(command.executable);
  const uv = /^uv(\.exe)?$/i.test(name);
  if (uv ? args[0] !== "run" || !args.includes("--offline") || !args.includes("--no-sync") : !isAbsolute(command.executable) || !/^python(?:\d+(?:\.\d+)*)?(\.exe)?$/i.test(name) || /windowsapps/i.test(command.executable)) throw new BridgeError("setup");
  if (args.length < 3 || !["-m", "--module"].includes(args.at(-3)!) || args.at(-2) !== "mvibe_auth" || !(args.at(-1) === "bridge" || (existingVibe && args.at(-1) === "bridge-vibe"))) throw new BridgeError("setup", "Configure helperCommand with an installed uv-managed runtime ending in -m mvibe_auth bridge (or bridge-vibe with authSource existing-vibe).");
  return { command: { executable: command.executable, args: [...args.slice(0, -1), existingVibe ? "bridge-vibe" : "bridge"] }, audience, existingVibe };
}

/** Registration phases in setup order; a failing phase names itself (3.1). */
type RegistrationPhase = "request-context" | "provider-transform" | "integration-transform" | "aisdk-hook";

/** Labeled setup failure: the message names the phase and the underlying error;
 * the original error is attached as `cause` so it is never substituted (3.3). */
function registrationPhaseFailure(phase: RegistrationPhase, error: unknown): BridgeError {
  const detail = (error instanceof Error ? error.message : String(error)).replace(/\s+/g, " ").trim() || "unknown error";
  const failure = new BridgeError("setup", `Vibe OIDC registration failed in the ${phase} phase (${detail}); install a compatible OpenCode V2 host/plugin pair and configure an installed uv-managed helper runtime.`);
  failure.cause = error;
  return failure;
}

export async function setupVibe(ctx: RegistrationContext) {
  const { command, audience, existingVibe } = settings(ctx.options);
  const baseURL = providerBaseURL(audience);
  const bridge = new StdioBridge({ command, audience });
  const providerID = Provider.ID.make("vibe-oidc");
  const fetch = createProviderFetch(bridge, audience, globalThis.fetch, { authSource: existingVibe ? "existing-vibe" : "oidc", diagnostics: ctx.options.diagnostics === true, ...(typeof ctx.options.captureDirectory === "string" ? { captureDirectory: ctx.options.captureDirectory } : {}), captureBody: ctx.options.captureBody === true });
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
  // Each registration phase labels itself so a setup failure names the failing
  // step and keeps the original error as cause (3.1, 3.3); after the phases
  // complete, phase resets and later failures keep the generic message.
  let phase: RegistrationPhase | undefined = undefined;
  try {
    phase = "request-context";
    registrations.push(await registerRequestContext(ctx, providerID));
    phase = "provider-transform";
    registrations.push(await ctx.provider.transform(editor => {
      hostModels = editor.get?.("mistral")?.models ?? new Map();
      editor.add({ info: { ...Provider.Info.empty(providerID), name: "Mistral-Vibe", activation: "enabled", package: "aisdk:@ai-sdk/openai-compatible" }, ...catalog.snapshot });
    }));
    phase = "integration-transform";
    registrations.push(await ctx.integration.transform(editor => {
      editor.method.update({ integrationID: providerID, method: { id: "vibe-browser", type: "command", label: existingVibe ? "Use existing Vibe login" : "Mistral-Vibe", command: [command.executable, ...command.args.slice(0, -1), existingVibe ? "login-vibe" : "login"] } });
      editor.update(providerID, integration => { integration.name = "Mistral-Vibe"; });
    }));
    phase = "aisdk-hook";
    registrations.push(await ctx.aisdk.hook("sdk", async event => {
      event.options.maxRetries = 0;
      event.options.fetch = fetch;
      event.sdk = createOpenAICompatible({ name: "vibe-oidc", baseURL, fetch, transformRequestBody: mistralRequestBody });
    }, { providerID }));
    phase = undefined;
    const updates = ctx.event.subscribe({ types: ["integration.updated", "credential.updated", "credential.switched"], signal: events.signal });
    void (async () => {
      try { for await (const _event of updates) { if (events.signal.aborted) break; await catalog.refresh(); } }
      catch { if (!events.signal.aborted) console.warn("Vibe OIDC catalog updates unavailable; verify compatible OpenCode V2 versions and reload the provider."); }
    })();
    // Registration ends here: the first catalog refresh runs concurrently with
    // the host finishing plugin load, so no helper process, credential read or
    // network request can delay or abandon registration. The catalog reports
    // its own attempt failures; this defensive catch covers the unexpected
    // rejection path (for example an invalidate-then-reload failure escaping
    // the catalog's own reporting), so the provider stays registered and no
    // unhandled rejection can escape setup.
    void catalog.refresh().catch(() => console.warn("Vibe OIDC model catalog refresh failed unexpectedly; provider registration is unaffected; verify compatible OpenCode V2/bridge versions and reload the provider to retry discovery."));
  } catch (error) {
    try { await cleanup(); } catch { /* Report the fixed setup diagnostic below. */ }
    throw phase === undefined ? new BridgeError("setup", "Vibe OIDC registration failed; install a compatible OpenCode V2 host/plugin pair and configure an installed uv-managed helper runtime.") : registrationPhaseFailure(phase, error);
  }
  return cleanup;
}

export default Plugin.define({ id: "vibe-oidc", setup: setupVibe });
