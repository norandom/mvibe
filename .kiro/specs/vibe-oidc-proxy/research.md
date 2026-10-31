# Implementation Gap Analysis: vibe-oidc-proxy

## Analysis Context
- Date: 2026-10-31. Language: en. Method: static code inspection, independent authentication/proxy/context exploration, requirement-to-asset mapping and synthesis.
- Requirements: 11 areas, 86 acceptance criteria; generated but **not approved**. This analysis does not grant approval or advance the phase.
- Core steering directory is absent. Approved discovery scope, requirements and source provide context. Historical “next step” text in brief.md is not current lifecycle metadata; spec.json is authoritative.
- No imports, dependency installation, runtime/test execution, browser launch, credential-store access, live authentication or inference occurred.
- **Supported** below means a visible implementation path, not demonstrated runtime compliance. **Missing** identifies absent behavior/artifacts; **Unknown** needs execution or external evidence; **Constraint** identifies an architectural, workflow or adjacent-system limit. Partial rows identify the exact shortfall.
- Authentication remains exclusively Vibe's separate browser OIDC/subscription flow. The inference audience URL does not imply API-key authentication. No option introduces API keys or fallback.

## Current Assets and Conventions
A standalone PEP 723 Python >=3.12 script, proxy.py, hosts a FastAPI application with lifespan initialization, three allowlisted upstream operations and streaming responses. It directly imports private Vibe-derived authentication helpers. There is no project manifest, lockfile, executable test suite, test configuration or CI configuration in the examined inventory.

The mvibe_auth package contains 13 Python modules. Private underscore modules hold OIDC configuration, callback, client, service, persistence and runtime header resolution. Public http.py implements the HTTPX2 transport and TLS/proxy behavior; concurrency.py bridges sync/async execution. Typed platform credentials and UserInfo helpers form another reusable surface, separate from the normal inference forwarding path.

Important dependency directions:
- proxy.py -> OIDC configuration/runtime/service and shared HTTP client.
- Runtime -> service/store plus shared HTTP transport; HTTP marker hook imports runtime when needed.
- Service -> OIDC client/callback/store and typed UserInfo helper.
- Package exports -> platform HTTP helper -> shared HTTP helper. Preserve import initialization behavior when introducing test seams.
- Global environment, keyring cache, tenant cache, TLS configuration and thread locks affect multiple tests; fresh service objects alone do not isolate state.

Dependencies are declared in proxy.py:1: some authentication packages are pinned, while framework/transport support packages are partly unpinned; both httpx and httpx2 are declared. Installation, library claim-validation semantics and ASGI compatibility remain **Unknown**. Deep dependency research is deferred to design rather than inferred from declared versions.

Reusable seams include fake app.state.client, replacement header/login functions, injected HTTPX2 transport, fake OIDC session/store, fake callback reader/writer, controlled time and credential-cache reset. VIBE_TEST_DISABLE_KEYRING disables loads but fails writes, so it cannot replace a successful fake store.

## Requirement-to-Asset Map
All criterion ranges refer to requirements.md. Source references identify the inspected file and starting line.

