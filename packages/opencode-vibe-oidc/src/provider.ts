import { createHttpCapture, type CaptureOptions } from "./http-capture.js";
import type { StdioBridge } from "./bridge.js";
import { BridgeError } from "./bridge.js";
import { randomUUID } from "node:crypto";

import { REQUEST_CONTEXT_HEADER } from "./request-context.js";
import type { RequestContext } from "./protocol.js";
import { createGateRecord, MODELS_KEY, type GateRecord, type UpstreamGate } from "./rate-limit.js";

export type ProviderFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
/** `gate` schedules every upstream attempt (pacing, rate-limit holds and re-sends);
 * without one, each fetch sends exactly once. */
export type ProviderFetchOptions = CaptureOptions & { diagnostics?: boolean; authSource?: "existing-vibe" | "oidc"; capture?: ReturnType<typeof createHttpCapture>; gate?: Pick<UpstreamGate, "run"> };
const credentialHeaders = new Set(["authorization", "x-external-organization-id", "x-external-workspace-id"]);
const clientMetadataHeaders = new Set(["user-agent"]);
const EMBEDDINGS_KEY = "embeddings";
const DEFAULT_KEY = "default";
const GATE_KEY_LIMIT = 128;
// Unicode "Other" and "Separator" characters except the ASCII space: what Python's
// str.isprintable() rejects, so a key matches the proxy's key_for.
const NON_PRINTABLE = /(?!\x20)[\p{C}\p{Z}]/gu;
const rateLimitedHint = "Upstream rate limit persisted beyond rateLimit.maxWaitMs or rateLimit.maxQueue; lower session concurrency or configure rateLimit.requestsPerSecond.";

/** Independent outbound guard; the shared library remains the authority for auth. */
export function providerBaseURL(audience: string): string {
  try {
    const base = new URL(audience);
    const loopback = base.hostname === "127.0.0.1" || base.hostname === "[::1]";
    if (base.username || base.password || base.search || base.hash || !(base.protocol === "https:" || base.protocol === "http:" && loopback)) throw new Error();
    return base.href.replace(/\/$/, "") + "/v1";
  } catch { throw new BridgeError("destination", "Invalid provider audience configuration."); }
}

/** The gate key of one request (mirrors the proxy's key_for): `models` for discovery,
 * which is never paced; `embeddings` for embeddings; otherwise the printable characters
 * of the chat model, at most 128 of them, else `default`. A client-supplied model name
 * thus never carries control characters into a key. */
function gateKey(endpoint: string, model: unknown): string {
  if (endpoint === "models") return MODELS_KEY;
  if (endpoint === "embeddings") return EMBEDDINGS_KEY;
  if (typeof model === "string") {
    const printable = Array.from(model.replace(NON_PRINTABLE, "")).slice(0, GATE_KEY_LIMIT).join("");
    if (printable) return printable;
  }
  return DEFAULT_KEY;
}

