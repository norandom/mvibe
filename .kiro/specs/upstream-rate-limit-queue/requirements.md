# Requirements Document

## Project Description (Input)
Check the mvibe proxy part that offers local API endpoints and the OpenCode plugin. When a Mistral API rate limit is hit: (a) detect it, (b) queue the request instead of failing at the consumer level. The operator's workspace limit for Mistral Large is 0.5 requests per second (https://admin.mistral.ai/plateforme/limits).

## Introduction
mvibe forwards every consumer request to Mistral's inference API the moment it arrives. Mistral enforces workspace limits (requests per second, tokens per minute, tokens per month; the operator's workspace shows 0.5 requests per second for Mistral Large), and a request that exceeds a limit is rejected with HTTP 429 before any inference runs. Today both consumers surface that rejection as a failure: the local proxy forwards the upstream 429 on the raw `/v1` routes and translates it on the Ollama and Anthropic adapter routes, and the OpenCode provider plugin returns it to the host with host retries disabled, so the agent turn fails. Agent clients burst (parallel tool calls, several sessions or subagents, compaction), so at 0.5 requests per second this failure is routine. This feature makes both consumers detect a rate-limit rejection and hold the request until it can be sent, instead of failing it at the consumer.

## Boundary Context
- **In scope**: The upstream request path of the proxy (every route that reaches Mistral: raw `/v1` passthrough, Ollama `/api/*` and Anthropic `/v1/messages` adapters) and the OpenCode plugin's provider request path including model-catalog discovery; rate-limit detection; holding, ordering and pacing of outbound requests; operator configuration; request diagnostics for held requests.
- **Out of scope**: Authentication and credential handling; the authentication helper protocol and its version coupling; changes to Mistral-side limits; coordination between a proxy process and a plugin process (each paces its own outbound requests); the OpenCode host's own retry policy; predicting token-per-minute consumption; re-sending after a response has started toward the client; upstream statuses other than 429, which keep their current forwarding.
- **Adjacent expectations**: An upstream 429 is a pre-inference rejection, so re-sending the identical request consumes no additional inference and is not a replay of inference. The existing credential snapshot lifetime applies to each individual attempt. The OpenCode host keeps honoring the provider's `x-should-retry: false` response header.

## Requirements

### Requirement 1: Rate-limit rejection detection
**Objective:** As an operator, I want both consumers to recognize an upstream rate-limit rejection and its wait guidance, so that the rejection is handled as a pacing signal rather than as an ordinary error.

#### Acceptance Criteria
1. When the upstream responds with HTTP status 429 to a request from the proxy or the plugin, the consumer shall classify the response as a rate-limit rejection regardless of the response body shape.
2. When a rate-limit rejection carries a `Retry-After` header in delay-seconds or HTTP-date form, the consumer shall derive the wait duration from that header.
3. If a rate-limit rejection carries no usable `Retry-After` header, the consumer shall derive the wait duration from a bounded exponential backoff with jitter that starts at the configured pacing interval, or at 1 second when no pacing is configured, and never exceeds 30 seconds for a single wait.
4. When a rate-limit rejection carries `x-ratelimit-*` headers, the consumer shall record their names and values in the request diagnostic.
5. The consumer shall not treat any status other than 429 as a rate-limit rejection; other upstream statuses keep their current forwarding behavior.
6. While recording rate-limit diagnostics, the consumer shall not include credentials, request or response bodies, or prompts.

### Requirement 2: Queue instead of fail at the proxy
**Objective:** As a user of an agent client behind the local proxy, I want a rate-limited request held and sent later, so that my agent turn completes instead of failing with 429.

#### Acceptance Criteria
1. When the upstream rejects a proxy request with a rate limit and no response has been started toward the client, the proxy shall hold the request, wait the derived duration, and re-send the identical request to the upstream without returning the 429 to the client.
2. While a rate-limit hold is active for a model, the proxy shall hold further requests for that model behind it in arrival order and shall not send them to the upstream until the hold ends.
3. While a request is held, the proxy shall keep the client connection open and send no response bytes.
4. If the client disconnects while its request is held, the proxy shall discard the request without sending it to the upstream and record the outcome as cancelled.
5. If a request has been held longer than the configured maximum wait (default 120 seconds), or accepting it would exceed the configured maximum number of held requests (default 64), the proxy shall return a 429 response to the client with a `Retry-After` header and a hint that the upstream rate limit persisted; when an upstream 429 was received for that request, its last body and `x-ratelimit-*` headers are forwarded unchanged.
6. The proxy shall apply criteria 2.1 to 2.5 on every route that reaches the upstream, including the Ollama and Anthropic adapter routes, whose error bodies keep their dialect shape.
7. When a response has already started toward the client, the proxy shall not re-send the request for any reason.
8. When a held request is finally sent, the proxy shall obtain a fresh credential snapshot for that attempt rather than reuse one obtained before the wait.

