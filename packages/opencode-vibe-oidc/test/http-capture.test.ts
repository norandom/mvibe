import { expect, test } from "bun:test";
import { createHttpCapture, CAPTURE_BODY_LIMIT, CAPTURE_REQUEST_LIMIT } from "../src/http-capture.js";
import { createProviderFetch } from "../src/provider.js";
import type { StdioBridge } from "../src/bridge.js";

function fixture(body = true) {
  const records: any[] = [];
  const capture = createHttpCapture({ captureDirectory: process.cwd(), captureBody: body }, (_name, data) => records.push(JSON.parse(data)));
  return { records, capture };
}
test("capture preserves final request identity and original response controls without reading stream", async () => {
  const { records, capture } = fixture();
  const bridge = { request: async (_method: string, params: any) => ({ target: params.target_url, expires_at: Date.now() / 1000 + 60, revision: "one", headers: { authorization: "Bearer secret-token", "x-external-workspace-id": "tenant-123", "user-agent": "vibe/1" }, metadata: { call_source: "vibe_code", client_name: "opencode" } }) } as Pick<StdioBridge, "request">;
  let sent = 0;
  const stream = new ReadableStream({ start() {}, pull() { throw new Error("capture read SSE"); } }, { highWaterMark: 0 });
  const response = new Response(stream, { status: 429, headers: { "x-should-retry": "true", "x-request-id": "req-1", "set-cookie": "secret-cookie", "x-unknown": "secret-unknown" } });
  const fetch = createProviderFetch(bridge, "https://inference.example", async input => { sent++; expect((input as Request).redirect).toBe("manual"); return response; }, { capture });
  const actual = await fetch("https://inference.example/v1/chat/completions?api_key=secret-query", { method: "POST", body: JSON.stringify({ model: "test-model", messages: [{ content: "secret-token tenant-123" }], api_key: "nested-secret", metadata: {} }) });
  expect(sent).toBe(1); expect(actual.body).toBe(stream); expect(actual.headers.get("x-should-retry")).toBe("false");
  expect(records).toHaveLength(2);
  expect(records[0].request_id).toBe(records[1].request_id);
  expect(records[0].body.model).toBe("test-model"); expect(records[0].body.metadata.client_name).toBe("opencode");
  expect(records[0].headers["user-agent"]).toBe("vibe/1");
  expect(records[1].status).toBe(429); expect(records[1].headers["x-should-retry"]).toBe("true"); expect(records[1].headers["x-request-id"]).toBe("req-1");
  for (const secret of ["secret-token", "tenant-123", "nested-secret", "secret-query", "secret-cookie", "secret-unknown"]) expect(JSON.stringify(records)).not.toContain(secret);
});
test("body and capture opt-in, size/session limits and IO failures", () => {
  const req = new Request("https://example.test/v1/chat/completions");
  const disabled: string[] = [];
  createHttpCapture({}, (_name, data) => disabled.push(data)).request(req, "{}", {});
  expect(disabled).toHaveLength(0);
  const noBody = fixture(false); noBody.capture.request(req, '{"secret":"value"}', {}); expect(noBody.records[0].body_state).toBe("disabled");
  const large = fixture(); large.capture.request(req, "x".repeat(CAPTURE_BODY_LIMIT + 1), {}); expect(large.records[0].body_state).toBe("omitted_size_limit");
  for (let i = 0; i < CAPTURE_REQUEST_LIMIT + 5; i++) large.capture.request(req, "{}", {});
  expect(large.records.filter(r => r.event === "capture_limit")).toHaveLength(1);
  expect(large.records.length).toBeLessThanOrEqual(CAPTURE_REQUEST_LIMIT + 1);
  const broken = createHttpCapture({ captureDirectory: process.cwd(), captureBody: true }, () => { throw new Error("disk full"); });
  expect(() => broken.request(req, "{}", {})).not.toThrow();
});

test("cancellation during capture never dispatches transport and IO errors do not replay", async () => {
  const controller = new AbortController();
  let sent = 0;
  const bridge = { request: async (_method: string, params: any) => ({ target: params.target_url, expires_at: Date.now() / 1000 + 60, revision: "one", headers: {}, metadata: {} }) } as Pick<StdioBridge, "request">;
  const capture = createHttpCapture({ captureDirectory: process.cwd() }, () => { controller.abort(); });
  const fetch = createProviderFetch(bridge, "https://inference.example", async () => { sent++; return new Response(); }, { capture });
  await expect(fetch("https://inference.example/v1/models", { signal: controller.signal })).rejects.toThrow();
  expect(sent).toBe(0);
  const broken = createHttpCapture({ captureDirectory: process.cwd() }, () => { throw new Error("disk full"); });
  const healthy = createProviderFetch(bridge, "https://inference.example", async () => { sent++; return new Response("ok"); }, { capture: broken });
  expect(await (await healthy("https://inference.example/v1/models")).text()).toBe("ok"); expect(sent).toBe(1);
});

