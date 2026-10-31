# Implementation Plan

Each executable sub-task is estimated at 1–3 hours. Foundation, core, integration and validation are ordered. Non-parallel tasks follow prior work; (P) tasks may run with preceding peers only after their listed dependencies and the foundation are complete. All checks are mandatory. Scope is implementation and offline verification; live-account execution and publication are excluded.

File ownership follows design.md. Boundaries name components rather than repeat paths. Provider tasks sharing adapter files are sequential. The requested technical documentation and live procedure are required acceptance artifacts attached to setup/integration/testing tasks; they are not dropped under the generic code-only planning rule.

- [x] 1. Foundation and isolated verification
- [x] 1.1 Prepare the installable Python environment
  - Package the shared library independently of optional proxy dependencies; preserve the script entry point and Vibe license attribution.
  - Resolve and lock the declared runtime, native locking and Python test dependencies; record supported interpreter and separate online setup from offline runs.
  - Done when a clean prepared environment imports the library without credential/browser/network side effects and the locked offline test runner executes a synthetic smoke check.
  - _Boundary: Packaging_
  - _Requirements: 12.1, 12.2, 10.9_

- [x] 1.2 Prepare the provider build and host test environment
  - Create strict TypeScript build/test configuration, package exports and locked dependencies for the current OpenCode 2 SDK and a compatible OpenAI adapter.
  - Provision the pinned OpenCode host in an isolated test profile as an explicit setup prerequisite; validate SDK hook signatures without a real account.
  - Done when typecheck and a synthetic plugin-load smoke succeed, or incompatibility is reported before dependent provider work begins.
  - _Boundary: Provider packaging_
  - _Requirements: 12.3, 12.8, 12.10_

- [x] 1.3 Establish default-deny fixtures and state isolation
  - Block real keyring, browser and remote network before application imports in Python and provider tests; provide controlled clock, store, callback and HTTP responses.
  - Isolate environment, credential/tenant/TLS caches and worker lifecycles; create a temporary cross-process synthetic store and lock area.
  - Done when deliberate attempts to access real external effects fail tests and repeated fixture use leaves no shared identity or running worker behind.
  - _Boundary: Verification Suite_
  - _Depends: 1.1, 1.2_
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 11.1, 12.10_

- [x] 1.4 Establish cross-language wire-contract fixtures
  - Create the canonical versioned request/response schema and synthetic fixture vectors for all documented methods, error reasons and result variants.
  - Create Python and strict TypeScript validation tests, including absent versus unreadable storage outcomes and secret-field rejection.
  - Done when both runtimes accept the same valid vectors and reject unknown versions, malformed envelopes, oversized frames and credential-bearing extra fields.
  - _Boundary: Stdio protocol_
  - _Depends: 1.3_
  - _Requirements: 12.2, 12.8, 12.10_

- [x] 2. Immutable authentication contracts
- [x] 2.1 Define primitive configuration and public result contracts
  - Place immutable configuration below service/facade and define how it replaces or maps existing OIDC configuration; keep feature-enabled state and tenant selection in that snapshot.
  - Define typed login outcomes, safe failures and inference snapshots with secret-free representations; reject malformed URLs and empty inference audience.
  - Done when config/result tests cover defaults, overrides, disablement, invalid values and distinguish inference ID-token snapshots from UserInfo access-token credentials.
  - _Boundary: AuthSession contracts_
  - _Depends: 1.3_
  - _Requirements: 8.1, 8.2, 8.3, 12.1, 12.2_

- [x] 2.2 Integrate captured configuration throughout authentication
  - Explicit integration: migrate service, runtime, marker checks and transport resolution to session-owned configuration rather than repeated environment reads.
  - Preserve existing destination/TLS/proxy behavior and remove upward runtime imports through resolver injection; maintain side-effect-free package imports.
  - Done when an existing session ignores later environment changes, a new session observes them, and dependency/import tests demonstrate the intended layering.
  - _Boundary: Authentication configuration integration_
  - _Depends: 2.1_
  - _Requirements: 8.1, 8.2, 8.3, 12.1, 12.2_

