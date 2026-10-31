# Design Validation: vibe-oidc-proxy

## Review Summary
**GO for task generation.** The design provides a coherent shared Python Vibe OIDC library, standalone proxy and thin OpenCode provider adapter. Independent document/contract and codebase-pattern reviews found no critical architectural blocker; runtime and live compatibility remain unverified.

Reviewed on 2026-10-31 against 12 requirement areas and 97 criteria. Core steering is absent; requirements, user direction, existing code and Kiro review rules supplied context. All 97 criterion IDs appear in the design mapping; ID presence alone is not proof of implementation coverage.

## Critical Issues
None established. The following are nonblocking task-planning clarifications, not claims of runtime defects.

## Clarifications to Carry into Tasks

### 1. Coordinate every token-bundle mutation
**Concern:** Tenant persistence rewrites the complete token bundle. The coordinator description names refresh and conditional save but does not explicitly enumerate tenant writes.
**Impact:** An implementation that leaves tenant save outside the shared lock could overwrite a concurrent rotation from another participating process.
**Recommendation:** Require coordinator-before-slot locking for tenant writes as well as login/refresh writes, or explicitly retain the coordinator through tenant resolution and persistence. Add a two-process synthetic-store race case.
**Traceability:** 3.7, 3.9, 5.7.
**Evidence:** design.md:177; mvibe_auth/_oidc_credentials.py:107; mvibe_auth/_oidc_runtime.py:167.

### 2. Assign immutable configuration ownership
**Concern:** The facade promises immutable AuthConfig while existing helpers repeatedly read environment configuration and feature flags.
**Impact:** A partial migration could make one session observe inconsistent configuration or introduce an upward dependency into the facade.
**Recommendation:** Locate the primitive config type below service/facade, specify its mapping to OidcConfig, and include service/runtime/marker environment-read migration in the file boundaries. Test that a session retains its configuration while a newly created session sees changes.
**Traceability:** 8.1–8.3, 12.1–12.2.
**Evidence:** design.md:51,163,174; mvibe_auth/_oidc_service.py:182; mvibe_auth/_oidc_runtime.py:115; mvibe_auth/oidc_marker.py:18.

### 3. Define abnormal cleanup outcomes
**Concern:** The design permits terminating an unresponsive helper after five seconds and aborting a client when stream close times out.
**Impact:** Forced termination can leave persistence outcome unknown; retiring a lifespan-shared client affects concurrent and subsequent requests.
**Recommendation:** Distinguish graceful worker drain from forced termination, require authoritative credential reload on helper restart, and choose/document client retirement versus replacement. Test cancellation before/during persistence, sibling requests and the next request after retirement. Clarify resolve cancellation by request ID versus helper retirement.
**Traceability:** 7.7, 7.8, 12.6, 12.9.
**Evidence:** design.md:181,203,221,235; proxy.py:52; mvibe_auth/_oidc_credentials.py:134.

## Design Strengths
- One authoritative OIDC implementation serves both consumers, preserves the ID-token/access-token distinction and keeps refresh tokens out of OpenCode storage.
- Bounded credential cycling, revision-aware tenant resolution, explicit response ownership and isolated verification provide a concrete basis for implementation.

## Final Assessment
GO means the architecture can proceed to implementation-task planning with the three clarifications included. It does not approve the design on the user's behalf, attest that dependencies interoperate, or claim feature readiness. No source code, requirements or design were changed during this review; no tests, login or inference were run.

Next: approve the design and generate tasks with `$kiro-spec-tasks vibe-oidc-proxy -y`. Task briefs should explicitly incorporate the clarifications above.

## Review Evidence
- Independent context/contracts review: GO.
- Independent codebase-pattern review: GO with the three nonblocking clarifications.
- Main synthesis: GO; no need to replace or split the shared-authentication architecture.
- Mechanical traceability: 97/97 criterion IDs present.
- Reviewed design SHA256: 3BC21D7D79D746A6FCACB1A78332339A290C956F6F148483532F2499E5786FFB.
- Reviewed requirements SHA256: 71D4521E1CAF1DF7B2F75A287A6329B2E570900FE7917108D292F5C5ED8DC9FA.
- spec.json retains design-generated, design approval false and implementation readiness false.

