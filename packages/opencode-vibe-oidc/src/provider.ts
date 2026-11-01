import { createHttpCapture, type CaptureOptions } from "./http-capture.js";
import type { StdioBridge } from "./bridge.js";
import { BridgeError } from "./bridge.js";
import { randomUUID } from "node:crypto";

import { REQUEST_CONTEXT_HEADER } from "./request-context.js";
import type { RequestContext } from "./protocol.js";

export type ProviderFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export type ProviderFetchOptions = CaptureOptions & { diagnostics?: boolean; authSource?: "existing-vibe" | "oidc"; capture?: ReturnType<typeof createHttpCapture> };
const credentialHeaders = new Set(["authorization", "x-external-organization-id", "x-external-workspace-id"]);
const clientMetadataHeaders = new Set(["user-agent"]);

/** Independent outbound guard; the shared library remains the authority for auth. */
export function providerBaseURL(audience: string): string {
  try {
    const base = new URL(audience);
    const loopback = base.hostname === "127.0.0.1" || base.hostname === "[::1]";
    if (base.username || base.password || base.search || base.hash || !(base.protocol === "https:" || base.protocol === "http:" && loopback)) throw new Error();
    return base.href.replace(/\/$/, "") + "/v1";
  } catch { throw new BridgeError("destination", "Invalid provider audience configuration."); }
}
export function createProviderFetch(bridge: Pick<StdioBridge, "request">, audience: string, transport: ProviderFetch = globalThis.fetch, options: ProviderFetchOptions = {}): ProviderFetch & Pick<typeof globalThis.fetch, "preconnect"> {
  const nativeSignInHint = "Sign in through normal Vibe, then retry; credentials are read from the native keyring.";
  const capture = options.capture ?? createHttpCapture(options);
  const base = new URL(providerBaseURL(audience));
  const paths = new Set(["models", "chat/completions", "embeddings"].map(path => base.pathname + "/" + path));
  const fetch: ProviderFetch = async (input, init) => {
    const started = performance.now();
    const diagnostic = {
      event: "provider_request", request_id: randomUUID(), endpoint: "unsupported",
      phase: "routing", outcome: "failed", status: null as number | null,
      hint: "Use the configured models, chat/completions or embeddings endpoint.",
    };
    let signal: AbortSignal | undefined;
    try {
      const request = new Request(input, init);
      signal = request.signal;
      if (request.signal.aborted) throw new BridgeError("cancelled");
      const target = new URL(request.url);
      if (target.origin !== base.origin || target.username || target.password || target.hash || !paths.has(target.pathname)) throw new BridgeError("destination", "Provider destination is outside the configured inference policy.");
      // Only names from the destination guard reach diagnostics, never URLs.
      diagnostic.endpoint = target.pathname.slice(base.pathname.length + 1);
      diagnostic.phase = "authentication";
      diagnostic.hint = options.authSource === "existing-vibe" ? nativeSignInHint : "Run OpenCode Vibe OIDC sign-in with the configured helper runtime; verify tenant access.";
      const chat = target.pathname === base.pathname + "/chat/completions" && request.method === "POST";
      let requestContext: RequestContext = {};
      const encodedContext = request.headers.get(REQUEST_CONTEXT_HEADER);
      if (chat && encodedContext !== null) {
        if (encodedContext.length > 4096) throw new BridgeError("context", "Invalid private request context.");
        try { requestContext = JSON.parse(encodedContext) as RequestContext; }
        catch { throw new BridgeError("context", "Invalid private request context."); }
      }
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
      // Only chat JSON is materialized for native attribution. Response bodies
      // remain streaming and the original cancellation signal stays linked.
      let body: string | undefined;
      if (chat && snapshot.metadata) {
        const serialized = await request.clone().text();
        try {
          const payload: unknown = JSON.parse(serialized);
          if (payload !== null && typeof payload === "object" && !Array.isArray(payload)) {
            const object = payload as Record<string, unknown>;
            if (object.metadata === undefined || object.metadata === null || typeof object.metadata === "object" && !Array.isArray(object.metadata)) {
              if (object.metadata) delete (object.metadata as Record<string, unknown>).user_plan;
              object.metadata = { ...(object.metadata as Record<string, unknown> | undefined), ...snapshot.metadata };
              body = JSON.stringify(object);
            }
          }
        } catch { /* Invalid/non-object JSON keeps its original bytes. */ }
      }
      if (request.signal.aborted) throw new BridgeError("cancelled");
      if (body !== undefined) headers.delete("content-length");
      const outbound = new Request(request, { headers, redirect: "manual", ...(body === undefined ? {} : { body }) });
      // Bun can inherit source headers when an empty Headers override is used.
      // Enforce removal on the final Request as well as the replacement set.
      outbound.headers.delete(REQUEST_CONTEXT_HEADER);
      if (body !== undefined) outbound.headers.delete("content-length");
      let captured: ReturnType<typeof capture.request>;
      try { captured = capture.request(outbound, body, snapshot.headers); }
      catch { /* All capture work is best effort, including sanitization. */ }
      const captureFailure = () => { try { captured?.failed(); } catch { /* Best effort. */ } };
      if (request.signal.aborted) { captureFailure(); throw new BridgeError("cancelled"); }
      let response: Response;
      try { response = await transport(outbound); }
      catch (error) { captureFailure(); throw error; }
      try { captured?.response(response); } catch { /* Best effort. */ }
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
        try { console.error(JSON.stringify({ ...diagnostic, elapsed_ms: Math.round((performance.now() - started) * 100) / 100 })); }
        catch { /* Diagnostic output must never affect inference. */ }
      }
    }
  };
  // Bun extends fetch with preconnect. Never open an unauthenticated side path.
  return Object.assign(fetch, { preconnect: () => {} });
}