- [x] 3. Authentication primitives
- [x] 3.1 Correct callback validation and cleanup
  - Reject blank and nonblank duplicate required parameters while retaining valid matching denial handling.
  - Cover request limits, malformed requests, callback timeout and cancellation using controlled readers/writers.
  - Done when valid callbacks resolve exactly once and every rejected/cancelled attempt closes its listener and pending activity.
  - _Boundary: OIDC callback_
  - _Depends: 2.2_
  - _Requirements: 2.5, 2.6, 2.7, 2.8, 10.6_

- [x] 3.2 Verify signed-token and discovery contracts
  - Exercise discovery issuer/endpoint checks and PKCE/state/nonce using synthetic issuer responses.
  - Generate synthetic signed tokens covering supported algorithms, claims, lifetime boundaries, changed subject and omitted replacement tokens; preserve library validation rather than copying it.
  - Done when positive exchange/refresh cases pass and invalid signatures, claims, token types and lifetimes fail with safe outcomes.
  - _Boundary: OIDC client_
  - _Requirements: 2.4, 3.1, 3.2, 3.3, 4.5, 4.6, 10.6_

- [x] 3.3 (P) Stabilize credential transport policy
  - Cover destination checks, marker resolution, proxy/NO_PROXY and configured trust using injected transports; no actual remote traffic.
  - Verify UserInfo uses access tokens, inference uses its separate snapshot and redirects never receive forwarded credentials.
  - Done when transport tests cover both direct/injected paths and disallowed destinations fail before credential use; ownership excludes callback/client test files.
  - _Boundary: HTTP transport_
  - _Depends: 2.2_
  - _Requirements: 1.5, 1.6, 7.9, 10.7_

- [x] 4. Credential persistence and cycling
- [x] 4.1 Implement conditional writes for all credential mutations
  - Distinguish missing slot from unreadable storage and support expected-absent as well as expected-present writes.
  - Preserve concurrent tenant-only changes while comparing token material and keep existing serialization, keyring slot and sanitized representations.
  - Done when synthetic store tests prove stale login/refresh writes cannot replace an observed newer set, storage failure is explicit and token material never appears in display output.
  - _Boundary: Credential Store_
  - _Depends: 3.1, 3.2, 3.3_
  - _Requirements: 3.4, 3.6, 3.7, 3.8, 5.8_

- [x] 4.2 Integrate cross-process mutation coordination
  - Implement per-user native lock coordination with coordinator-before-slot ordering, bounded acquisition and no lock held during browser interaction.
  - Explicit integration: route tenant whole-bundle writes, login commits, refresh commits and clears through the same mutation boundary across store, service and tenant resolver; retain best-effort limitations for external Vibe.
  - Done when two synthetic consumer processes cannot overwrite each other's rotation with a stale tenant write and cancellation does not release a lock before its worker finishes.
  - _Boundary: Coordination, Credential Store, OIDC service and Tenant resolver integration_
  - _Depends: 4.1_
  - _Requirements: 3.7, 3.9, 4.7, 5.7_

- [x] 4.3 Implement bounded refresh and conflict cycling
  - Reload authoritative storage for each resolution, apply access/ID-token freshness thresholds and allow one conflict retry.
  - On invalid grant reload and retry only changed token material; retain a fresh prior ID token when allowed and reject an unchanged stale token.
  - Done when controlled tests cover exact expiry thresholds, rotation, competing updates, exhausted budget and re-login guidance without unbounded retries.
  - _Boundary: OIDC service_
  - _Depends: 4.2_
  - _Requirements: 3.9, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

- [x] 4.4 Protect login commits and expose outcomes
  - Capture an authoritative snapshot before browser authorization and conditionally commit only after validation.
  - On changed storage discard the candidate and resolve the current identity within the budget; distinguish completed from reused-current and never silently claim the browser-selected account was saved.
  - Done when login conflict, denial, persistence failure and cancellation tests preserve newer credentials and expose a single terminal outcome.
  - _Boundary: OIDC service_
  - _Requirements: 2.4, 2.7, 2.8, 2.9, 3.4, 3.6, 3.7, 3.9_

