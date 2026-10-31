# Requirements Document

## Introduction
Developers using OpenCode and similar OpenAI-compatible agents need a local proxy that authenticates through Vibe's separate Mistral browser OIDC flow and subscription identity. The implementation exists but has not been systematically verified. This specification captures intended observable behavior, documents current internals, and establishes offline tests before separately enabled live verification.

These requirements are proposed contracts, not claims that the current implementation satisfies them. Existing evidence is recorded in [current-state.md](current-state.md); discovery scope and candidate verification are in [brief.md](brief.md) and [verification-plan.md](verification-plan.md). Core steering documents are absent; these approved discovery inputs provide the project context.

## Boundary Context
- **In scope:** A reusable Vibe OIDC authentication library and an OpenCode provider plugin sharing that implementation; local agent access to models, chat completions including streaming/tool calls, and embeddings through Vibe OIDC; browser login, stored identity reuse, refresh, organization/workspace selection, configuration, failures, documentation and verification.
- **Out of scope:** API-key authentication or fallback, Responses API, public hosting, additional provider protocols, modifications to installed Vibe, and claims of universal agent compatibility.
- **Adjacent expectations:** Mistral controls issuer registration, subscription/model entitlement, remote payloads and availability. Clients supply compatible requests and configuration. Vibe may share stored credentials with this proxy; concurrent re-login across applications is not guaranteed safe.
- **Verification boundary:** Specification capture performs no live login or inference. Offline verification and opt-in live verification provide distinct evidence; neither substitutes for the other.
- **Terminology:** “Proxy” denotes the local tool; “Verification Suite” denotes its automated offline checks; “Project Documentation” denotes the operator and engineering documentation. Requirement references use section and criterion numbers, for example 1.1.

## Requirements

### Requirement 1: Vibe OIDC Identity
**Objective:** As an agent user, I want Vibe subscription authentication so that I can use the proxy without Mistral API keys.

#### Acceptance Criteria
1. When upstream authentication is required, the Proxy shall derive credentials exclusively from Vibe's separate browser OIDC flow or credentials previously obtained through that flow.
2. If OIDC authentication fails, the Proxy shall report the authentication failure without falling back to API-key authentication.
3. When an agent supplies a placeholder API key or Authorization header, the Proxy shall exclude that caller credential from upstream authentication.
4. When authenticating an inference request, the Proxy shall use the ID-token credential required by the existing Vibe integration.
5. When the separate UserInfo helper authenticates its identity probe, the Proxy shall use the access-token credential for that probe.
6. If the destination is outside the configured OIDC credential destination policy, the Proxy shall refuse to attach OIDC credentials.

### Requirement 2: Browser Sign-in
**Objective:** As an operator, I want a bounded browser sign-in flow so that I can establish a Vibe identity locally.

#### Acceptance Criteria
1. When startup finds no readable stored identity matching the active authentication configuration, the Proxy shall initiate browser sign-in before serving agent requests.
2. When the operator requests forced sign-in, the Proxy shall initiate a new browser sign-in before serving agent requests.
3. When an authorization URL becomes available, the Proxy shall display that URL and attempt to open it in the operator's browser.
4. When browser sign-in starts, the Proxy shall bind the authorization result to that sign-in attempt using state, nonce and proof of possession of the authorization-code verifier.
5. If a callback has an incorrect method, path, missing or duplicate required parameter, or mismatched state, the Proxy shall reject it as an authorization result.
6. If the issuer returns a matching authorization denial, the Proxy shall report sign-in failure.
7. If sign-in does not complete within 300 seconds, the Proxy shall terminate the attempt and report failure.
8. When a sign-in attempt completes, fails or is cancelled, the Proxy shall release its callback listener and pending callback activity.
9. If sign-in fails, the Proxy shall not announce successful startup for agent requests.

### Requirement 3: Token Validation and Persistent Identity
**Objective:** As an operator, I want validated credentials reused across launches so that sign-in remains consistent with the intended Vibe identity.

