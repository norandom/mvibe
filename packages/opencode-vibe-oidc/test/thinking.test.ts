import { expect, test } from "bun:test";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import type { LanguageModelV3CallOptions } from "@ai-sdk/provider";
import { mistralRequestBody } from "../src/reasoning.js";
import { reasoningSupported, variantsFor } from "../src/thinking.js";

const thinking = { type: "thinking", thinking: [{ type: "text", text: "Inspect first." }] };

function assistantMessage(extra: Record<string, unknown> = {}) {
  return { role: "assistant", content: "Answer.", reasoning_content: "Inspect first.", ...extra };
}

test("curated variants match the Vibe reference rows and carry only a settings overlay", () => {
  const large4 = variantsFor("mistral-large-4");
  expect(large4.map(variant => String(variant.id))).toEqual(["low", "high", "max"]);
  expect(large4.map(variant => variant.settings)).toEqual([{ reasoningEffort: "low" }, { reasoningEffort: "high" }, { reasoningEffort: "max" }]);
  for (const variant of large4) expect(Object.keys(variant).sort()).toEqual(["id", "settings"]);
  // Aliases share the row; large-4 rejects "none", so no off variant.
  expect(variantsFor("mistral-large-4-0-8").map(variant => String(variant.id))).toEqual(["low", "high", "max"]);
  const medium = variantsFor("mistral-medium-latest");
  expect(medium.map(variant => String(variant.id))).toEqual(["high", "off"]);
  expect(medium[1]!.settings).toEqual({ reasoningEffort: "none" });
  // Models known not to reason, and unknown models, offer nothing.
  expect(variantsFor("mistral-large-2512")).toEqual([]);
  expect(variantsFor("mistral-large-latest")).toEqual([]);
  expect(variantsFor("mistral-small-latest")).toEqual([]);
  expect(variantsFor("codestral-latest")).toEqual([]);
  const glm = variantsFor("zai-glm-5-3");
  expect(glm.map(variant => String(variant.id))).toEqual(["low", "high", "max"]);
});

test("reasoning support requires a curated row and an active level", () => {
  expect(reasoningSupported("mistral-large-4")).toBe(true);
  expect(reasoningSupported("vibe-oidc/mistral-large-4")).toBe(true);
  expect(reasoningSupported("mistral-large-4", "high")).toBe(true);
  expect(reasoningSupported("mistral-large-4", "none")).toBe(false);
  expect(reasoningSupported("mistral-medium-latest", "none")).toBe(false);
  expect(reasoningSupported("mistral-large-2512")).toBe(false);
  expect(reasoningSupported("mistral-large-2512", "high")).toBe(false);
  // A requested effort does not establish support for replay on an unknown model.
  expect(reasoningSupported("some-new-model")).toBe(false);
  expect(reasoningSupported("some-new-model", "none")).toBe(false);
  expect(reasoningSupported("some-new-model", "high")).toBe(false);
});

test.each(["high", "none"])("reasoning %s preserves escaped text and tool history", level => {
  const content = 'Path C:\\work\\file.json; quote "hello"; newline\nUnicode: café';
  const tool_calls = [{ id: "call00001", type: "function", function: { name: "read", arguments: JSON.stringify({ path: content }) } }];
  const tool = { role: "tool", tool_call_id: "call00001", content };
  const request = { model: "mistral-medium-latest", reasoning_effort: level, messages: [assistantMessage({ content, tool_calls }), tool] };
  const wire = JSON.parse(JSON.stringify(mistralRequestBody(request)));
  expect(wire.messages[0].tool_calls).toEqual(tool_calls);
  expect(wire.messages[1]).toEqual(tool);
  expect(wire.messages[0].content).toEqual(level === "none" ? content : [thinking, { type: "text", text: content }]);
  expect(request.messages[0]).toHaveProperty("reasoning_content", "Inspect first.");
});

test("transform preserves a variant-merged reasoning_effort field", () => {
  const body = { model: "mistral-large-4", reasoning_effort: "high", messages: [{ role: "user", content: "hi" }], stream: true };
  const transformed = mistralRequestBody(body);
  expect(transformed.reasoning_effort).toBe("high");
  expect(transformed.stream).toBe(true);
  expect(body.reasoning_effort).toBe("high");
});

test("reasoning replay is gated by model and level", () => {
  const replayBody = { model: "mistral-large-4", messages: [assistantMessage()] };
  expect(mistralRequestBody(replayBody).messages).toEqual([{ role: "assistant", content: [thinking, { type: "text", text: "Answer." }] }]);
  const offBody = { model: "mistral-large-4", reasoning_effort: "none", messages: [assistantMessage()] };
  expect(mistralRequestBody(offBody).messages).toEqual([{ role: "assistant", content: "Answer." }]);
  const noReasoningBody = { model: "mistral-large-2512", messages: [assistantMessage()] };
  expect(mistralRequestBody(noReasoningBody).messages).toEqual([{ role: "assistant", content: "Answer." }]);
  const unknownPlain = { model: "some-new-model", messages: [assistantMessage()] };
  expect(mistralRequestBody(unknownPlain).messages).toEqual([{ role: "assistant", content: "Answer." }]);
  const unknownEffort = { model: "some-new-model", reasoning_effort: "high", messages: [assistantMessage()] };
  expect(mistralRequestBody(unknownEffort).messages).toEqual([{ role: "assistant", content: "Answer." }]);
  // No model field keeps the historical replay behavior.
  const noModel = { messages: [assistantMessage()] };
  expect(mistralRequestBody(noModel).messages).toEqual([{ role: "assistant", content: [thinking, { type: "text", text: "Answer." }] }]);
});

test.each(["generate", "stream"] as const)("installed SDK %s forwards reasoningEffort as reasoning_effort through the transform", async mode => {
  const requests: Record<string, unknown>[] = [];
  const model = createOpenAICompatible({ name: "vibe-oidc", baseURL: "https://inference.example.test/v1", transformRequestBody: mistralRequestBody,
    fetch: Object.assign(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      if (mode === "generate") return Response.json({ id: "r", choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: "ok" } }] });
      return new Response('data: {"id":"r","choices":[{"index":0,"delta":{"role":"assistant","content":"ok"},"finish_reason":null}]}\n\ndata: {"id":"r","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n', { headers: { "content-type": "text/event-stream" } });
    }, { preconnect: () => {} }),
  })("mistral-large-4");
  const options: LanguageModelV3CallOptions = {
    prompt: [{ role: "user", content: [{ type: "text", text: "hello" }] }],
    providerOptions: { "vibe-oidc": { reasoningEffort: "high" } },
  };
  if (mode === "generate") await model.doGenerate(options);
  else { const { stream } = await model.doStream(options); for await (const _part of stream) { /* drain */ } }
  expect(requests).toHaveLength(1);
  expect(requests[0]!.reasoning_effort).toBe("high");
  expect(requests[0]!.model).toBe("mistral-large-4");
  // Without the option no field is added.
  if (mode === "generate") {
    await model.doGenerate({ prompt: [{ role: "user", content: [{ type: "text", text: "hello" }] }] });
    expect(requests).toHaveLength(2);
    expect("reasoning_effort" in requests[1]!).toBe(false);
  }
});
