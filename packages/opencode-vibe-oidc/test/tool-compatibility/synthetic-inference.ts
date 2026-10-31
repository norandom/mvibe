import type { ProviderFetch } from "../../src/provider.js";
import type { ToolIdentity, TraceBase } from "./contracts.js";
import { parseToolIdentity } from "./contracts.js";
import { fixtureHash } from "./cases.js";

export type SyntheticCall = { tool: ToolIdentity; callId: string; input: unknown; result: string; validateInput: (input: unknown) => boolean };
export type SyntheticPlan = { caseId: string; scopeHash: string; sessionId: string; chainId: string; model: string; calls: readonly SyntheticCall[]; answer: string };
export type SyntheticRequest = { requestId: string; requestKind: TraceBase["requestKind"]; scopeHash: string; sessionId: string; chainId: string; offered: readonly ToolIdentity[] };
const fail = (): never => { throw new Error("synthetic_protocol"); };
const id = (value: unknown): void => { if (typeof value !== "string" || !/^[a-zA-Z0-9_.:-]{1,160}$/.test(value)) fail(); };
const MAX_BYTES = 131072;
async function readBody(request: Request): Promise<any> {
  const reader = request.body?.getReader(); if (!reader) return fail();
  let size = 0; const chunks: Uint8Array[] = [];
  try {
    while (true) {
      if (request.signal.aborted) fail();
      const { value, done } = await reader.read(); if (done) break;
      size += value.length; if (size > MAX_BYTES) fail(); chunks.push(value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const body = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    fixtureHash(body); return body;
  } catch { await reader.cancel().catch(() => {}); return fail(); }
  finally { reader.releaseLock(); }
}

/** Owned MODEL boundary only. Trusted fixture builders supply validators/results;
 * host adapters supply observed metadata. Neither constitutes executor evidence.
 * The returned fetch has no fallback, auth handling, tool execution or retries. */
export function createSyntheticInference(source: SyntheticPlan): { forRequest(context: SyntheticRequest): ProviderFetch & Pick<typeof fetch, "preconnect"> } {
  try {
    for (const value of [source.caseId, source.sessionId, source.chainId, source.model]) id(value);
    if (!/^[a-f0-9]{64}$/.test(source.scopeHash) || !source.calls.length || source.calls.length > 16 || typeof source.answer !== "string" || source.answer.length > 4096) fail();
    const calls = source.calls.map(c => {
      const tool = parseToolIdentity(c.tool); id(c.callId);
      if (!tool.offeredName || tool.origin === "unknown" || !tool.originHash || typeof c.result !== "string" || c.result.length > 16384) fail();
      fixtureHash(c.input);
      const input = structuredClone(c.input), before = fixtureHash(input);
      if (JSON.stringify(input).length > 8192) fail();
      if (c.validateInput(input) !== true || fixtureHash(input) !== before) fail();
      return { tool, callId: c.callId, input, inputHash: before, result: c.result };
    });
    if (new Set(calls.map(c => c.callId)).size !== calls.length || calls.reduce((n, c) => n + JSON.stringify(c.input).length, 0) > 32768) fail();
    const plan = { ...source, calls }; // snapshot only; validator functions are not retained
    let phase: "initial" | "streaming" | "awaiting" | "finished" | "failed" = "initial";
    let busy = false, count = 0, initialHistoryHash = "";
    const seen = new Set<string>();
    function response(parts: unknown[], signal: AbortSignal, complete: () => void): Response {
      let index = 0;
      const encoder = new TextEncoder();
      const abort = () => { phase = "failed"; };
      signal.addEventListener("abort", abort, { once: true });
      const body = new ReadableStream<Uint8Array>({
        pull(controller) {
          if (signal.aborted) { phase = "failed"; signal.removeEventListener("abort", abort); controller.error(new Error("synthetic_protocol")); return; }
          if (index < parts.length) controller.enqueue(encoder.encode("data: " + JSON.stringify(parts[index++]) + "\n\n"));
          else { controller.enqueue(encoder.encode("data: [DONE]\n\n")); controller.close(); signal.removeEventListener("abort", abort); complete(); }
        },
        cancel() { phase = "failed"; signal.removeEventListener("abort", abort); },
      }, { highWaterMark: 0 });
      return new Response(body, { headers: { "content-type": "text/event-stream", "x-should-retry": "false" } });
    }
    const chunk = (delta: unknown, finish_reason: "tool_calls" | "stop" | null) => ({ id: "owned-completion", object: "chat.completion.chunk", created: 0, model: plan.model, choices: [{ index: 0, delta, finish_reason }] });
    return { forRequest(context) {
      // Observation adapters pass metadata explicitly, never through credential headers.
      let ctx: SyntheticRequest;
      try { fixtureHash(context); ctx = structuredClone(context); } catch { return Object.assign(async () => fail(), { preconnect() {} }); }
      return Object.assign(async (input: RequestInfo | URL, init?: RequestInit) => {
        try {
          if (busy || phase === "failed" || phase === "finished" || ++count > 16) fail();
          busy = true;
          id(ctx.requestId);
          if (seen.has(ctx.requestId) || ctx.scopeHash !== plan.scopeHash || ctx.sessionId !== plan.sessionId || ctx.chainId !== plan.chainId || !["primary", "title", "generate", "compaction"].includes(ctx.requestKind)) fail();
          seen.add(ctx.requestId);
          const request = new Request(input, init), url = new URL(request.url);
          if (request.signal.aborted || request.method !== "POST" || url.href !== "https://inference.example.test/v1/chat/completions") fail();
          const body = await readBody(request);
          if (body.model !== plan.model || body.stream !== true || !Array.isArray(body.messages) || !body.messages.length || body.messages.length > 128) fail();
          if (ctx.requestKind !== "primary") return response([chunk({ role: "assistant", content: `Owned ${ctx.requestKind}` }, null), chunk({}, "stop")], request.signal, () => {});
          if (!Array.isArray(body.tools) || body.tools.length > 256 || ctx.offered.length > 256) fail();
          const names = body.tools.map((t: any) => t?.function?.name);
          if (new Set(names).size !== names.length) fail();
          for (const call of calls) {
            const observed = ctx.offered.filter(t => t.offeredName === call.tool.offeredName);
            if (observed.length !== 1 || fixtureHash(parseToolIdentity(observed[0])) !== fixtureHash(call.tool)) fail();
            const wire = body.tools.find((t: any) => t?.function?.name === call.tool.offeredName);
            if (!wire || wire.type !== "function" || fixtureHash({ description: wire.function.description ?? "", inputSchema: wire.function.parameters }) !== call.tool.schemaHash) fail();
          }
          if (phase === "initial") {
            if (body.messages.some((m: any) => m.role === "tool" || m.tool_calls)) fail();
            initialHistoryHash = fixtureHash(body.messages);
            const parts: unknown[] = [chunk({ role: "assistant" }, null)];
            calls.forEach((c, index) => parts.push(chunk({ tool_calls: [{ index, id: c.callId, type: "function", function: { name: c.tool.offeredName, arguments: "" } }] }, null)));
            const argumentsParts = calls.map(c => Array.from(JSON.stringify(c.input)));
            for (let offset = 0; argumentsParts.some(a => offset < a.length); offset += 4) {
              argumentsParts.forEach((a, index) => { if (offset < a.length) parts.push(chunk({ tool_calls: [{ index, function: { arguments: a.slice(offset, offset + 4).join("") } }] }, null)); });
            }
            parts.push(chunk({}, "tool_calls")); phase = "streaming";
            return response(parts, request.signal, () => { if (phase !== "failed") phase = "awaiting"; });
          }
          if (phase !== "awaiting") fail();
          const tailStart = body.messages.length - calls.length - 1;
          if (tailStart < 1 || fixtureHash(body.messages.slice(0, tailStart)) !== initialHistoryHash) fail();
          const assistant = body.messages[tailStart];
          if (assistant.role !== "assistant" || (assistant.content !== null && assistant.content !== "" && assistant.content !== undefined) || !Array.isArray(assistant.tool_calls) || assistant.tool_calls.length !== calls.length) fail();
          const resultIds = new Set<string>();
          for (const c of calls) {
            const matched = assistant.tool_calls.filter((t: any) => t.id === c.callId);
            if (matched.length !== 1 || matched[0].type !== "function" || matched[0].function?.name !== c.tool.offeredName || typeof matched[0].function.arguments !== "string" || fixtureHash(JSON.parse(matched[0].function.arguments)) !== c.inputHash) fail();
            const results = body.messages.slice(tailStart + 1).filter((m: any) => m.role === "tool" && m.tool_call_id === c.callId);
            if (results.length !== 1 || results[0].content !== c.result || (results[0].name !== undefined && results[0].name !== c.tool.offeredName) || resultIds.has(c.callId)) fail();
            resultIds.add(c.callId);
          }
          phase = "streaming";
          return response([chunk({ role: "assistant", content: plan.answer }, null), chunk({}, "stop")], request.signal, () => { if (phase !== "failed") phase = "finished"; });
        } catch { phase = "failed"; return fail(); }
        finally { busy = false; }
      }, { preconnect() {} });
    } };
  } catch { return fail(); }
}