- [x] 4.5 Bind tenant selection to the resolved identity
  - Implement explicit organization/workspace overrides, first/exact selection, pagination and rejection of empty/whitespace identifiers.
  - Bind cached/discovered tenant to current identity and selection; recheck token revision before return and restart on conflict within the same budget.
  - Done when selection/error cases pass and tenant persistence failure permits a valid request without enabling stale-token or cross-identity tenant reuse.
  - _Boundary: Tenant resolver_
  - _Depends: 4.2, 4.3_
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

- [x] 5. Public shared-library integration
- [x] 5.1 Expose the reusable authentication session
  - Explicit integration: assemble config, service, store, coordinator and tenant resolver behind the documented public session interface.
  - Keep token bundles and refresh tokens private; status/resolve never launch a browser and public exports do not import the proxy.
  - Done when consumer tests obtain immutable target-bound snapshots, reuse stored identity and receive typed sanitized failures using only public interfaces.
  - _Boundary: AuthSession integration_
  - _Depends: 4.4, 4.5_
  - _Requirements: 1.1, 1.2, 1.4, 3.5, 8.6, 12.1, 12.2_

- [x] 5.2 Verify session cancellation and consumer parity
  - Exercise session close during waiting, refresh and persistence with controlled worker completion and repeated cancellation.
  - Verify two consumer instances use identical rotation/tenant policy and document cooperative versus external-writer guarantees alongside tested API examples.
  - Done when closure drains owned work, no token material appears in captured diagnostics, and parity/race tests pass without real keyring access.
  - _Boundary: Shared-library verification_
  - _Depends: 5.1_
  - _Requirements: 2.8, 3.8, 3.9, 10.3, 10.4, 12.1, 12.10_

- [x] 6. Proxy and bridge adapters
- [x] 6.1 Migrate proxy startup and routes to the public library
  - Explicit integration: inject public session/client factories, preserve CLI browser presentation and loopback settings, and replace private auth imports.
  - Move configuration and authentication failures inside the safe request boundary; retain supported operation allowlist and generic errors.
  - Done when isolated startup/route tests cover missing/matching identity, forced sign-in, health, methods, caller-credential exclusion and 401/502 behavior.
  - _Boundary: Proxy integration_
  - _Depends: 5.2_
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.9, 3.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 8.4, 8.5, 8.6_

- [x] 6.2 Implement owned streaming and request preservation
  - Explicit integration: acquire upstream inside response ownership, preserve repeated/blank query pairs and raw body, and forward decoded response content/status/type incrementally.
  - Close under bounded protection; on shared-client retirement reject subsequent forwarding with sanitized 502 until restart and terminate affected siblings without synthetic success content.
  - Done when tests prove first-chunk delivery before upstream completion, cleanup before/during iteration, bodyless/error statuses, no replay/redirect and documented retirement behavior.
  - _Boundary: Proxy and Stream Owner integration_
  - _Requirements: 4.8, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 8.7_

- [x] 6.3 (P) Implement the private Python protocol dispatcher
  - Expose handshake, status, resolve and login lifecycle over private stdio with validated IDs, bounded frames and typed errors.
  - Allow waiting login operations without blocking cancellation/shutdown; enforce the active-request cap and keep stdout protocol-only.
  - Done when synthetic bridge tests pass for framing, concurrency, wrong versions, safe errors and all success payloads without changes to proxy-owned files.
  - _Boundary: Stdio Bridge_
  - _Depends: 1.4, 5.2_
  - _Requirements: 12.1, 12.2, 12.8, 12.10_

- [x] 6.4 Implement bridge shutdown and restart safety
  - Handle parent EOF and shutdown by stopping admission, cancelling pending activity and draining persistence workers before acknowledging graceful closure.
  - Treat forced host termination as unknown commit outcome; require authoritative reload on the next helper session.
  - Done when process-level fake-store tests cover graceful closure, abrupt death and restart without assuming the last write succeeded or failed.
  - _Boundary: Stdio Bridge_
  - _Depends: 6.3_
  - _Requirements: 2.8, 3.9, 12.9, 12.10_