#### Acceptance Criteria
1. When receiving an initial token response, the Proxy shall validate the ID-token signature, issuer, client audience, nonce, subject and lifetime before accepting the identity.
2. If discovery identifies a different issuer or a token uses an unsupported signing algorithm, the Proxy shall reject the authentication result.
3. If the token response lacks a usable bearer access token, a positive finite access-token lifetime or the required initial ID token, the Proxy shall reject it.
4. When sign-in succeeds, the Proxy shall persist credentials in the Vibe-compatible operating-system credential store for subsequent launches.
5. When readable stored credentials match the active issuer, client and audience, the Proxy shall reuse that identity without a new browser sign-in unless forced sign-in is requested.
6. If credentials cannot be persisted after initial sign-in, the Proxy shall report sign-in failure rather than claim successful persistent login.
7. When a credential update encounters a stored identity or token set changed since the update began, the Proxy shall avoid overwriting the newer credential set.
8. When displaying credential objects or authentication failures, the Proxy shall omit token values and raw token-response contents.
9. When credential resolution detects that stored credentials changed during login or refresh, the Shared Auth Library shall preserve the newer credentials and retry resolution with a bounded retry budget before returning a retryable failure.

### Requirement 4: Refresh and Authentication Expiry
**Objective:** As an agent user, I want credentials refreshed when possible so that routine expiry does not require repeated browser sign-in.

#### Acceptance Criteria
1. When resolving credentials whose access token expires within 30 seconds, the Proxy shall attempt refresh before forwarding the authenticated request.
2. If the inference ID token has no usable expiry or expires within 30 seconds, the Proxy shall attempt refresh before using it for inference.
3. If refresh does not provide an ID token valid beyond the 30-second freshness window, the Proxy shall refuse to forward the inference request with that token.
4. If refresh credentials are missing or the refresh grant is rejected, the Proxy shall report authentication failure with re-login guidance.
5. If a refresh response changes the authenticated subject, the Proxy shall reject the identity change.
6. When a valid refresh response omits a replacement refresh token, the Proxy shall retain the previously accepted refresh token.
7. When concurrent requests within one proxy process require refresh of the same identity, the Proxy shall serialize refresh resolution to avoid competing refresh operations in that process.
8. When an upstream inference request returns 401, the Proxy shall relay that response without automatically replaying the request.

### Requirement 5: Organization and Workspace Selection
**Objective:** As an operator, I want predictable tenant selection so that agent requests use the intended Vibe organization and workspace.

#### Acceptance Criteria
1. When an organization override is configured, the Proxy shall use that organization without performing workspace discovery.
2. When organization and workspace overrides are both configured, the Proxy shall attach the configured organization and workspace context.
3. When a workspace is selected without an organization override, the Proxy shall discover the organization associated with that exact accessible workspace.
4. When no tenant override is configured, the Proxy shall select the first accessible workspace returned by workspace discovery.
5. When workspace discovery spans multiple pages, the Proxy shall continue until the applicable workspace is found or the results are exhausted.
6. If discovery yields no applicable workspace or cannot produce a valid tenant result, the Proxy shall report authentication failure without forwarding the inference request.
7. When reusing cached tenant information, the Proxy shall restrict reuse to the matching authenticated identity and workspace selection.
8. If persistence of newly resolved tenant information fails but usable request credentials and tenant context are available, the Proxy shall allow that request to proceed.

### Requirement 6: Local Agent Interface
**Objective:** As an agent user, I want a defined local interface so that I can configure an OpenAI-compatible client.

#### Acceptance Criteria
1. When launched with default settings, the Proxy shall listen on 127.0.0.1 port 8765.
2. When an operator supplies a valid port override, the Proxy shall listen on that port while retaining loopback-only binding.
3. When a client requests GET /health after startup, the Proxy shall return status 200 with {"status":"ok"} without making an upstream readiness request.
4. When a client requests GET /v1/models, POST /v1/chat/completions or POST /v1/embeddings, the Proxy shall route the operation to the corresponding endpoint of the configured audience.
5. If a client sends another GET or POST operation under /v1/, the Proxy shall return 404 with an unsupported-endpoint error without forwarding it.
6. If a client uses another method for the proxy API, the Proxy shall reject it without forwarding it.
7. When serving the loopback interface, the Proxy shall accept requests without requiring a local caller credential.

