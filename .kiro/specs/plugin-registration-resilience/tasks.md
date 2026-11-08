# Implementation Plan

- [ ] 1. Bounded model-catalog attempts with classified diagnostics
- [x] 1.1 Add per-attempt deadline to the model catalog: optional deadline (default 30s) and injectable timer; arm at attempt start, clear on settle/supersede/close; on expiry mark the attempt's generation stale, report a timeout diagnostic naming the deadline, keep the last-good model list, and discard late results; the deadline must never abort the shared helper session (only close() aborts).
  - Unit test with a never-settling discovery fetch and a fake timer: timeout diagnostic reported, last-good list retained, late result discarded when it settles, timer cleared, bridge abort never invoked.
  - Unit test: a follow-up refresh() after a timed-out or failed attempt starts a new attempt (failures are not cached).
  - _Boundary: ModelCatalog_
  - _Requirements: 2.1, 2.2, 4.3_
- [x] 1.2 Classify discovery failures with cause text: prefix existing diagnostics with a failure class (unreachable, unauthorized, invalid) and append the underlying error message; keep messages single-line, secret-free (no tokens, headers, prompts, bodies).
  - Unit tests assert the classified message content per failure mode; existing isolation/diagnostics tests remain green.
  - Unit test: auth-failure attempts (helper reports not signed in, or helper unavailable) keep the provider registered and report the existing sign-in/unavailable diagnostics.
  - _Boundary: ModelCatalog, Discovery classification_
  - _Requirements: 3.2, 3.3, 3.4, 4.1_
- [x] 1.3 Report a pending-models diagnostic before the first successful publish: exactly once per catalog instance while the snapshot is empty, stating that the provider is registered and discovery is pending.
  - Unit test: after construction (no refresh yet) and after a failed attempt, the pending diagnostic is present and not duplicated.
  - _Boundary: ModelCatalog_
  - _Requirements: 1.3, 1.4_
- [ ] 2. Non-blocking provider registration with step-labeled failures
- [x] 2.1 (P) Start the first model-catalog refresh without awaiting it; plugin setup resolves after the synchronous registration transforms only, with a defensive catch that reports unexpected refresh-loop rejections; no helper process spawn and no network on the registration path.
  - Setup unit test: setup resolves while the refresh promise is gated; bridge-spawn spy records zero spawns.
  - _Boundary: Setup orchestration_
  - _Requirements: 1.1, 1.2, 1.4, 4.1_
- [x] 2.2 Label each registration phase (request-context, provider-transform, integration-transform, aisdk-hook); on failure throw an error whose message names the failing phase and attaches the original error as cause, replacing the current generic-only setup message.
  - Setup unit tests with failing phase stubs assert the phase name in the message and the cause identity.
  - _Boundary: Setup orchestration_
  - _Requirements: 3.1, 3.3, 5.2_
- [ ] 3. Real-host integration evidence
- [x] 3. Extend the host-integration harness: gate the discovery fetch; assert the provider is queryable and setup evidence is recorded before the gate releases; capture the pending diagnostic through the existing console.warn patch.
  - Host-integration run produces a compatibility report showing the provider query succeeded while discovery was gated, with the pending diagnostic captured.
  - _Depends: 1.1, 1.3, 2.1_
  - _Boundary: Host integration harness_
  - _Requirements: 1.1, 1.2, 1.3, 5.2_
- [ ] 4. Validation
- [ ] 4. Run the full package gate: unit tests, typecheck, lint, build; existing catalog-coalescing, registration, secret-policy and prepared-runtime tests green; confirm via diff that the bridge protocol files and version coupling are untouched.
  - All checks green in one run; git diff shows no changes under the bridge/protocol or version-coupling surfaces.
  - _Requirements: 2.3, 3.4, 4.2, 5.1_

> README/`--print-logs` documentation is consciously deferred to release notes (task rules exclude documentation tasks).
