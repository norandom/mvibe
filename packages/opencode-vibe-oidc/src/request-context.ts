import type { Plugin, Provider } from "@opencode/plugin";
import type { RequestContext } from "./protocol.js";

export const REQUEST_CONTEXT_HEADER = "x-mvibe-request-context";

/** IDs and iteration counters are scoped to the host session, never the SDK. */
export async function registerRequestContext(ctx: Pick<Plugin.Context, "session" | "app">, providerID: Provider.ID) {
  const turns = new Map<string, { message_id?: string; iteration: number }>();
  const registrations: Array<{ dispose(): Promise<void> }> = [];
  try {
    // Context is the active request history, unlike prompt (which can enqueue a
    // future turn while an earlier turn is still running).
    registrations.push(await ctx.session.hook("context", event => {
      let userIndex = -1;
      for (let index = event.messages.length - 1; index >= 0; index--) {
        if (event.messages[index]?.role === "user") { userIndex = index; break; }
      }
      const user = event.messages[userIndex];
      turns.set(event.sessionID, {
        ...(user?.id ? { message_id: user.id } : {}),
        iteration: event.messages.slice(userIndex + 1).some(message => message.role === "assistant" || message.role === "tool") ? 1 : 0,
      });
    }, { providerID }));
    registrations.push(await ctx.session.hook("model.request", event => {
      const turn = turns.get(event.sessionID);
      const context: RequestContext = {
        session_id: event.sessionID,
        ...(event.kind === "primary" && turn?.message_id ? { message_id: turn.message_id } : {}),
        client_name: "opencode", client_version: ctx.app.version,
        agent_entrypoint: "programmatic", agent_version: ctx.app.version,
        purpose: event.kind === "primary" ? "agent" : event.kind,
        iteration: event.kind === "primary" ? turn?.iteration ?? 0 : 0,
      };
      event.headers[REQUEST_CONTEXT_HEADER] = JSON.stringify(context);
    }, { providerID }));
  } catch (error) {
    turns.clear();
    await Promise.allSettled(registrations.map(registration => registration.dispose()));
    throw error;
  }
  return { dispose: async () => { turns.clear(); await Promise.all(registrations.map(r => r.dispose())); } };
}