| Criteria | Existing assets | Assessment and gap tags |
| --- | --- | --- |
| 1.1–1.4 | proxy.py:75; _oidc_runtime.py:114,222 | Supported: generated OIDC headers, inference ID token, caller Authorization excluded; no API-key fallback. **Unknown:** actual Vibe inference acceptance. |
| 1.5 | _oidc_service.py:230; _oidc_platform_credentials.py:53 | Supported: separate UserInfo probe uses access token. **Unknown:** live issuer behavior. |
| 1.6 | _oidc_runtime.py:115; _oidc_client.py:78 | Supported destination checks before resolution; issuer-origin UserInfo restriction. **Constraint:** exact destination policy must be documented in design. |
| 2.1–2.3 | proxy.py:31,48,121; _oidc_runtime.py:41 | Supported startup/forced browser login and display/open URL. **Unknown:** browser failure experience and actual launch. |
| 2.4 | _oidc_service.py:81; _oidc_client.py:47 | Supported state, nonce and S256 verifier flow. **Unknown:** dependency enforcement needs controlled exchange tests. |
| 2.5 | _oidc_callback.py:55 | Partial / **Missing:** blank duplicate parameter rejection; see G1. |
| 2.6–2.9 | _oidc_callback.py:30,67; _oidc_service.py:76,122,163; proxy.py:40 | Supported denial, 300-second attempt timeout, cleanup and failure before serving. **Unknown:** cancellation and blocked worker timing. |
| 3.1–3.3 | _oidc_client.py:68,99,114 | Supported signature/claims/subject/token-response validation and RS/ES allowlist. **Unknown:** executable positive/negative JWT coverage and exact library claim semantics. |
| 3.4–3.6 | _oidc_credentials.py:12,185; _oidc_runtime.py:48; _oidc_service.py:112,184 | Supported Vibe slot persistence, matching identity reuse and persistence failure. **Unknown:** real keyring/platform compatibility. Reuse at startup does not check freshness. |
| 3.7 | _oidc_credentials.py:117; _oidc_service.py:114,224 | Partial / **Constraint:** conditional refresh save exists; initial login save is unconditional and compare/write is not cross-process atomic. Requirement scope needs review; see G4. |
| 3.8 | _oidc_credentials.py:46,54; _oidc_client.py:34; _oidc_service.py:122 | Partial / **Unknown:** SecretStr representation and sanitized failure paths exist, but deliberate storage serialization exposes tokens. Verify every display/log boundary; storage serialization is not safe display. |
| 4.1–4.3 | _oidc_service.py:184; _oidc_runtime.py:222,242 | Supported 30-second access/ID freshness, forced refresh and stale-ID rejection. **Unknown:** boundary execution and live refreshed ID-token availability. |
| 4.4 | _oidc_service.py:201; proxy.py:90 | Supported re-login response at proxy boundary; helper distinguishes invalid_grant from other OAuth failures. **Constraint:** helper retry advice differs from proxy generic guidance. |
| 4.5–4.6 | _oidc_client.py:157 | Supported subject continuity and retained refresh token. **Unknown:** omitted/new ID-token combinations and rotation tests. |
| 4.7–4.8 | _oidc_runtime.py:27,222; proxy.py:80,107 | Supported process-wide proxy resolution lock and no inference 401 replay. **Constraint:** directly created service instances/cross-process Vibe are not protected by that lock. |
| 5.1–5.5 | _oidc_runtime.py:130,195 | Supported overrides, exact/default selection and page traversal. **Unknown:** actual remote workspace schema and ordering. |
| 5.6 | _oidc_runtime.py:32,152,210 | Partial / **Missing:** reject empty tenant IDs; see G3. Existing empty/no-match/error paths otherwise fail before inference. |
| 5.7–5.8 | _oidc_runtime.py:104,141,165,175; _oidc_credentials.py:107 | Supported identity/selection-aware tenant reuse and nonfatal tenant-save failure. **Unknown:** identity/config/credential-change race coverage. |
| 6.1–6.3 | proxy.py:60,121,128 | Supported fixed loopback/default/override and local health. **Unknown:** actual startup and invalid-port behavior. |
| 6.4–6.7 | proxy.py:57,65 | Supported allowlist/custom GET-POST 404 and no local caller authentication. Other method rejection delegated to framework. **Unknown:** executable route/method matrix. |
| 7.1 | proxy.py:82 | Supported method/raw body and query handoff. **Unknown:** repeated/blank/encoded query values across Starlette-to-HTTPX2 mapping; see G5. |
| 7.2 | proxy.py:77; _oidc_runtime.py:114 | Supported generated JSON/auth/tenant headers. Tenant validity depends on G3. |
| 7.3–7.6 | proxy.py:88,100,107 | Supported status, content type, incremental application payload and no-cache. **Unknown:** SSE timing/tool calls, decoded/compressed bodies and bodyless statuses. No-cache applies to relayed responses, not locally generated errors. |
| 7.7 | proxy.py:100 | Partial / **Unknown:** generator finally closes after entry; ownership before entry and cancellation-resistant close need evidence; see G6. |
| 7.8–7.9 | proxy.py:52,90,100 | Supported no replacement JSON after stream start and no redirect following. **Unknown:** actual disconnect/error behavior. |
| 8.1–8.2 | _oidc_config.py:38,81; oidc_marker.py:18; proxy.py:20 | Supported overrides and explicit disable refusal. setdefault preserves operator disable; HTTP error mapping has G2. |
| 8.3 | _oidc_config.py:43,94,98 | Partial / **Constraint:** validation exists, but custom audience may be empty, numeric-loopback HTTP is allowed and some malformed URL parsing raises ValueError. Exact supported policy needs documentation, not an inferred new policy. |
| 8.4 | proxy.py:75,90 | Partial / **Missing:** consistent config/authentication failure mapping; see G2. |
| 8.5–8.7 | proxy.py:90,95,107 | Supported generic handled failures and upstream error relay. **Unknown:** sanitized uncaught/config/framework diagnostics and complete connection-error matrix. |
| 9.1–9.3 | current-state.md; this report | Baseline/date, flow/source references and evidence categories exist. **Constraint:** no baseline commit; upstream-copy provenance is a repository assertion, not verified source equivalence. |
| 9.4–9.6 | README.md:15; current-state.md | Supported client setup, local-auth/liveness/entitlement caveats and shared-slot warning. |
| 9.7 | README.md:19; LICENSE.vibe | Partial / **Missing:** verified upstream diff/deviation ledger. Existing license retained; import adaptation noted. |
| 10.1–10.9 | verification-plan.md; source seams above | **Missing:** executable isolated suite, runner/test dependencies, canonical command, controlled fixtures, requirement traceability and result reporting. A candidate coverage list is not test evidence. |
| 10.10 | verification-plan.md; G1–G6 below | Documentation policy and static discrepancy record exist. **Unknown:** no executed characterization results yet. |
| 11.1 | verification-plan.md | **Missing:** executable separation/default live exclusion because no suite exists. |
| 11.2 | verification-plan.md | Proposed operator-controlled live sequence exists; no live execution. |
| 11.3 | verification-plan.md | Partial / **Missing:** result-record template/version inventory and actual evidence. |
| 11.4–11.6 | verification-plan.md | Documentation policies distinguish unverified/entitlement cases and exclude secrets from artifacts. **Unknown:** enforcement on future result artifacts. |
| 11.7 | verification-plan.md | Partial / **Missing:** separately recorded offline, local-startup and live evidence. No readiness/GO claim is supported. |