### Requirement 7: Request and Response Passthrough
**Objective:** As an agent user, I want compatible payloads preserved so that model responses and tool calls reach my client.

#### Acceptance Criteria
1. When forwarding an allowed operation, the Proxy shall preserve the request method, query parameter values and request-body bytes.
2. When forwarding an allowed operation, the Proxy shall supply OIDC-derived authentication, resolved tenant context and application/json request content type.
3. When the upstream service returns a response, the Proxy shall preserve its status code and content type, using application/json when the upstream content type is absent.
4. When the upstream response contains chat messages, embeddings, model listings or tool-call data, the Proxy shall relay the response content without translating its application payload.
5. While an upstream response is streaming, the Proxy shall relay available response data without waiting for the complete response.
6. When relaying a response, the Proxy shall include Cache-Control: no-cache.
7. When an upstream response completes or streaming is interrupted by an error or client cancellation, the Proxy shall release the associated upstream response.
8. If streaming fails after response headers have been sent, the Proxy shall terminate that stream without appending a replacement JSON error as successful response content.
9. When an upstream response redirects, the Proxy shall not automatically follow that redirect using the request credentials.

### Requirement 8: Configuration and Failure Reporting
**Objective:** As an operator, I want explicit configuration and bounded failure behavior so that I can diagnose failures without exposing credentials.

#### Acceptance Criteria
1. When supported VIBE_CLI_OIDC_* overrides are supplied, the Proxy shall apply the selected issuer, client, audience, callback, scope and tenant settings.
2. If OIDC is explicitly disabled, the Proxy shall refuse OIDC authentication rather than silently re-enable it or use API keys.
3. If authentication configuration violates the documented URL, callback or scope constraints, the Proxy shall reject it before sending authenticated inference requests.
4. If authentication fails while preparing a proxy request, the Proxy shall return 401 with guidance to run proxy.py --login.
5. If an upstream connection fails before response headers are sent, the Proxy shall return 502 with a generic upstream-connection error.
6. When reporting proxy authentication or connection errors, the Proxy shall omit token values, authorization codes and raw credential-store errors.
7. If the upstream returns an application error response, the Proxy shall relay its status and response content without representing it as a successful inference result.

### Requirement 9: Implementation and Operator Documentation
**Objective:** As a maintainer, I want the existing behavior and assumptions captured so that testing and later changes have a reviewable baseline.

#### Acceptance Criteria
1. When documenting the implementation baseline, the Project Documentation shall identify the examined source revision or hashes and the inspection date.
2. When capturing internals, the Project Documentation shall describe the sign-in, validation, storage, refresh, tenant-selection, forwarding and cleanup flows with source-file references.
3. When describing behavior, the Project Documentation shall distinguish code-observed behavior, intended requirements, suspected defects and unverified external assumptions.
4. When describing agent setup, the Project Documentation shall provide the loopback base URL, supported operations, model-ID discovery and the placeholder-only role of a client API-key field.
5. When describing operation, the Project Documentation shall state that local caller authentication is absent, health is local liveness only, and account entitlement controls model availability.
6. When describing shared credentials, the Project Documentation shall warn that concurrent Vibe/proxy re-login can contend for the same credential slot.
7. When Vibe-derived code is changed, the Project Documentation shall retain license attribution and record deviations from the documented source baseline.

### Requirement 10: Offline Automated Verification
**Objective:** As a maintainer, I want repeatable offline code tests so that behavior can be checked before using a live account.

