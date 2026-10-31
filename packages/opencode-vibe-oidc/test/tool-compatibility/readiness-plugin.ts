/** Executed exclusively inside the explicitly launched disposable host. */
import { Plugin } from "@opencode/plugin";
import { setupVibe } from "../../src/index.js";
import { writeFileSync } from "node:fs";

export default Plugin.define({ id: "tool-readiness", async setup(ctx) {
  const original = globalThis.fetch;
  let inference = 0;
  // There is deliberately no original-fetch fallback in offline mode.
  globalThis.fetch = Object.assign(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.origin !== "https://inference.example.test" || request.headers.get("authorization") !== "Bearer synthetic-one") throw new Error("HOST_NETWORK");
    if (request.method === "GET" && url.pathname === "/v1/models") return Response.json({ data: [{ id: "synthetic-model", name: "Synthetic", capabilities: { tools: true, input: ["text"], output: ["text"] }, limit: { context: 131072, output: 4096 }, time: { released: 0 }, status: "active", cost: [{ input: 0, output: 0, cache: { read: 0, write: 0 } }] }] });
    if (request.method !== "POST" || url.pathname !== "/v1/chat/completions" || ++inference > 1) throw new Error("HOST_NETWORK");
    const chunk = (delta: unknown, finish_reason: string | null) => "data: " + JSON.stringify({ id: "readiness", object: "chat.completion.chunk", created: 0, model: "synthetic-model", choices: [{ index: 0, delta, finish_reason }] }) + "\n\n";
    return new Response(chunk({ role: "assistant", content: "owned readiness" }, null) + chunk({}, "stop") + "data: [DONE]\n\n", { headers: { "content-type": "text/event-stream" } });
  }, { preconnect() {} });
  const deny = await ctx.tool.hook("execute.before", () => { throw new Error("HOST_TOOL_DENIED"); });
  const cleanup = await setupVibe(ctx);
  const attempt = await ctx.integration.command.connect({ integrationID: "vibe-oidc", methodID: "vibe-browser", label: "synthetic" });
  const controller = new AbortController();
  void (async () => {
    try {
      const deadline = Date.now() + 20_000;
      while (!controller.signal.aborted && Date.now() < deadline) {
        const status = (await ctx.integration.command.status({ integrationID: "vibe-oidc", attemptID: attempt.data.attemptID })).data;
        if (status.status === "failed") break;
        if (status.status === "complete" && JSON.stringify(await ctx.model.list()).includes('"synthetic-model"')) {
          const result = await ctx.experimental.generate.text({ model: { providerID: "vibe-oidc", id: "synthetic-model" }, prompt: "readiness" });
          if (result.text !== "owned readiness") throw new Error("HOST_MODEL");
          writeFileSync(String(ctx.options.readiness), '{"ready":true}'); return;
        }
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      writeFileSync(String(ctx.options.readiness), '{"ready":false}');
    } catch { writeFileSync(String(ctx.options.readiness), '{"ready":false}'); }
  })();
  return async () => { controller.abort(); await deny.dispose(); await cleanup(); globalThis.fetch = original; };
} });
