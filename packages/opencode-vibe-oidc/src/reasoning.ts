function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// Mistral requires reasoning in content ThinkChunks when replaying assistant
// history: https://docs.mistral.ai/studio/conversations/reasoning
export function mistralRequestBody(body: Record<string, unknown>): Record<string, unknown> {
  if (!Array.isArray(body.messages)) return body;
  return { ...body, messages: body.messages.map((message: unknown) => {
    if (!isRecord(message) || message.role !== "assistant" || typeof message.reasoning_content !== "string" || !message.reasoning_content.length) return message;
    const { reasoning_content, content, ...rest } = message;
    // Only adapt the SDK's observed shapes; leave unknown content untouched.
    if (content != null && typeof content !== "string" && !Array.isArray(content)) return message;
    const chunks = Array.isArray(content) ? content : typeof content === "string" ? [{ type: "text", text: content }] : [];
    return { ...rest, content: [{ type: "thinking", thinking: [{ type: "text", text: reasoning_content }] }, ...chunks] };
  }) };
}