Authentication source names in this table are relative to mvibe_auth/. Root script/docs names are relative to the repository or spec directory as appropriate.

## Concrete Discrepancies and Targeted Investigation

### G1: Blank duplicate callback parameters
**Missing behavior, 2.5.** _oidc_callback.py:59 uses parse_qs without retaining blank values, then checks list cardinality. For a matching state, state=correct&state= or code=x&code= can therefore appear single-valued. This is a static discrepancy with duplicate-required-parameter rejection, not a demonstrated account compromise. Add controlled callback cases for nonblank/blank duplicates and missing/empty state/code; design the parser correction around the approved contract.

### G2: Configuration bypasses authentication error mapping
**Missing behavior, 8.4; adjacent 8.2, 8.3, 8.6.** proxy.py:75 constructs OidcConfig before the try beginning at line 76. OidcError for invalid/disabled configuration bypasses the generic 401 and re-login guidance. Malformed URL parsing may also raise ValueError. Decide how configuration validation and request-boundary sanitization meet the proposed contract; exercise configuration versus credential-resolution errors independently.

### G3: Empty tenant identifiers can pass schema validation
**Missing behavior, 5.6 and 7.2.** _oidc_runtime.py:32 declares workspace and organization identifiers as unconstrained strings. A workspace response with empty organization_id or id may be accepted and forwarded. Controlled tests should cover empty identifiers, empty pages, absent selected workspace and malformed payloads. Whitespace normalization/allowed identifier format remains a design clarification, not an invented requirement.

