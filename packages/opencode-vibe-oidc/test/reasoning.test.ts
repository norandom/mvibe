import { expect, test } from "bun:test";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import type { LanguageModelV3CallOptions } from "@ai-sdk/provider";
import { mistralRequestBody } from "../src/reasoning.js";

const thinking = { type: "thinking", thinking: [{ type: "text", text: "Inspect the fixture first.\n保持" }] };
const call = { id: "call00001", type: "function", function: { name: "read_fixture", arguments: '{"path":"hello.ps1"}' } };

test.each([{ content: "Inspecting now." }, { content: null }, { content: [{ type: "text", text: "Inspecting now.", extra: "retained" }] }])("Mistral replay preserves reasoning, content and other fields: %j", ({ content }) => {
  const assistant = { role: "assistant", content, reasoning_content: "Inspect the fixture first.\n保持", tool_calls: [call], prefix: false };
  const tool = { role: "tool", tool_call_id: call.id, content: "fixture contents" };
  const body = { model: "mistral-large-4", messages: [assistant, tool], temperature: 0.2, tools: [{ type: "function" }], extra: { retained: true } };
  const before = structuredClone(body);
  const transformed = mistralRequestBody(body);
  const chunks = content === null ? [] : typeof content === "string" ? [{ type: "text", text: content }] : content;
  expect(transformed).toEqual({ ...body, messages: [{ role: "assistant", content: [thinking, ...chunks], tool_calls: [call], prefix: false }, tool] });
  expect(body).toEqual(before);
  expect(mistralRequestBody(transformed)).toEqual(transformed);
});

test("plain text history, tool results and unrelated fields pass through", () => {
  const body = { messages: [{ role: "user", content: "hello" }, { role: "assistant", content: "world" }, { role: "tool", content: "result", reasoning_content: "not assistant" }], stream: true };
  expect(mistralRequestBody(body)).toEqual(body);
  expect(mistralRequestBody({ model: "embedding-model", input: "text" })).toEqual({ model: "embedding-model", input: "text" });
});

test.each(["generate", "stream"] as const)("installed SDK %s continues reasoning + tool history using Mistral chunks", async mode => {
  const requests: Record<string, unknown>[] = [];
  const model = createOpenAICompatible({ name: "vibe-oidc", baseURL: "https://inference.example.test/v1", transformRequestBody: mistralRequestBody,
    fetch: Object.assign(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      requests.push(body);
      if (requests.length === 1) return Response.json({ id: "initial", choices: [{ index: 0, finish_reason: "tool_calls", message: { role: "assistant", content: [thinking, { type: "text", text: "Inspecting now." }], tool_calls: [call] } }] });
      if (mode === "generate") return Response.json({ id: "continued", choices: [{ index: 0, finish_reason: "stop", message: { role: "assistant", content: "Done." } }] });
      return new Response('data: {"id":"continued","choices":[{"index":0,"delta":{"role":"assistant","content":"Done."},"finish_reason":null}]}\n\ndata: {"id":"continued","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n', { headers: { "content-type": "text/event-stream" } });
    }, { preconnect: () => {} }),
  })("mistral-large-4");
  const initial = await model.doGenerate({ prompt: [{ role: "user", content: [{ type: "text", text: "Read hello.ps1" }] }] });
  const reasoning = initial.content.find(part => part.type === "reasoning");
  const toolCall = initial.content.find(part => part.type === "tool-call");
  expect(reasoning?.text).toBe(thinking.thinking[0]!.text);
  if (!reasoning || !toolCall) throw new Error("fixture must parse reasoning and a tool call through the installed SDK");
  const options: LanguageModelV3CallOptions = {
    prompt: [
      { role: "user", content: [{ type: "text", text: "Read hello.ps1" }] },
      { role: "assistant", content: [reasoning, { type: "text", text: "Inspecting now." }, { type: "tool-call", toolCallId: toolCall.toolCallId, toolName: toolCall.toolName, input: JSON.parse(toolCall.input) }] },
      { role: "tool", content: [{ type: "tool-result", toolCallId: toolCall.toolCallId, toolName: toolCall.toolName, output: { type: "text", value: "fixture contents" } }] },
    ],
    tools: [{ type: "function", name: "read_fixture", inputSchema: { type: "object", properties: { path: { type: "string" } } } }],
    temperature: 0.2,
  };
  if (mode === "generate") {
    expect((await model.doGenerate(options)).content).toContainEqual({ type: "text", text: "Done." });
  } else {
    const { stream } = await model.doStream(options);
    const parts = [];
    for await (const part of stream) parts.push(part);
    expect(parts).toContainEqual(expect.objectContaining({ type: "text-delta", delta: "Done." }));
    expect(parts.some(part => part.type === "error")).toBe(false);
  }
  expect(requests).toHaveLength(2);
  const continuation = requests[1]!;
  expect(continuation.messages).toEqual([
    { role: "user", content: "Read hello.ps1" },
    { role: "assistant", content: [thinking, { type: "text", text: "Inspecting now." }], tool_calls: [call] },
    { role: "tool", tool_call_id: call.id, content: "fixture contents" },
  ]);
  expect(continuation.temperature).toBe(0.2);
  expect(continuation.tools).toHaveLength(1);
  expect(continuation.stream).toBe(mode === "stream" ? true : undefined);
});