#### Acceptance Criteria
1. When the default Verification Suite runs, the Verification Suite shall avoid the real operating-system credential store, browser launch, live sign-in and remote service requests.
2. When verifying authentication and forwarding, the Verification Suite shall use synthetic credentials and controlled external responses.
3. When verifying time-sensitive behavior, the Verification Suite shall exercise expiry, timeout and refresh boundaries without waiting for real token expiry.
4. When a test modifies cached identity, tenant or authentication configuration, the Verification Suite shall isolate that state from subsequent cases.
5. When verifying the proxy, the Verification Suite shall cover allowed and rejected operations, payload preservation, error responses, streaming and response cleanup.
6. When verifying authentication, the Verification Suite shall cover callback rejection, token-validation failure, credential reuse/storage failure, refresh rotation/failure/concurrency and tenant selection/failure.
7. When verifying credential transport, the Verification Suite shall cover destination restrictions, redirect behavior and supported proxy/trust configuration using controlled destinations and responses.
8. When reporting verification, the Verification Suite shall associate checks with requirement IDs and distinguish passed, failed and skipped checks.
9. When maintainers run the documented offline verification command, the Verification Suite shall return a failing exit status if any mandatory check fails.
10. When a characterization check demonstrates a suspected defect, the Project Documentation shall record the discrepancy without declaring that behavior compliant with the intended requirement.

### Requirement 11: Separate Live Verification
**Objective:** As an operator, I want live checks separated from offline tests so that account interaction occurs deliberately and produces identifiable evidence.

#### Acceptance Criteria
1. When the default offline command runs, the Verification Suite shall exclude live account checks unless they are explicitly enabled by a separate live-verification action.
2. When defining live verification, the Project Documentation shall provide a sequence covering browser OIDC, credential reuse, tenant selection, model listing, minimal chat, streaming, tool-call exchange and embeddings where the account supports them.
3. When recording live results, the Project Documentation shall identify the client/provider and runtime versions, sanitized configuration and result of each exercised operation.
4. If refresh or another live scenario has not been exercised, the Project Documentation shall mark it unverified rather than infer success from offline tests.
5. If the account lacks a model or capability, the Project Documentation shall distinguish that entitlement limitation from an established proxy defect.
6. When recording verification artifacts, the Project Documentation shall exclude real tokens, authorization codes, transient sign-in URLs and private prompt/account contents.
7. When assessing readiness, the Project Documentation shall report offline evidence, local startup evidence and live compatibility evidence separately.

### Requirement 12: Shared Authentication Library and OpenCode Provider
**Objective:** As a maintainer and OpenCode user, I want one reusable Vibe OIDC implementation so that the proxy and provider plugin share authentication behavior.

#### Acceptance Criteria
1. When the proxy or OpenCode provider needs authentication, the Shared Auth Library shall own login, validation, persistence, refresh, tenant selection and credential cycling through one implementation.
2. When another consumer integrates authentication, the Shared Auth Library shall expose a documented public interface independent of proxy HTTP routing and OpenCode hooks.
3. When OpenCode loads the plugin, the Provider Plugin shall register a distinct Vibe OIDC provider with a browser OIDC connection method.
4. When a provider request begins, the Provider Plugin shall resolve current credentials through the Shared Auth Library instead of retaining a fixed credential from plugin startup.
5. When available models are requested, the Provider Plugin shall obtain model IDs through authenticated Vibe access and expose only models with sufficient verified or operator-configured metadata.
6. When forwarding a provider inference request, the Provider Plugin shall preserve streaming and tool-call content and propagate caller cancellation.
7. When persisting OpenCode connection state, the Provider Plugin shall store only non-secret connection metadata and leave refresh credentials in the shared library's credential store.
8. If the shared library runtime or adapter protocol is unavailable or incompatible, the Provider Plugin shall report an actionable setup error without falling back to API-key authentication.
9. When the provider is disposed or its host connection closes, the Provider Plugin shall release its owned helper and pending authentication activity.
10. When verifying either consumer, the Verification Suite shall exercise the shared authentication contract and the provider adapter without live credentials.



## Review Status
Requirements review gate: passed inline after mechanical checks and scope/testability review. Protocol names and endpoint identifiers above specify the existing external contract; component choices, test frameworks and implementation mechanisms remain deferred to design. Requirements were approved by the explicit design -y command; shared-library, credential-cycling and provider scope were subsequently requested by the user. No code tests or live checks have been executed in this phase.