### G4: Stale-write requirement exceeds unconditional login save
**Constraint / requirement review, 3.7.** Refresh save reloads and compares expected token material under an in-process lock; tenant-only updates are preserved. Initial browser login uses unconditional save. Separate processes can change the shared Vibe slot between compare and write, so no cross-process atomic guarantee exists. Reconcile the broad “credential update” wording with the explicit exclusion of concurrent cross-application re-login safety before designing a fix. Do not silently promise cross-process locking or weaken the requirement.

### G5: Multi-value query preservation needs executable evidence
**Unknown, 7.1.** request.query_params is passed directly to HTTPX2 params. Framework/client conversion semantics may collapse duplicate keys; source inspection alone does not establish a defect. Test repeated names, blank values, percent-encoded values and ordinary single-valued queries. Confirm whether requirements preserve value multiplicity, ordering and/or exact wire encoding before finalizing the adapter.

### G6: Stream ownership and cancellation need executable evidence
**Unknown, 7.7–7.8.** Upstream close occurs in the async generator finally, which is active only once iteration begins; awaiting close is not explicitly shielded. Test cancellation during send, after send but before iteration, during iteration and during close, plus iterator failure and response construction failure. Establish response ownership through all transitions. These are candidate leak scenarios, not confirmed runtime leaks.

## Requirement Clarifications for Human Review
These findings inform review; requirements were not edited.
- **3.5 / 4.1–4.4:** Startup reuses a readable identity without checking expiry; request-time resolution refreshes or fails. Confirm whether an unrefreshable stored identity should fail with re-login guidance or automatically launch the browser.
- **3.7:** Bound stale-write protection to the intended update/process scope; resolve G4.
- **4.2–4.3:** Clarify that refresh may retain an already-fresh ID token when no replacement is returned, while stale ID tokens remain unusable. The present wording can imply every refresh must return a new ID token.
- **1.6 / 8.3 / 10.7:** Enumerate the existing supported destination, URL, scope, proxy/NO_PROXY and trust policy in design/operator docs. Empty custom audience and loopback HTTP audience need explicit support decisions; do not replace Vibe defaults with a new authentication model.
- **7.1:** Specify duplicate-query semantics; preserve raw body bytes as already explicit.
- **6.6 / 7.3–7.5:** Define framework method rejection expectations and wire versus application-payload handling for decoded responses. Unsupported routes outside /v1/ are not new endpoints in scope.
- Exact signing algorithms, clock leeway, callback limits and error boundaries should be documented as testable design contracts supporting the approved user-visible requirements.

## Implementation Options

### Option A: Extend Existing Components
Add tests and focused changes directly around proxy.py and the current private authentication helpers.
- Extend callback parsing, tenant validation and proxy error handling where the confirmed discrepancies occur; reuse current storage/refresh/HTTP paths.
- New test fixtures/configuration are still necessary, but production structure remains unchanged.
- Advantages: smallest production diff; preserves CLI/package layout and Vibe integration.
- Costs: tests depend on private names and globals; patching lifespan, threads, time and HTTP construction can become brittle.
- Compatibility: route/auth behavior remains stable except approved fixes; direct source-copy deviations must be tracked.
- Effort: **M (3–7 days)** planning estimate for isolated suite and focused fixes, excluding live-account scheduling. Risk: **Medium** from mutable state, cancellation and untested dependency combinations.

### Option B: Introduce Explicit Verification-Oriented Components
Create an application factory and explicit authentication/store/transport adapters while retaining the Vibe OIDC implementation underneath.
- Own application construction and dependency selection in new components; keep CLI a small entry point. Expose controlled fixture boundaries for auth, storage and transport.
- Advantages: cleaner integration tests and less global patching; separates lifecycles.
- Costs: broader production refactor before a passing baseline; additional interfaces can drift from the copied Vibe implementation.
- Compatibility: retain current CLI, loopback routes, Vibe credential slot and ID-token flow; test import/initialization behavior and public/private boundaries carefully.
- Effort: **L (1–2 weeks)** planning estimate. Risk: **High** until characterization covers the refactor boundary.

