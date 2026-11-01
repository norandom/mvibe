# API contracts and observability extension

User requested on 2026-11-01 while approving baseline continuation. Keep the
practical E2E baseline; add change detection and operator diagnostics, not a new
all-tool certification system.

## OpenCode contract evidence

The installed @opencode/protocol 2.2.5 exports ClientApi, an Effect HttpApi.
OpenApi.fromApi(ClientApi) produces OpenAPI 3.1 with 138 path entries locally.
This is a prepared-SDK contract, not proof that an arbitrary running binary
implements it. Record package/CLI versions and binary hash with each run.
The V2 official API reference also links an OpenAPI JSON document.

Plugin context methods include hooks/transforms outside HTTP. Compare the
published plugin TypeScript declaration surface used by this provider alongside
HTTP schemas, compile against the prepared candidate SDK, then run the real host
E2E baseline. Schema equality alone never proves behavioral compatibility.

Keep a reviewed baseline and explicit candidate capture/compare commands. Emit
readable structural differences and nonzero drift result; do not silently update
baselines. Distinguish additive/changed/removed surface without assuming every
difference breaks our provider. Candidate updates must be prepared separately,
never mutate the user's auto-updating installation or fetch during offline tests.

## Proxy API and diagnostics now

FastAPI already serves /openapi.json and /docs, but the current catch-all route
hides the actual supported operations. Publish explicit models, chat completions
and embeddings operations while retaining raw payload passthrough, streaming and
unknown-endpoint handling. Document tool calls as forwarded conversations: the
agent executes tools, the proxy does not run tools or act as an MCP server.

Use structlog-backed structured proxy stdout events with request IDs, phase,
status, elapsed time and fixed actionable hints for auth, endpoint, upstream,
stream and cleanup failures. Do not log prompts, tool arguments/results, headers,
credentials or raw upstream exceptions. No auth/helper protocol stdout changes.
Add only safe metadata needed for diagnosis and preserve upstream status/body,
stream timing and no replay. OpenTelemetry export and distributed tracing are
follow-up work in ../proxy-observability/brief.md.

## Sources

- https://dev.opencode.ai/v2/docs/api
- https://dev.opencode.ai/v2/docs/build/plugins/server/
- https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/structlog/structlog.html
- https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/logging/logging.html
- https://structlog.org/en/stable/contextvars.html