### Requirement 3: Configurable proactive pacing
**Objective:** As an operator who knows the workspace limit, I want outbound requests spaced to that rate, so that bursts rarely trigger a 429 at all.

#### Acceptance Criteria
1. Where an operator configures a default requests-per-second rate (for example 0.5 for a workspace limited to 0.5 requests per second), the consumer shall start inference requests no faster than that rate and hold excess requests in arrival order.
2. Where the operator configures per-model rates, the consumer shall apply the model-specific rate to requests naming that model and the default rate to other inference requests.
3. Where no pacing rate is configured, the consumer shall send requests without proactive spacing and rely on rejection handling alone.
4. If the pacing, maximum-wait or queue-depth configuration is invalid (non-numeric, non-positive, or a malformed per-model list), the proxy shall refuse to start with a message naming the setting, and the plugin shall fail provider setup with a message naming the option.
5. The consumer shall not apply proactive pacing to model-list discovery requests; those remain subject to rejection handling only.
6. The consumer shall let the operator override the maximum wait and the maximum number of held requests alongside the pacing rate.

### Requirement 4: Queue instead of fail in the OpenCode plugin
**Objective:** As an OpenCode user of the Mistral-Vibe provider, I want rate-limited requests held and re-sent inside the provider, so that the host sees a slower response rather than a failed turn.

#### Acceptance Criteria
1. When the upstream rejects a plugin request with a rate limit, the provider shall hold, wait and re-send the identical request (same method, header policy and body) under the same ordering, pacing, maximum-wait and queue-depth rules as the proxy (criteria 2.1, 2.2, 2.5 and 3.1 to 3.6).
2. If the host cancels a request while it is held, the provider shall discard it without sending it to the upstream and report the cancelled outcome.
3. If the maximum wait or queue depth is exceeded, the provider shall return the last upstream 429 response unchanged in status, headers and body (with `x-should-retry: false` added as today), or a 429 with a `Retry-After` header when no upstream attempt was made, so the host keeps its current error presentation.
4. The provider shall keep host-level retries disabled; the provider owns all waiting.
5. The provider shall apply rejection handling to model-catalog discovery requests while respecting the discovery deadline; a discovery attempt that cannot be sent before its deadline fails as a timeout as today.
6. When a held request is finally sent, the provider shall obtain a fresh credential snapshot for that attempt.
7. The provider shall read the pacing rate, per-model rates, maximum wait and queue depth from provider options in the OpenCode configuration.

### Requirement 5: Operator visibility
**Objective:** As an operator, I want to see when and how long requests were held, so that I can tune the configured rate and spot persistent limiting.

#### Acceptance Criteria
1. When a request was held before being sent, the consumer shall record the total held duration in milliseconds and the number of upstream rate-limit rejections encountered in the request diagnostic.
2. If a request is returned to the client as rate-limited after exhausting the maximum wait or queue depth, the consumer shall record a distinct outcome identifying the exhaustion and a hint naming the setting to adjust.
3. When a rate-limit hold begins for a model, the proxy shall emit one warning-level log line naming the model and the wait duration, without request content.
4. Where diagnostic export is enabled, the proxy shall include the held duration, rejection count and outcome in the exported record under the existing allowlist discipline.
5. The plugin shall include the held duration and rejection count in its opt-in diagnostics output and shall not write them to ordinary host logs when diagnostics are disabled.

### Requirement 6: Compatibility and safety
**Objective:** As a maintainer, I want the queueing to leave existing contracts intact, so that the change is low-risk to ship.

#### Acceptance Criteria
1. The consumer shall deliver Requirements 1 to 5 without changes to the authentication helper protocol, its version coupling, credential storage or sign-in flows.
2. When the upstream accepts a request on the first attempt and no pacing applies, the proxy shall forward status, headers and body bytes exactly as today.
3. The consumer shall bound the memory used by held requests through the configured queue depth and shall retain a held request's prepared body only until its final attempt is sent or the request is discarded.
4. When the consumer returns a 429 to its client, the consumer shall include the upstream `Retry-After` and `x-ratelimit-*` headers when they were received.