### Option C: Hybrid, Characterize Then Add Minimal Seams
First add isolated characterization/contract tests and fake external boundaries. Then repair confirmed discrepancies and introduce only seams made necessary by deterministic integration/cancellation tests.
- Reuse OIDC client/service/store rather than replacing them. New work owns test harness, fixtures, traceability and evidence; focused existing-file changes own behavior corrections.
- Advantages: incremental evidence, small reviewable fixes, reduced up-front refactor risk.
- Costs: requires discipline to separate known-bug characterization from desired-contract tests and avoid leaving an inconsistent mix of patch points/adapters.
- Compatibility and recovery: keep CLI/routes/auth behavior fixed; separate harness, corrections and refactors into reviewable changes so a correction/refactor can be reverted independently. No deployment or feature-flag system is needed for this local tool.
- Effort: **M (3–7 days)** planning estimate, with possible L scope if cancellation/import behavior requires refactoring. Risk: **Medium** for offline work; live compatibility remains **High uncertainty** until separately exercised.

These are comparative estimates, not delivery commitments. Comprehensive validation of copied authentication code is more complex than CRUD because it combines browser callbacks, JWT verification, thread/async boundaries, shared persistence and streaming.

## Design Recommendations and Research Needed
Prefer **Option C for design evaluation**, not a final implementation decision. It retains the required Vibe flow while creating evidence before structural changes.

1. Resolve G4 and the requirement clarifications before treating the specification as implementation-ready.
2. Specify a canonical reproducible development/test environment, compatible Python version, test command and exact test dependencies. Investigate Authlib's HTTPX integration alongside HTTPX2, joserfc/CodeIDToken validation, and FastAPI/Starlette streaming semantics using primary documentation and controlled offline execution.
3. Design a default-deny offline harness: intercept real keyring/browser/network before application startup; provide synthetic success and failure responses; isolate environment, credential/tenant/TLS caches and ongoing worker tasks.
4. Map fixtures/checks to all 86 acceptance criteria. Separate code-supported contracts, confirmed discrepancy regressions, documentation checks and tests requiring future external evidence.
5. Establish response ownership/cancellation, multi-value query adaptation, URL error normalization, token refresh boundaries and tenant identifier validation with targeted cases before broad coverage.
6. Keep UserInfo access-token credentials separate from inference/tenant ID-token headers. An API-key replacement is outside every option.
7. Record complete source hashes for authentication-copy updates, an upstream provenance/deviation ledger and a sanitized evidence template. Account identifiers, tokens and callback material must not enter committed fixtures or reports.
8. Defer live issuer registration, refresh ID-token issuance, keyring backend compatibility, subscription/model entitlement, workspace schema and client SSE/tool-call interoperability to the explicit live verification procedure. Offline mocks cannot prove them.
9. Establish local startup evidence independently of unit/integration results. A startup test must substitute the real login/store/upstream boundaries; production startup remains unverified until deliberately exercised.

## Overall Assessment and Handoff
- Existing code provides most functional paths; there is no basis for claiming they pass tests.
- Confirmed implementation discrepancies: G1, G2 and G3. G4 is a scope/guarantee mismatch requiring requirement review. G5/G6 remain targeted execution questions.
- Largest missing capability: executable isolated verification, reproducible commands and traceable evidence. Documentation is a useful baseline but does not close that gap.
- Overall offline implementation planning estimate: **M**, **Medium risk**, with upward pressure from race/cancellation fixes. Live integration risk cannot be calibrated from static inspection.
- Requirements approval remains pending. No design/tasks generated, runtime code changed or tests executed in this gap-analysis step.
- Next: review this analysis and requirement clarifications, then run kiro-spec-design vibe-oidc-proxy after requirements approval (or intentionally use -y to fast-track approval).

## Verification of This Analysis
Claim type: TASK. Claim: static gap analysis was recorded with requirement coverage, options, risk and research items. Runtime correctness, test success and feature readiness are explicitly excluded. File readback, criterion-reference coverage and unchanged source-baseline checks are performed when saving this report.

## Source Baseline (SHA256)

