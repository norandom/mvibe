# Proxy and provider observability

## User intent

Operators and AI-agent users need to understand why Vibe OIDC requests, streaming
or tool conversations fail without reading secrets or guessing which component
failed. OpenCode 2 updates are expected to change behavior. The user asked on
2026-11-01 to make observability a key feature, with a logging library selected
now and OpenTelemetry/traces later.

## Starting point

The direct OpenCode plugin uses the shared Python auth library without the proxy.
The proxy forwards models, chat completions and embeddings; OpenCode/the calling
agent executes tools. Both paths need diagnostics eventually. A proxy log cannot
claim to have observed direct-plugin or MCP execution it did not handle.

The current baseline adds practical E2E reruns, contract drift artifacts, proxy
OpenAPI/Swagger and structured stdout diagnostics. This follow-up builds on that
foundation rather than creating another test-certification framework.

## Intended direction

- Use structlog with the Python logging ecosystem for structured events and
  future OpenTelemetry integration. Keep logging setup explicit and inert on
  import; proxy stdout is distinct from helper/MCP protocol stdout.
- Correlate request/operation IDs, host/model/profile versions, outcome, latency,
  stream completion/abort, tool-related request metadata and safe recovery hints.
- Add OpenTelemetry spans and metrics for proxy requests/streams, auth resolution,
  subprocess bridge and direct plugin boundaries after a small reviewed design.
- Propagate W3C trace context only across approved boundaries, with no credential
  or private prompt/result capture. Trace IDs must correspond to real contexts.
- Support opt-in OTLP export/collector configuration, resource identity, sampling,
  bounded queues and flush/shutdown; observability failure must not replay
  inference, leak secrets, block indefinitely or corrupt streaming/protocols.
- Provide operator examples for a collector/backend, local diagnosis and expected
  OpenCode update failures. Retain offline exporter tests using owned sinks.

## Scope and acceptance direction

First implement one correlated request/stream trace and a few useful counters,
then extend the direct-plugin/helper path. No dashboards, hosted telemetry
service, broad LLM payload collection or mandatory exporter dependency is implied.
Tokens, cookies, credentials, sign-in URLs, request headers and private tool
arguments/results are excluded by default. Configure telemetry destinations
explicitly; default tests remain offline.

Official references: structlog context variables and OpenTelemetry's structlog
and standard Python logging instrumentation. Verify supported package versions
when designing the follow-up; do not pretend selecting a logging library means
tracing/export is already implemented.
