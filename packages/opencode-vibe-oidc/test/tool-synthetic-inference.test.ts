import { describe, expect, test } from "bun:test";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { fixtureHash } from "./tool-compatibility/cases.js";
import { createSyntheticInference } from "./tool-compatibility/synthetic-inference.js";
import type { SyntheticPlan, SyntheticRequest } from "./tool-compatibility/synthetic-inference.js";
import type { LanguageModelV3CallOptions } from "@ai-sdk/provider";

const definition = { description: "Owned fixture", inputSchema: { type: "object" as const, properties: { nested: { type: "array" as const } }, required: ["nested"] } };
const input = { nested: [{ text: "雪😀", n: 2.5, ok: true }, null, [1, false]] };
function setup() {
  const calls = ["native-read", "mcp-read"].map((id, i) => ({ tool: { registryId: id, declaredName: "read", offeredName: `actual_${i}`, origin: "native" as const, originHash: "a".repeat(64), schemaHash: fixtureHash(definition) }, callId: `owned_call_${i}`, input: structuredClone(input), result: `fixture-${i}-雪`, validateInput: (value: unknown) => fixtureHash(value) === fixtureHash(input) }));
  const plan: SyntheticPlan = { caseId: "case", scopeHash: "a".repeat(64), sessionId: "session", chainId: "chain", model: "owned-model", calls, answer: "Observed owned results" };
  const context: SyntheticRequest = { requestId: "first", requestKind: "primary", scopeHash: plan.scopeHash, sessionId: plan.sessionId, chainId: plan.chainId, offered: calls.map(c => c.tool) };
  const body = { model: plan.model, stream: true, messages: [{ role: "user", content: "owned objective" }] as any[], tools: calls.map(c => ({ type: "function", function: { name: c.tool.offeredName, description: definition.description, parameters: definition.inputSchema } })) };
  const continuation = () => ({ ...body, messages: [...body.messages, { role: "assistant", content: null, tool_calls: calls.map(c => ({ id: c.callId, type: "function", function: { name: c.tool.offeredName, arguments: JSON.stringify(c.input) } })) }, ...calls.map(c => ({ role: "tool", tool_call_id: c.callId, content: c.result }))] });
  return { plan, context, body, continuation };
}
const invoke = (fetch: ReturnType<ReturnType<typeof createSyntheticInference>["forRequest"]>, body: unknown, url = "https://inference.example.test/v1/chat/completions") => fetch(url, { method: "POST", body: JSON.stringify(body) });
describe("owned synthetic model boundary", () => {
  test("streams observed parallel names/IDs incrementally and waits for exact real fixture results", async () => {
    const s = setup(), model = createSyntheticInference(s.plan);
    const text = await (await invoke(model.forRequest(s.context), s.body)).text();
    const chunks = text.split("\n\n").filter(x => x.startsWith("data: {")).map(x => JSON.parse(x.slice(6)));
    const calls = ["", ""];
    for (const chunk of chunks) for (const call of chunk.choices[0].delta.tool_calls ?? []) calls[call.index] += call.function.arguments ?? "";
    expect(calls.map(c => JSON.parse(c))).toEqual([input, input]);
    expect(chunks.filter(c => c.choices[0].finish_reason === "tool_calls")).toHaveLength(1);
    expect(text).not.toContain(s.plan.answer);
    const final = await (await invoke(model.forRequest({ ...s.context, requestId: "second" }), s.continuation())).text();
    expect(final).toContain(s.plan.answer); expect(final).toContain('"finish_reason":"stop"');
  });
  for (const mutation of ["missing", "crossed", "wrong-name", "wrong-input", "wrong-result", "premature-final", "duplicate"] as const) test(`rejects ${mutation} continuation`, async () => {
    const s = setup(), model = createSyntheticInference(s.plan); await (await invoke(model.forRequest(s.context), s.body)).text();
    const body = s.continuation();
    if (mutation === "missing") body.messages.pop();
    if (mutation === "crossed") body.messages[2].tool_call_id = s.plan.calls[1]!.callId;
    if (mutation === "wrong-name") body.messages[1].tool_calls[0].function.name = "private";
    if (mutation === "wrong-input") body.messages[1].tool_calls[0].function.arguments = "{}";
    if (mutation === "wrong-result") body.messages[2].content = "private-result";
    if (mutation === "premature-final") body.messages.push({ role: "assistant", content: s.plan.answer });
    if (mutation === "duplicate") body.messages.push(body.messages[2]);
    await expect(invoke(model.forRequest({ ...s.context, requestId: "second" }), body)).rejects.toThrow("synthetic_protocol");
    await expect(invoke(model.forRequest({ ...s.context, requestId: "third" }), s.continuation())).rejects.toThrow("synthetic_protocol");
  });
  test("auxiliary traffic never advances the primary chain", async () => {
    const s = setup(), model = createSyntheticInference(s.plan);
    for (const requestKind of ["title", "generate", "compaction"] as const) {
      const body = await (await invoke(model.forRequest({ ...s.context, requestId: requestKind, requestKind }), s.body)).text();
      expect(body).not.toContain('"tool_calls":'); expect(body).not.toContain(s.plan.answer);
    }
    expect(await (await invoke(model.forRequest(s.context), s.body)).text()).toContain('"finish_reason":"tool_calls"');
  });
  test("denies unobserved names, schema drift, identity drift and foreign destinations", async () => {
    for (const mutation of ["name", "schema", "identity", "destination", "model", "stream"] as const) {
      const s = setup(), model = createSyntheticInference(s.plan);
      if (mutation === "name") s.body.tools[0]!.function.name = "guessed";
      if (mutation === "schema") s.body.tools[0]!.function.parameters.required = [];
      if (mutation === "identity") s.context.offered = [{ ...s.plan.calls[0]!.tool, originHash: "b".repeat(64) }, s.plan.calls[1]!.tool];
      if (mutation === "model") s.body.model = "other";
      if (mutation === "stream") s.body.stream = false;
      await expect(invoke(model.forRequest(s.context), s.body, mutation === "destination" ? "https://private.test" : undefined)).rejects.toThrow("synthetic_protocol");
    }
  });
  test("invalid fixture inputs fail before any model response", () => {
    const s = setup(); s.plan.calls[0]!.validateInput = () => false;
    expect(() => createSyntheticInference(s.plan)).toThrow("synthetic_protocol");
  });
  test("same tool may produce distinct parallel calls and reversed results retain identity", async () => {
    const s = setup(); s.plan.calls[1]!.tool = s.plan.calls[0]!.tool;
    s.context.offered = [s.plan.calls[0]!.tool]; s.body.tools.pop();
    const model = createSyntheticInference(s.plan);
    await (await invoke(model.forRequest(s.context), s.body)).text();
    const next = s.continuation(); [next.messages[2], next.messages[3]] = [next.messages[3], next.messages[2]];
    expect(await (await invoke(model.forRequest({ ...s.context, requestId: "second" }), next)).text()).toContain(s.plan.answer);
  });
  test("bounded parse, invalid JSON and cancellation fail closed with fixed errors", async () => {
    for (const payload of ["{", JSON.stringify({ nested: "x".repeat(131073) }), JSON.stringify({ model: "private-input" })]) {
      const s = setup(), model = createSyntheticInference(s.plan);
      await expect(model.forRequest(s.context)("https://inference.example.test/v1/chat/completions", { method: "POST", body: payload })).rejects.toThrow("synthetic_protocol");
    }
    const s = setup(), model = createSyntheticInference(s.plan);
    const response = await invoke(model.forRequest(s.context), s.body); await response.body!.cancel();
    await expect(invoke(model.forRequest({ ...s.context, requestId: "second" }), s.continuation())).rejects.toThrow("synthetic_protocol");
  });
  test("request replay and unfinished streamed calls cannot produce final answers", async () => {
    for (const unfinished of [true, false]) {
      const s = setup(), model = createSyntheticInference(s.plan);
      const response = await invoke(model.forRequest(s.context), s.body);
      if (!unfinished) await response.text();
      await expect(invoke(model.forRequest({ ...s.context, requestId: unfinished ? "second" : "first" }), s.continuation())).rejects.toThrow("synthetic_protocol");
      if (unfinished) await response.body!.cancel();
    }
  });
  test("request budgets and fixture input size are bounded", async () => {
    const s = setup(), model = createSyntheticInference(s.plan);
    for (let i = 0; i < 16; i++) await (await invoke(model.forRequest({ ...s.context, requestId: `aux-${i}`, requestKind: "title" }), s.body)).text();
    await expect(invoke(model.forRequest(s.context), s.body)).rejects.toThrow("synthetic_protocol");
    const oversized = setup(); oversized.plan.calls[0]!.input = { x: "x".repeat(9000) }; oversized.plan.calls[0]!.validateInput = () => true;
    expect(() => createSyntheticInference(oversized.plan)).toThrow("synthetic_protocol");
  });
  test("aborted requests and post-final replay never restart inference", async () => {
    const s = setup(), model = createSyntheticInference(s.plan), controller = new AbortController(); controller.abort();
    await expect(model.forRequest(s.context)("https://inference.example.test/v1/chat/completions", { method: "POST", body: JSON.stringify(s.body), signal: controller.signal })).rejects.toThrow("synthetic_protocol");
    const t = setup(), complete = createSyntheticInference(t.plan);
    await (await invoke(complete.forRequest(t.context), t.body)).text();
    await (await invoke(complete.forRequest({ ...t.context, requestId: "second" }), t.continuation())).text();
    await expect(invoke(complete.forRequest({ ...t.context, requestId: "third" }), t.continuation())).rejects.toThrow("synthetic_protocol");
  });
  test("installed SDK parses incremental nested Unicode and parallel calls", async () => {
    const s = setup(), boundary = createSyntheticInference(s.plan);
    const provider = createOpenAICompatible({ name: "owned", baseURL: "https://inference.example.test/v1", fetch: boundary.forRequest(s.context) });
    const result = await provider.chatModel(s.plan.model).doStream({ prompt: [{ role: "user", content: [{ type: "text", text: "owned objective" }] }], tools: s.plan.calls.map(c => ({ type: "function", name: c.tool.offeredName!, description: definition.description, inputSchema: definition.inputSchema })) });
    const parts = []; for await (const part of result.stream) parts.push(part);
    const calls = parts.filter(p => p.type === "tool-call");
    expect(calls.map(c => JSON.parse(c.input))).toEqual([input, input]);
    expect(calls.map(c => c.toolName)).toEqual(["actual_0", "actual_1"]);
    expect(parts.some(p => p.type === "finish" && p.finishReason.unified === "tool-calls")).toBe(true);
    const next = createOpenAICompatible({ name: "owned", baseURL: "https://inference.example.test/v1", fetch: boundary.forRequest({ ...s.context, requestId: "second" }) });
    const prompt: LanguageModelV3CallOptions["prompt"] = [
      { role: "user", content: [{ type: "text", text: "owned objective" }] },
      { role: "assistant", content: s.plan.calls.map(c => ({ type: "tool-call", toolCallId: c.callId, toolName: c.tool.offeredName!, input: c.input as any })) },
      { role: "tool", content: s.plan.calls.map(c => ({ type: "tool-result", toolCallId: c.callId, toolName: c.tool.offeredName!, output: { type: "text", value: c.result } })) },
    ];
    const final = await next.chatModel(s.plan.model).doStream({ prompt, tools: s.plan.calls.map(c => ({ type: "function", name: c.tool.offeredName!, description: definition.description, inputSchema: definition.inputSchema })) });
    const finalParts = []; for await (const part of final.stream) finalParts.push(part);
    expect(finalParts.some(p => p.type === "text-delta" && p.delta === s.plan.answer)).toBe(true);
  });
});