| File | SHA256 |
| --- | --- |
| proxy.py | 8D97854E93E4DB1A422FF65CCAC58FBFDBA663EDC5126B8BEB35A05B073E0F54 |
| README.md | 01AE00064472D9BFEC044133DA13977A89092902F07C5F1940F4F96F473BCDC1 |
| mvibe_auth/__init__.py | DCC9DE903F85E0B1DE2E2431E1D0E4BB1674FA06C24ED504EBF6363B602115FC |
| mvibe_auth/_credentials_port.py | CBCA7ACDC66D91C7448A37A81F0F8A02C68ACCF5DECCFC24C3799239CBFE11A7 |
| mvibe_auth/_http.py | CB49FB19FD86165F653634D438B89B7B71E8EE8E43145FF342D8E6BCA5FA61F4 |
| mvibe_auth/_oidc_callback.py | 2A6B54AA88CD8AEFC8BE4CC7BBB36E3773517D944D266D43CFDD58641B36F9A3 |
| mvibe_auth/_oidc_client.py | 976BD9C3FDC74DB0E1687ADBF7E4EFA586CEFACA52A2D8542E2A35CE8E95C277 |
| mvibe_auth/_oidc_config.py | 4B4E87BACBF3D8B11B78426F51E2285D75C874EA5AF8E7588CD5F41196A6F7EF |
| mvibe_auth/_oidc_credentials.py | 451ED14867D0BD52638B1566179A3A5F45E12CDD38F0E21D098AC60A478C2E3A |
| mvibe_auth/_oidc_platform_credentials.py | F64F5813520B98792C3C479CD8932200AAC42812BECCE68BE8FADB5C016C6F24 |
| mvibe_auth/_oidc_runtime.py | 246A972E749192B91DEAAD10D55E99D62A058AFAEA1FFF3A8A8F567045D3AEF0 |
| mvibe_auth/_oidc_service.py | 9C8D4E8DAFF19DD97F52E28AF8AF42F53181E4DF696F76482AF51ADC39673253 |
| mvibe_auth/concurrency.py | FAE2F4CBAA7F571196616FF4AA328AB331035C1285D4C4FBED70B9A436DDAB79 |
| mvibe_auth/http.py | C1C072A977464B235412F8B3338285222CD5A8343BDDD219E02946CDFEE30BEB |
| mvibe_auth/oidc_marker.py | 711A2FDF30008221031D7100995847F331F0A66FEADF73154E6374CBC9BA6EBA |

---

## Design Entry Review — 2026-10-31
Requirements approval was explicitly authorized by `kiro-spec-design vibe-oidc-proxy -y` and recorded in spec.json. Design remains ungenerated.

Consulted design-principles, design-discovery-light, design-synthesis, design-review-gate and design/research templates. The review gate requires clarification rather than inventing a contract when requirements leave a material scope ambiguity.

### Credential replacement decision required
Requirement 3.7 applies stale-write protection to every credential update, including browser login. Current login uses unconditional save, while refresh uses conditional save. The boundary excludes guarantees for concurrent re-login across applications, so cross-process atomic protection is not proposed.

Concrete proposed interpretation for review: within the proxy, capture the stored credential snapshot when browser sign-in begins; if that snapshot has changed before saving, fail that sign-in with retry guidance and preserve the newer credentials. Apply the same observed-change protection to refresh. Retain the documented limitation for another application's write occurring between comparison and persistence.

Alternative: explicit browser sign-in replaces the stored login even when it changed during the attempt; constrain requirement 3.7 to background refresh updates. This changes the requirement and must not be selected silently.

This determines both user-visible sign-in behavior and persistence contracts. No final design is written until the choice is resolved. No runtime code, authentication or tests were executed.


---

## Design Discovery and Synthesis — 2026-10-31

### Summary
Discovery classified as complex integration: shared Python authentication, standalone proxy and TypeScript OpenCode provider. User clarified credential cycling, then explicitly requested a reusable library shared with an OpenCode provider plugin for Vibe OIDC. These directions supersede the prior design-entry question. Requirements 3.9 and 12.1–12.10 record the added behavior; earlier gap findings remain historical.