- [ ] 7. OpenCode provider adapter
- [x] 7.1 Implement the managed helper transport
  - Spawn the installed helper with argument arrays and hidden Windows window; validate handshake and correlate bounded protocol responses.
  - Implement deadlines, child-exit errors and disposal; resolve timeout/cancellation retires the helper, rejects all its pending requests, and never restarts browser authorization automatically.
  - Done when synthetic child tests prove owned-process cleanup, actionable setup failures and authoritative reload on lazy restart; preserve graceful drain before five-second forced termination.
  - _Boundary: Provider bridge client_
  - _Depends: 1.2, 1.4, 6.4_
  - _Requirements: 12.2, 12.8, 12.9, 12.10_

- [x] 7.2 Register Vibe browser authentication in OpenCode
  - Explicit integration: register the V2 provider and command-based Vibe browser OIDC method; add the Python login command using the public session and non-secret stdout marker. Verify cancellation, reused-current and failure outcomes through the actual host contracts.
  - Persist only the non-secret connection marker and configuration metadata; V2 stores the command marker in a key-shaped record, which is never upstream authentication. No API-key option or refresh-token copy.
  - Done when host-hook tests complete success/failure/reused-current paths and verify persisted state contains no credential material.
  - _Boundary: Provider integration and Python login-command integration_
  - _Requirements: 12.3, 12.7, 12.8_

- [ ] 7.3 Resolve credentials for each inference request
  - Resolve a current snapshot for each request, enforce audience policy, remove caller auth and attach only approved credential/tenant headers.
  - Preserve streaming and tool-call payloads, propagate AbortSignal and prohibit redirects/replay; handle helper retirement as typed request failure.
  - Done when consecutive requests observe changed credentials and tests prove cancellation, passthrough and no token persistence or request replay.
  - _Boundary: Provider fetch adapter_
  - _Requirements: 1.3, 1.6, 4.8, 12.4, 12.6, 12.7_

- [ ] 7.4 Expose authenticated model discovery
  - Fetch models through the same per-request auth adapter and map to pinned SDK model types.
  - Use verified upstream or explicit operator metadata for required capabilities/limits; report missing metadata and omit incomplete models.
  - Done when synthetic model cases cover available, empty, unauthorized and incomplete results without invented entitlement, limits or pricing.
  - _Boundary: Provider models_
  - _Requirements: 12.5, 12.8_

- [ ] 8. Integrated validation and reproducible operation
- [ ] 8.1 Verify public startup and provider-host integration
  - Explicit integration: start the proxy with injected boundaries and load the plugin in the isolated pinned host profile with synthetic provider/helper responses.
  - Verify real host propagation of custom fetch, auth marker, model discovery, first streaming chunk and cancellation.
  - Done when local-startup and host compatibility results are separately recorded with no real account activity; failures remain blocking rather than skipped.
  - _Boundary: Consumer integration verification_
  - _Depends: 6.2, 6.4, 7.4_
  - _Requirements: 6.1, 6.3, 10.5, 12.3, 12.4, 12.6, 12.10_

- [ ] 8.2 Make acceptance coverage executable
  - Map every criterion to named executable checks or required documentary evidence; reject unknown IDs and empty evidence mappings.
  - Implement the offline orchestrator for Python tests, provider tests and typecheck using prepared locked dependencies.
  - Done when failed mandatory checks and missing coverage cause nonzero exit, and the report distinguishes pass/fail/skip without treating collected tests as successful execution.
  - _Boundary: Verification Suite_
  - _Requirements: 10.5, 10.6, 10.7, 10.8, 10.9, 10.10_

- [ ] 8.3 Deliver tested installation and operator contracts
  - Explicit integration: verify library-only installation and proxy/plugin setup in isolated environments, including script/package parity, Python prerequisite and version mismatch errors.
  - Ship the requested library/provider guides, README examples and provenance/deviation record as acceptance outputs of those checks; include source hashes, internal flow references and credential/liveness/entitlement limitations.
  - Done when setup examples and public-interface examples are checked offline and every required artifact reflects the tested code rather than unverified claims.
  - _Boundary: Operational acceptance integration_
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 12.2, 12.8_

