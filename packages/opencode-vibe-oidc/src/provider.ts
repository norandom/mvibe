import type { StdioBridge } from "./bridge.js";
import { BridgeError } from "./bridge.js";
import { randomUUID } from "node:crypto";

export type ProviderFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export type ProviderFetchOptions = { diagnostics?: boolean };
const credentialHeaders = new Set(["authorization", "x-external-organization-id", "x-external-workspace-id"]);

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
      diagnostic.hint = "Run OpenCode Vibe OIDC sign-in with the configured helper runtime; verify tenant access.";
      const snapshot = await bridge.request("resolve", { target_url: target.href }, request.signal);
      if (request.signal.aborted) throw new BridgeError("cancelled");
      if (snapshot.target !== target.href || !Number.isFinite(snapshot.expires_at) || snapshot.expires_at <= Date.now() / 1000) throw new BridgeError("snapshot", "Authentication helper returned an unusable request snapshot.");
      const headers = new Headers(request.headers);
      for (const name of [...credentialHeaders, "x-api-key", "api-key", "proxy-authorization"]) headers.delete(name);
      for (const [name, value] of Object.entries(snapshot.headers)) if (credentialHeaders.has(name.toLowerCase())) headers.set(name, value);
      diagnostic.phase = "acquisition";
      diagnostic.hint = "Check Vibe connectivity and configuration before an explicit retry; no inference was replayed.";
      // Request construction transfers the body stream without reading it. The
      // original signal remains linked through both Request objects.
      const response = await transport(new Request(request, { headers, redirect: "manual" }));
      diagnostic.status = response.status;
      diagnostic.phase = "response_headers";
      diagnostic.outcome = response.status >= 400 ? "upstream_error" : "headers_received";
      diagnostic.hint = ({
        400: "Check tool schema and request fields against the selected model capabilities.",
        401: "Run OpenCode Vibe OIDC sign-in again; verify account and tenant access.",
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
