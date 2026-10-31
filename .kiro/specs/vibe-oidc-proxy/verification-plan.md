# Proposed Verification Plan

## Status
Discovery proposal, not an approved design or task list. Tests have not been added or run. Authentication is exclusively Vibe OIDC; API-key authentication and fallback are excluded.

## Sequence and Evidence
1. Initialize the Kiro spec and generate testable requirements from the brief. Review requirements before design.
2. Perform brownfield gap analysis. Classify each observation as implemented behavior, intended contract, suspected defect or external assumption.
3. Design isolated tests and approved behavior changes, then generate tasks with requirement IDs and explicit review gates.
4. Implement the offline suite and required seams. Record exact commands, dependency environment, results and unresolved gaps.
5. Run a separately enabled, operator-controlled live smoke only after offline evidence and the review gates support it.

No canonical test command exists yet. Select and document the runner/dependency configuration during design; pytest with asynchronous support is a candidate, not an installed or validated toolchain.

## Offline Isolation
Use synthetic tokens and a fake credential store; never read/write the real Vibe slot. Reset process caches and locks between relevant cases. Block browser opening and remote network calls by default. Use injected transports and fake discovery/token/JWKS/tenant responses. Test callback parsing with fake readers/writers first; any actual loopback socket tests form an explicit separate layer. Prevent application lifespan from launching real login.

## Candidate Coverage
| Area | Cases and expected evidence |
| --- | --- |
| Proxy routes | Three allowed operations, rejected routes/methods, query/raw-body preservation, incoming credential exclusion |
| Proxy lifecycle | Stored/missing/mismatched credentials, forced login, shared client startup and cleanup, loopback CLI binding |
| Responses | JSON and SSE chunks, tool-call payload preservation, status/content-type, upstream close on success/failure/cancellation |
| Error boundaries | OIDC 401, setup 502, configuration outside try, stream failures after response starts, no implicit 401 replay |
| Configuration | Defaults, overrides, feature flag, URL/scope rejection, custom issuer/audience edge cases |
| Login | Discovery issuer checks, PKCE/state/nonce, token validation, JWT algorithm/issuer/audience/subject/expiry failures |
| Callback | Wrong method/path/state, duplicate/missing parameters, OAuth error, bounded input/concurrency, timeout and cleanup |
| Credentials | Serialization masking versus persistence, malformed/mismatched bundles, fake keyring failure, cache reset |
| Refresh | Expiry thresholds, token rotation, omitted ID/refresh token, invalid_grant, subject mismatch, concurrent resolution and stale-save prevention |
| Tenant | Explicit organization/workspace, pagination, empty result, selected workspace missing, cache identity and persistence failure |
| HTTP | Destination restrictions, marker replacement, redirect policy, environment proxies/NO_PROXY, injected transport and TLS setup |
| Separate helpers | Access-token UserInfo probe, subject check and rejected credential snapshot without replay |

Do not freeze a suspected defect into the desired contract merely because a characterization test reproduces it. Track the decision and any approved behavior change separately.

## Review and Readiness Gates
- Requirements: numbered, unambiguous acceptance criteria; explicit Vibe OIDC scope and exclusions.
- Design: current versus intended behavior distinguished; no real-keyring/network leakage in tests; configuration and error boundaries resolved.
- Tasks: traceable coverage, implementation/review ownership and validation evidence.
- Offline completion: fresh suite output, no hidden remote calls, no skipped critical cases described as passing; dependency/import checks are separate evidence.
- Live readiness: local startup smoke plus offline coverage and unresolved-assumption review; offline success does not prove subscription inference.

## Later Opt-in Live Smoke
Record client/version, Python/dependency versions and sanitized configuration. Avoid simultaneous Vibe/proxy re-login because credentials are shared. Exercise browser OIDC, credential reuse, tenant selection, model listing, a minimal chat request, streaming, a tool-call round trip and embeddings only where supported by the account. Observe refresh when feasible; otherwise mark it unverified. Check shutdown and subsequent startup. Record unsupported model/account capabilities separately from proxy defects.

Do not log tokens, callback codes, authorization URLs containing transient secrets, raw prompts or account identifiers in evidence. Live execution may affect the shared credentials and consume account quota; none is performed during discovery.

## Revalidation Triggers
Vibe-derived code updates, dependency changes, issuer/client/redirect/audience changes, token or tenant schema changes, transport changes, and agent/provider upgrades require targeted regression checks and renewed live evidence where relevant.