export function createProviderFetch(bridge: Pick<StdioBridge, "request">, audience: string, transport: ProviderFetch = globalThis.fetch, options: ProviderFetchOptions = {}): ProviderFetch & Pick<typeof globalThis.fetch, "preconnect"> {
  const nativeSignInHint = "Sign in through normal Vibe, then retry; credentials are read from the native keyring.";
  const authenticationHint = options.authSource === "existing-vibe" ? nativeSignInHint : "Run OpenCode Vibe OIDC sign-in with the configured helper runtime; verify tenant access.";
  const capture = options.capture ?? createHttpCapture(options);
  const base = new URL(providerBaseURL(audience));
  const paths = new Set(["models", "chat/completions", "embeddings"].map(path => base.pathname + "/" + path));
  const fetch: ProviderFetch = async (input, init) => {
    const started = performance.now();
    const diagnostic = {
      event: "provider_request", request_id: randomUUID(), endpoint: "unsupported",
      model: null as string | null,
      phase: "routing", outcome: "failed", status: null as number | null,
      hint: "Use the configured models, chat/completions or embeddings endpoint.",
    };
    let signal: AbortSignal | undefined;
    // What the gate did for this request; set only when a gate runs it.
    let record: GateRecord | undefined;
    try {
      const request = new Request(input, init);
      signal = request.signal;
      if (request.signal.aborted) throw new BridgeError("cancelled");
      const target = new URL(request.url);
      if (target.origin !== base.origin || target.username || target.password || target.hash || !paths.has(target.pathname)) throw new BridgeError("destination", "Provider destination is outside the configured inference policy.");
      // Only names from the destination guard reach diagnostics, never URLs.
      diagnostic.endpoint = target.pathname.slice(base.pathname.length + 1);
      diagnostic.phase = "authentication";
      diagnostic.hint = authenticationHint;
      const chat = target.pathname === base.pathname + "/chat/completions" && request.method === "POST";
      let requestContext: RequestContext = {};
      const encodedContext = request.headers.get(REQUEST_CONTEXT_HEADER);
      if (chat && encodedContext !== null) {
        if (encodedContext.length > 4096) throw new BridgeError("context", "Invalid private request context.");
        try { requestContext = JSON.parse(encodedContext) as RequestContext; }
        catch { throw new BridgeError("context", "Invalid private request context."); }
      }
      // The caller's body is read once, before any attempt; every attempt sends these
      // bytes again. Response bodies remain streaming.
      const original = request.body === null ? undefined : await request.clone().arrayBuffer();
      // Only chat JSON is materialized for native attribution; its model names the gate key.
      let model: unknown;
      let attributable: Record<string, unknown> | undefined;
      if (chat && original !== undefined) {
        try {
          const payload: unknown = JSON.parse(new TextDecoder().decode(original));
          if (payload !== null && typeof payload === "object" && !Array.isArray(payload)) {
            const object = payload as Record<string, unknown>;
            model = object.model;
            // Model id only; message content is never part of diagnostics.
            if (typeof object.model === "string") diagnostic.model = object.model;
            if (object.metadata === undefined || object.metadata === null || typeof object.metadata === "object" && !Array.isArray(object.metadata)) attributable = object;
          }
        } catch { /* Invalid/non-object JSON keeps its original bytes. */ }
      }
      // Set by the first attempt that gets a snapshot; later attempts send these exact
      // bytes with their own snapshot's headers and never attribute again.
      let attributed: string | undefined;
      // One upstream attempt: a fresh snapshot, the header policy, a new outbound
      // Request and one send. It returns the upstream response unread and unwrapped.
      const attempt = async (): Promise<Response> => {
        if (request.signal.aborted) throw new BridgeError("cancelled");
        diagnostic.phase = "authentication";
        diagnostic.hint = authenticationHint;
        const snapshot = await bridge.request("resolve", { target_url: target.href, ...(chat ? { request_context: requestContext } : {}) }, request.signal);
        if (request.signal.aborted) throw new BridgeError("cancelled");
        if (snapshot.target !== target.href || !Number.isFinite(snapshot.expires_at) || snapshot.expires_at <= Date.now() / 1000) throw new BridgeError("snapshot", "Authentication helper returned an unusable request snapshot.");
        if (chat && !snapshot.metadata) throw new BridgeError("setup", "Native request metadata is unavailable; install matching library and provider versions.");
        const headers = new Headers(request.headers);
        headers.delete(REQUEST_CONTEXT_HEADER);
        for (const name of [...credentialHeaders, "x-api-key", "api-key", "proxy-authorization"]) headers.delete(name);
        for (const [name, value] of Object.entries(snapshot.headers)) {
          if (credentialHeaders.has(name.toLowerCase()) || clientMetadataHeaders.has(name.toLowerCase())) headers.set(name, value);
        }
        diagnostic.phase = "acquisition";
        diagnostic.hint = "Check Vibe connectivity and configuration before an explicit retry; no inference was replayed.";
        if (attributable !== undefined && attributed === undefined) {
          if (attributable.metadata) delete (attributable.metadata as Record<string, unknown>).user_plan;
          attributable.metadata = { ...(attributable.metadata as Record<string, unknown> | undefined), ...snapshot.metadata };
          attributed = JSON.stringify(attributable);
        }
        if (request.signal.aborted) throw new BridgeError("cancelled");
        if (attributed !== undefined) headers.delete("content-length");
        const body = attributed ?? original;
        // Rebuilt for every attempt; the original cancellation signal stays linked.
        const outbound = new Request(request.url, { method: request.method, headers, redirect: "manual", signal: request.signal, ...(body === undefined ? {} : { body }) });
        // Bun can inherit source headers when an empty Headers override is used.
        // Enforce removal on the final Request as well as the replacement set.
        outbound.headers.delete(REQUEST_CONTEXT_HEADER);
        if (attributed !== undefined) outbound.headers.delete("content-length");
        let captured: ReturnType<typeof capture.request>;
        try { captured = capture.request(outbound, attributed, snapshot.headers); }
        catch { /* All capture work is best effort, including sanitization. */ }
        const captureFailure = () => { try { captured?.failed(); } catch { /* Best effort. */ } };
        if (request.signal.aborted) { captureFailure(); throw new BridgeError("cancelled"); }
        let response: Response;
        try { response = await transport(outbound); }
        catch (error) { captureFailure(); throw error; }
        try { captured?.response(response); } catch { /* Best effort. */ }
        return response;
      };
      let response: Response;
      if (options.gate === undefined) response = await attempt();
      else {
        record = createGateRecord();
        response = await options.gate.run(gateKey(diagnostic.endpoint, model), attempt, request.signal, record);
      }
      diagnostic.status = response.status;
      diagnostic.phase = "response_headers";
      diagnostic.outcome = response.status >= 400 ? "upstream_error" : "headers_received";
      diagnostic.hint = ({
        400: "Check tool schema and request fields against the selected model capabilities.",
        401: options.authSource === "existing-vibe" ? nativeSignInHint : "Run OpenCode Vibe OIDC sign-in again; verify account and tenant access.",
        403: "Check Vibe tenant and model access; OpenCode owns tool permissions.",
        422: "Check tool schema and reasoning history serialization against the selected model; update the provider before an explicit retry.",
        429: "Respect upstream rate limits before an explicit retry; no inference was replayed.",
      } as Record<number, string>)[response.status] ?? (response.status >= 400
        ? "Check upstream availability before an explicit retry; no inference was replayed."
        : "Response headers received; body or stream completion is not observed by these diagnostics.");
      if (record?.exhausted) {
        diagnostic.outcome = "rate_limited";
        diagnostic.hint = rateLimitedHint;
      }
      // OpenCode V2's native retry policy reads this response control header;
      // SDK maxRetries alone does not govern host-level one-shot/session retries.
      // Keep upstream status/body/stream intact, including cancellation ownership.
      const responseHeaders = new Headers(response.headers);
      responseHeaders.set("x-should-retry", "false");
      return new Response(response.body, { status: response.status, statusText: response.statusText, headers: responseHeaders });
    } catch (error) {
      if (signal?.aborted || error instanceof BridgeError && error.code === "cancelled") {
        diagnostic.outcome = "cancelled";
        diagnostic.hint = "Request cancelled; inspect OpenCode state before any explicit retry.";
      }
      if (error instanceof BridgeError) throw error;
      throw new BridgeError("request", "Provider request failed; retry explicitly or sign in again if needed.");
    } finally {
      if (options.diagnostics === true) {
        try {
          // Numbers only from the gate record: never its x-ratelimit-* header values.
          const held = record === undefined ? {} : { queued_ms: Math.round(record.queuedMs * 100) / 100, rate_limit_rejections: record.rejections };
          console.error(JSON.stringify({ ...diagnostic, ...held, elapsed_ms: Math.round((performance.now() - started) * 100) / 100 }));
        } catch { /* Diagnostic output must never affect inference. */ }
      }
    }
  };
  // Bun extends fetch with preconnect. Never open an unauthenticated side path.
  return Object.assign(fetch, { preconnect: () => {} });
}