- [ ] 8.4 Prepare separate live-verification evidence tooling
  - Create and validate the explicit live procedure and structured result template for browser login, reuse, tenant, models, chat, streaming, tools, embeddings and refresh where available.
  - Provide synthetic evidence checks for version/config fields, secret exclusion and distinct unverified/entitlement outcomes; keep default verification unable to launch live checks.
  - Done when sanitized fixture reports distinguish offline, startup and live evidence, and untouched live scenarios remain unverified; this task performs no live login or inference.
  - _Boundary: Verification tooling_
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

- [ ] 8.5 Run final integrated offline acceptance
  - Explicit integration: run the prepared locked offline pipeline, cross-process rotation/tenant cases, helper lifecycle and pinned-host smoke together.
  - Confirm all required artifacts, task coverage and regression cases match the final code; record dependency/runtime versions and unresolved external assumptions.
  - Done when all mandatory offline checks and local-startup evidence pass with no live activity, or completion remains blocked with exact failures; live compatibility is reported separately.
  - _Boundary: End-to-end verification integration_
  - _Requirements: 10.1, 10.8, 10.9, 11.7, 12.1, 12.10_



## Implementation Notes
- 1.1: Python checks: uv run --locked --offline pytest; build: uv build --offline --no-build-isolation. Propagate native failures with exit $LASTEXITCODE in PowerShell. Windows asyncio socketpair must be prepared before blocking socket connections in isolated import checks. Distribution version is independent of Vibe transport provenance.


- User correction: target auto-updating OpenCode 2; lock exact test pairs and check current stable through a separate compatibility action. V1 hooks are superseded by V2 domain registrations. Task 7.2 owns command-based OIDC login and marker integration; task 1.2 verifies V2 setup and task 8.1 verifies the real host.


- 1.2: Verified OpenCode2.2.5/SDK2.2.5; user-reported2.2.6 unverified. OpenAI-compatible2.0.88 uses providerV3; adapter3 uses incompatibleV4. Bun tests/typecheck/build and test:host pass. models --standalone loads fixture but does not invoke cleanup; unit disposal test only, full lifecycle remains8.1.


- 1.3: Test guards install before imports; DNS callback/promise Resolver chains and UDP sends require explicit guards. Worker cleanup must be registered. Host smoke needs absolute OPENCODE_TEST_BINARY because child cwd is temporary. Python10/Bun10 pass after independent remediation review.


- 1.4: Protocol frame cap includes terminal LF. _bridge_protocol.py and src/protocol.ts consume shared vectors; preserve safe ProtocolError when normalizing JSON decoder ValueError/RecursionError. Python389/Bun388 pass. Canonical schema emitted with TypeScript build.


- 2.1: Primitive AuthConfig in _auth_config.py, frozen public outcomes in _auth_contracts.py re-exported by api.py; facade orchestration remains5.1. Legacy env readers remain until2.2. Python432pass and independent APPROVED.


- 2.2: OidcService/OidcRuntime/transport own captured AuthConfig; transport injects oidc_resolver and no longer imports runtime. Task6.1 must pass owner.headers and owner.config; unconfigured markers failclosed. Python446pass, independent APPROVED.


- 3.1: Callback retains blank query values before cardinality checks and bounds writer shutdown; synthetic listener tests cover timeout/cancellation with existing service300seconddeadline simulated. Python486pass, independentAPPROVED.


- 3.2: Generated signed JWT/real Authlib offline checks found nonfinite date claims accepted by dependencies; finite-date supplement runs after existing claims.validate. Python551pass; independentAPPROVED. No service/storepolicychanged.


- 3.3: Characterization-only49transporttests verify real policy with controlledboundaries; no productionchanges or behavioralflag. Python600pass and independentAPPROVED.


- 4.1: StoreABSENT sentinel distinctunreadable; conditional save/clear/tenant compare all tokenrevisionfields excludingtenant. Compatibilitysave/clear capture snapshot beforeworker;4.4must supply prebrowser snapshot. Shieldedworkersdrain repeatedcancel. Python627pass independentAPPROVED.


