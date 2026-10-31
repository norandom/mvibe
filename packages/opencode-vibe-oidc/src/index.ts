import { Plugin, Provider } from "@opencode/plugin";
import { basename, isAbsolute } from "node:path";
import { BridgeError, StdioBridge } from "./bridge.js";
import type { HelperCommand } from "./bridge.js";

export type RegistrationContext = Pick<Plugin.Context, "provider" | "integration" | "options">;

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
  const bridge = new StdioBridge({ command, audience });
  const registrations: Array<{ dispose(): Promise<void> }> = [];
  let closing: Promise<void> | undefined;
  const cleanup = () => closing ??= (async () => {
    const results = await Promise.allSettled([bridge.dispose(), ...[...registrations].reverse().map(registration => registration.dispose())]);
    if (results.some(result => result.status === "rejected")) throw new BridgeError("cleanup", "Vibe OIDC cleanup failed; restart the provider before retrying.");
  })();
  try {
    const providerID = Provider.ID.make("vibe-oidc");
    registrations.push(await ctx.provider.transform(editor => {
      editor.add({ info: { ...Provider.Info.empty(providerID), name: "Vibe OIDC", activation: "enabled", package: "@ai-sdk/openai-compatible" }, models: [] });
    }));
    registrations.push(await ctx.integration.transform(editor => {
      editor.method.update({ integrationID: providerID, method: { id: "vibe-browser", type: "command", label: "Vibe OIDC", command: [command.executable, ...command.args.slice(0, -1), "login"] } });
    }));
  } catch {
    try { await cleanup(); } catch { /* Report the fixed setup diagnostic below. */ }
    throw new BridgeError("setup", "Vibe OIDC registration failed; install a compatible OpenCode V2 host/plugin pair and configure an installed uv-managed helper runtime.");
  }
  return cleanup;
}

export default Plugin.define({ id: "vibe-oidc", setup: setupVibe });
