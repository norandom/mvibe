# Product direction

Updated: 2026-11-01. Bootstrapped from the implementation and user-confirmed
existing-login acceptance.

mvibe connects agent clients to Mistral through a direct OpenCode provider or a
local OpenAI-compatible proxy. Both consumers share Python authentication.
Normal Vibe owns browser sign-in and credential renewal; the recommended route
reuses its existing stored credential without manual API-key entry.

Keep the proxy and provider aligned on authentication policy. The proxy serves
model discovery, chat completions, streaming/tool-call payloads and embeddings.
Clients execute native and MCP tools. A search executor and the Responses API
are not implied by protocol forwarding.

The user confirmed correct Vibe quota usage in normal Vibe, the neutral native
capture launcher and the integrated OpenCode existing-login route. Treat this
as account-specific live acceptance evidence. HTTP success, an opaque key,
User-Agent parity or a Pro display label alone does not establish entitlement.
Experimental direct-ID-token OIDC remains a separate opt-in route.

Use [authentication.md](authentication.md) for the durable auth decisions and
[the technical writeup](../../docs/vibe-authentication.md) for operational detail.