### Research Log
- Existing source: Authlib-based login and validation, conditional refresh writes, unconditional login write, cached ID-token resolution and access-token-only UserInfo helper were reviewed. Keep inference and UserInfo credentials distinct.
- Published package registry: https://registry.npmjs.org/@opencode-ai%2fplugin/latest reported 1.19.5. The exact declaration artifact https://unpkg.com/@opencode-ai/plugin@1.19.5/dist/index.d.ts was read. It exposes PluginModule.server, auth OAuth authorize/auto callback, loader, provider.models and dispose. The OAuth success union permits a key/metadata variant, allowing a non-secret connection marker rather than copied refresh credentials. This proves a type-level seam, not actual host interoperability. GitHub raw/source URLs returned 404; the package artifact is the design source.
- OpenCode documentation https://dev.opencode.ai/docs/plugins/ confirms JavaScript/TypeScript hooks and local/npm plugin loading. Scope targets the inspected 1.19.5 API; v2 integrations are not promised.
- Authlib https://docs.authlib.org/en/stable/oauth2/client/http/index.html documents AsyncOAuth2Client and PKCE verifier use. Retain its existing implementation; no hand-written OAuth replacement.
- HTTPX https://www.python-httpx.org/async/ documents manual-stream close responsibility and decoded versus raw iteration. Treat HTTPX2 equivalence as an implementation compatibility check, not proof from HTTPX docs.
- AnyIO https://anyio.readthedocs.io/en/stable/cancellation.html explains repeated cancellation and shielded finalization. Stream ownership therefore surrounds the full ASGI invocation and closes with bounded protection.
- filelock https://py-filelock.readthedocs.io/en/latest/ provides participating-process synchronization. It cannot make installed Vibe cooperate; external-slot comparison remains best effort.
- pytest https://docs.pytest.org/en/stable/how-to/monkeypatch.html supports controlled replacement of dependencies/environment; uv https://docs.astral.sh/uv/concepts/projects/sync/ documents locked environment operation. Test execution itself remains future work.

### Architecture Pattern Evaluation
Retain the hybrid choice: a public facade, minimal auth corrections and thin consumer adapters. A full TypeScript auth port duplicates validation/rotation; replacing the Python proxy expands migration risk. A common Python core plus private stdio adapter preserves one source of truth at the cost of a Python prerequisite for OpenCode.

### Design Decisions and Synthesis
- Generalization: proxy and provider need the same request-scoped inference snapshot. AuthSession owns that capability, not a provider-specific token cache.
- Credential cycling: reload authoritative storage on every resolution; compare before persistence; on observed conflict discard stale work and re-resolve once. Login returns reused-current distinctly. No implicit browser retry or inference replay.
- Store contract: missing and unreadable are different; expected-absent is valid conditional-write input. Participating consumers coordinate with a per-user lock. Installed Vibe's uncoordinated writes remain outside atomic guarantees.
- Build versus adopt: retain Authlib/joserfc/keyring; adopt filelock and pytest instead of custom locking/test frameworks. Build only the typed facade and small protocol/provider adapters, since they encode this application's Vibe-specific integration.
- Simplification: no service daemon, public auth HTTP endpoint, second token store or JS OIDC implementation. The helper is owned by the plugin and carries only short-lived inference headers.
- Scope update: provider is an explicit user-requested consumer of this shared auth work. Requirements remain approved per prior -y and subsequent explicit direction; design approval remains false.

### Risks and Validation
Exact runtime dependency resolution, provider option propagation, model metadata mapping, helper cancellation and two-process rotation require implementation tests before acceptance. SDK declarations do not prove that a host invokes hooks correctly. Python availability is documented as a plugin prerequisite. No live account, keyring, import compatibility or inference checks were performed during design.

### Consulted Skills and Review
Applied kiro-spec-design, design-principles, full/light discovery, synthesis and design-review-gate; reused kiro-verify-completion for artifact verification. Independent codebase research checked library/adapter seams. Main-context synthesis and inline design review cover boundary, files, public contracts, error/lifecycle ownership and requirements traceability. Design is finalized only after mechanical validation of all criterion mappings.


