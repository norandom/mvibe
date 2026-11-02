# Requirements Document

## Project Description (Input)

Proxy and direct-provider operators and AI-agent users need correlated, actionable diagnostics for Vibe OIDC, streaming and tool conversations as OpenCode changes. Current diagnostics lack end-to-end traces and metrics. Build on the current structured logging foundation to add safe OpenTelemetry correlation, traces/metrics, opt-in export and operator guidance, preserving streaming, helper protocol stdout and credential privacy. See brief.md; design/tasks are future phases.

## Introduction

The local proxy and the direct OpenCode provider share authentication but are separate execution paths; a proxy log must never claim to have observed direct-plugin or MCP execution it did not handle. The existing opt-in structured stdout diagnostics (proxy `--log`, plugin `diagnostics: true`) are the baseline; this feature extends them into correlated, exportable observability without ever exposing secrets or payloads.

## Boundary Context

- **In scope**: correlated request/stream diagnostics for the proxy's supported endpoints; equivalent opt-in diagnostics for the direct provider path; safe content rules; opt-in telemetry export (traces/metrics) with bounded, flush-on-shutdown behavior; operator guidance; offline verification with owned sinks.
- **Out of scope**: dashboards, hosted telemetry services, LLM payload/prompt capture, Responses API, search endpoints, tool/MCP execution (the agent executes tools), mandatory exporter dependencies.
- **Adjacent expectations**: OpenCode host/plugin lifecycle and its config flags remain owned by OpenCode and the adapter installer; the shared auth library keeps credentials out of diagnostics; MCP servers and helper protocol stdout remain distinct channels that observability must not corrupt.

## Requirements

### Requirement 1: Correlated proxy request and stream observability

**Objective:** As an operator running the proxy, I want every proxied request to carry a correlation ID and emit lifecycle events, so that I can trace one request end to end without reading secrets.

#### Acceptance Criteria

1. When a request reaches a supported proxy endpoint, the proxy shall assign it a unique correlation ID and return that ID to the caller in a response header.
2. When request logging is enabled, the proxy shall emit a structured event for each request containing the correlation ID, the processing phase, the outcome, the HTTP status where one was produced, the elapsed time and a fixed, actionable hint.
3. When a streamed response completes, is interrupted or is cancelled, the proxy shall emit an event with the matching outcome and the same correlation ID.
4. When an endpoint is unsupported or a local failure occurs, the proxy shall emit an event with a fixed failure code and hint, without including raw error details.
5. The proxy shall correlate all events of one request so that an operator can reconstruct the request lifecycle from the log alone.

### Requirement 2: Safe diagnostic content

**Objective:** As a user, I want diagnostics to never expose my credentials, prompts or tool data, so that logs can be shared for support without leaking secrets.

#### Acceptance Criteria

1. The diagnostics facility shall never emit tokens, cookies, authorization headers, sign-in URLs, request or response bodies, prompts, tool arguments, tool results or raw exception messages.
2. If an internal error occurs while producing a diagnostic event, the proxy shall continue serving the request and shall not fail inference because of the diagnostics path.
3. Where an upstream error status is observed, the diagnostic shall use a fixed, curated hint rather than forwarding the upstream body or exception text.

### Requirement 3: Explicit opt-in and inert default

**Objective:** As an operator, I want diagnostics off unless I explicitly enable them, so that default proxy behavior and protocol stdout stay unchanged.

#### Acceptance Criteria

1. The proxy shall not emit request diagnostics unless the operator explicitly enables them through the documented flag or configuration.
2. While diagnostics are disabled, the proxy shall behave exactly as before, including response headers other than the correlation ID behavior required in Requirement 1.
3. The direct provider path shall expose its own explicit opt-in setting, and a proxy log shall not claim events for requests the proxy did not handle.
4. If diagnostics are enabled, enabling them shall not alter request routing, streaming behavior or the helper/MCP protocol stdout channel.

### Requirement 4: Direct-provider parity

**Objective:** As an operator debugging the direct OpenCode plugin, I want the same correlation and safe-event shape as the proxy, so that both auth paths are diagnosable with one mental model.

#### Acceptance Criteria

1. When the direct provider plugin's diagnostics setting is enabled, the plugin shall emit correlated lifecycle events for the requests it handles, including model, phase, outcome and safe hints.
2. When the direct provider plugin's diagnostics setting is disabled, the plugin shall not emit those events.
3. If the plugin cannot emit a diagnostic, the plugin shall continue normal operation and shall not corrupt its protocol stdout.

### Requirement 5: Opt-in telemetry export

**Objective:** As an operator running a collector, I want to export traces and metrics to an OpenTelemetry backend when I configure one, so that proxy and provider activity appears in my existing tooling.

#### Acceptance Criteria

1. Where telemetry export is configured, the system shall export spans for proxy requests and streams, authentication resolution and the helper bridge, with correlation IDs matching the log events.
2. Where telemetry export is configured, the system shall propagate W3C trace context only across approved boundaries and shall not invent trace IDs for events it did not observe.
3. If telemetry export is not configured, the system shall require no exporter, collector or network access and shall behave as in Requirement 3.
4. If the exporter is unreachable or fails, the system shall not replay inference, block requests indefinitely, leak secrets or corrupt streaming; queued telemetry shall be bounded and flushed on shutdown.

### Requirement 6: Operator guidance and offline verification

**Objective:** As an operator, I want documented examples and offline tests for local diagnosis and collector setup, so that observability can be verified without live credentials or paid inference.

#### Acceptance Criteria

1. The documentation shall include operator examples for local diagnosis, a collector/backend setup and expected failure modes when the agent host updates.
2. When observability behavior is tested, the tests shall run offline using owned sinks and shall not require real credentials, network access or a running collector.
3. When a new agent-host version changes observable behavior, the documentation shall describe how to re-run the diagnostics checks against it.
