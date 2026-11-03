import { reasoningSupported } from "./thinking.js";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// Mistral requires reasoning in content ThinkChunks when replaying assistant
// history: https://docs.mistral.ai/studio/conversations/reasoning
// The replay happens only for models that take reasoning input (the Vibe
// reference's `takes_reasoning_input`): the request's own `reasoning_effort`
// field (emitted by the SDK from the selected variant's settings overlay)
// and the model decide.
export function mistralRequestBody(body: Record<string, unknown>): Record<string, unknown> {
  if (!Array.isArray(body.messages)) return body;
  const model = typeof body.model === "string" && body.model.length > 0 ? body.model : "";
  const replay = model.length > 0 ? reasoningSupported(model, body.reasoning_effort) : true;
  return { ...body, messages: body.messages.map((message: unknown) => {
    if (!isRecord(message) || message.role !== "assistant" || typeof message.reasoning_content !== "string" || !message.reasoning_content.length) return message;
    if (!replay) {
      // Drop only the reasoning field; content and every other field stay as-is.
      const stripped = { ...message };
      delete stripped.reasoning_content;
      return stripped;
    }
    const { reasoning_content, content, ...rest } = message;
    // Only adapt the SDK's observed shapes; leave unknown content untouched.
    if (content != null && typeof content !== "string" && !Array.isArray(content)) return message;
    const chunks = Array.isArray(content) ? content : typeof content === "string" ? [{ type: "text", text: content }] : [];
    return { ...rest, content: [{ type: "thinking", thinking: [{ type: "text", text: reasoning_content }] }, ...chunks] };
  }) };
}