- 4.2: Native coordinator in _coordination.py; activeowner leases and explicit worker delegation prevent childtask/stalecontext bypass. Storeworkersmustdrainbefore leaving scope. Allbundlewrites incltenantcoordinatebeforeslot; no browserwaitlock. Python642pass independentAPPROVED afterownershiprepair.


- 4.3: ResolutionBudget shared conflict retry permits2passes total; invalidgrant never retries unchangedrejectedrefresh. access_tokens defaultsaccessonly forUserInfo; runtimeonecall require_id_tokenTrue. Minimalruntimeintegration avoidsdoublebudget. Tenant4.5mustreusebudget. Python673pass independentAPPROVED.


- 4.4: Service snapshotsstore beforediscovery/browser; explicitconditionalcommitexpectedpresent/ABSENT. Changedstore discardsbrowsercandidate spends sharedbudget and returnsreused_current onlyfreshusable. waitreturnsstabletypedoutcome. Python689pass independentAPPROVED.

- Python runtime rule: use uv/uvx and uv-managed environments exclusively. Never invoke or install Windows Store Python; avoid bare python/py aliases. Synthetic subprocesses use the running uv-managed sys.executable.


- 4.5: Runtime _resolve_headers sharesResolutionBudget with service and checks authoritative tokenrevision/freshness beforeallreturnpaths incloverride/cache. Tenantpersistfailureonlyproceedsvalidcurrent; invalidIDsrejected. Python718pass independentAPPROVED.


- 5.1: PublicAuthSession in api.py delegates existingservice/runtime; runtime.material returns same-resolutionmetadata; serviceattempt.ready signalslisteningURL/error. Snapshotrevision opaqueHMAC; refreshprivate. Python737pass independentAPPROVED.


- 5.2: Publiclifecycle/parity13cases characterizecorrectbehavior no productchanges; completednativeprobe verifies persistencecancel retainslock. docs/auth-library.md publicexamples executeoffline. Python750pass independentAPPROVED.


- 6.1: ProxypublicAuthSession/appfactory migration; adapterOIDCdefaultwhenenvabsent noenvmutation explicitdisablehonored. Devproxydepskeepcanonicalofflinepytestcomplete; runtime no-devlibindependent. Streamowner/queryprecision6.2. Python781pass independentAPPROVED.


- 6.2: ProxyStreamOwner acquiresinsideASGIcall, boundsHTTPXautoiteratorclose+explicitclose; failedcleanupretiresclientuntilrestart and abortssiblings next502. OrderedquerypairsencodedwithoutHTTPXgrouping. Helperpackaged. Python806pass independentAPPROVED.


- 6.3: PublicsessionNDJSONbridge 31ordinary+1cancel slot; malformedduplicateactiveIDs capturesstatebeforeawait, avoidssecondstartedreply andretires. Stdioincidentaloutputdiscarded originalbinarypipeprotocolonly. Python837pass independentAPPROVED aftercorrelationrepair.


- 6.4: Actualmoduleprocess10lifecyclecases confirm gracefulworkerdrain/nativeexclusion and forcedkillbefore/aftercommit authoritative restart. Windowsuvvenvexe mayberedirectorparent;7.1mustterminateownedruntime/tree notlauncheronly. Synthetic harnessrecordsos.getpid. Python847pass independentAPPROVED no orphanworkers.


- 7.1: Explicitinstalledhelperargv uv offline/no-sync orabsoluteuvmanagedruntime. Windows10+ nativeJobObject inwindows-job.ts preservesdescendantownershipafterrootexit; verifyActiveProcesses0 beforereplacement. No dependenciesinstalledfromrequests. Bun411pass +actualprocessharnesses independentAPPROVED afterdescendantrepair.


- 7.2: V2commandregistration+PythonpublicloginCLI emitsfixedmarkerafterclose/usableoutcome only. Realhost2.2.5 storesmarkerKeyshapeonly; commandcancelremovesattempt and actualPythonPIDexits. Fresh11Python/5Bun+hostsmokePASS; independentfull858Python416Bun/buildsPASS.