test("total session bytes are bounded and tenant pseudonyms correlate only inside a session", () => {
  const { capture, records } = fixture();
  const req = new Request("https://example.test/v1/models", { headers: { "x-external-workspace-id": "tenant-one" } });
  for (let i = 0; i < CAPTURE_REQUEST_LIMIT; i++) capture.request(req, JSON.stringify({ messages: ["x".repeat(200 * 1024)] }), { "x-external-workspace-id": "tenant-one" });
  expect(records.at(-1).event).toBe("capture_limit"); expect(records.at(-1).reason).toBe("session_bytes");
  expect(Buffer.byteLength(records.map(r => JSON.stringify(r, null, 2)).join(""))).toBeLessThan(8 * 1024 * 1024);
  expect(records[0].headers["x-external-workspace-id"]).toBe(records[1].headers["x-external-workspace-id"]);
  const another = fixture(); another.capture.request(req, "{}", { "x-external-workspace-id": "tenant-one" });
  expect(another.records[0].headers["x-external-workspace-id"]).not.toBe(records[0].headers["x-external-workspace-id"]);
});

test("invalid JSON and excessive nesting are explicit, transport failure is correlated", async () => {
  const { records, capture } = fixture();
  const req = new Request("https://example.test/v1/models");
  capture.request(req, "not-json", {}); expect(records[0].body_state).toBe("omitted_invalid_json");
  let nested: unknown = "leaf"; for (let i = 0; i < 45; i++) nested = { child: nested };
  capture.request(req, JSON.stringify(nested), {}); expect(JSON.stringify(records[1].body)).toContain("OMITTED_DEPTH_LIMIT");
  const bridge = { request: async (_method: string, params: any) => ({ target: params.target_url, expires_at: Date.now() / 1000 + 60, revision: "one", headers: {} }) } as Pick<StdioBridge, "request">;
  const fetch = createProviderFetch(bridge, "https://inference.example", async () => { throw new Error("raw upstream secret"); }, { capture });
  await expect(fetch("https://inference.example/v1/models")).rejects.toThrow();
  expect(records.at(-1).event).toBe("transport_error"); expect(records.at(-1).request_id).toBe(records.at(-2).request_id);
  expect(JSON.stringify(records)).not.toContain("raw upstream secret");
});

test("capture sanitization and response recording errors cannot change inference", async () => {
  const bridge = { request: async (_method: string, params: any) => ({ target: params.target_url, expires_at: Date.now() / 1000 + 60, revision: "one", headers: {} }) } as Pick<StdioBridge, "request">;
  for (const capture of [ { request() { throw new Error("sanitize"); } }, { request() { return { response() { throw new Error("headers"); }, failed() { throw new Error("failure"); } }; } } ]) {
    const fetch = createProviderFetch(bridge, "https://inference.example", async () => new Response("ok"), { capture });
    expect(await (await fetch("https://inference.example/v1/models")).text()).toBe("ok");
  }
});

test("plain token and bearer fields are redacted and long headers mark truncation", () => {
  const { records, capture } = fixture();
  capture.request(new Request("https://example.test/v1/models", { headers: { "user-agent": "x".repeat(2100) } }), JSON.stringify({ token: "plain-token-secret", bearer: "plain-bearer-secret", max_tokens: 123, nested: { TOKEN: "uppercase-secret" } }), {});
  expect(records[0].body.token).toBe("[REDACTED]");
  expect(records[0].body.bearer).toBe("[REDACTED]");
  expect(records[0].body.nested.TOKEN).toBe("[REDACTED]");
  expect(records[0].body.max_tokens).toBe(123);
  expect(records[0].headers["user-agent"]).toEndWith("[TRUNCATED]");
});

test("header truncation is explicit", () => {
  const { records, capture } = fixture();
  capture.request(new Request("https://example.test/v1/models", { headers: { "user-agent": "x".repeat(2100) } }), undefined, {});
  expect(records[0].headers["user-agent"]).toEndWith("[TRUNCATED]");
});
