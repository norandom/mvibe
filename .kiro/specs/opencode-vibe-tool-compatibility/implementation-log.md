# Implementation evidence

Tasks approved by the user. Review mode: required, independent task-local review.
Live execution is separate from the default offline implementation checks.

## 1.1 Prepared runtimes

- Commit: baf086a.
- Implementer: READY_FOR_REVIEW; independent reviewer: APPROVED.
- RED: missing preparation module, native Bun exit 1.
- Fresh review: 498 Bun tests / 1,568 assertions, typecheck and build passed.
- Native preflight: Bun 1.4.4 / OpenCode 2.2.5 passed; missing host failed with
  preparation guidance. Exact fixture/type dependencies locked.
- Completion gate: VERIFIED for preparation only. Host tool execution and live
  compatibility are not established by this task.

## 1.2 Shared evidence contracts

- Implementer: READY_FOR_REVIEW; independent review: APPROVED after one repair.
- RED: flag OFF 19 pass / 1 fail; array regression 53 pass / 1 fail.
- Review caught inconsistent tool identities and cancellation correlations.
  Repair regressions failed before the fix; original independent reproductions
  now reject with the fixed invalid_report error.
- Fresh independent verification: 561 Bun tests / 1,680 assertions, typecheck
  and build passed, exit 0. Completion gate: VERIFIED for schema/identity scope.
- Manifest authority, positive execution credit and live support remain later work.

## 1.3 Authoritative cases

- Implementer: READY_FOR_REVIEW; independent review: APPROVED.
- RED flag OFF: 24 pass / 4 fail; flag ON: 28 pass; flag removed.
- Fresh independent verification: 594 Bun tests / 1,852 assertions; typecheck
  and build passed, exit 0. Completion gate: VERIFIED for case definitions.
- Forty-five immutable recipes cover 24 observed tool IDs and owned fixture
  families, with explicit positive/negative expectations and bounded limits.
- Per-run trusted fixture material supplies expected hashes. These expectations
  do not grant execution credit; applicability and runtime evidence remain scoped.

## 2.1 Per-turn inventory

- Implementer: READY_FOR_REVIEW; independent review: APPROVED after one repair.
- Initial RED: 0 pass / 10 fail with the flag OFF; flag removed after GREEN.
- Review reproduced stale scope metadata under a retained hash. Nine new
  regressions failed before repair; the independent original repro now rejects.
- Fresh independent verification: 614 Bun tests / 1,957 assertions; typecheck
  and build passed, exit 0. Completion gate: VERIFIED for inventory accounting.
- Explicit provenance, per-turn definitions, negotiated MCP data and scoped
  assignments/classifications produce gaps on unknown or changed observations.
  Inventory accounting grants no positive tool-execution credit.

## 2.2 Scoped report gate

- Implementer: READY_FOR_REVIEW; independent review: APPROVED.
- RED flag OFF: 27 pass / 1 fail; flag removed after GREEN.
- Fresh independent verification: 672 Bun tests, typecheck and build passed,
  exit 0. Independent mutations reject missing rows, reused executor proofs,
  crossed continuation inputs and cross-mode positive claims.
- Completion gate: VERIFIED for report assembly and validation. External cases,
  scope/mode, hashes, limits and provenance constrain positive/negative proofs.
- Synthetic baseline credit demonstrates gate behavior, not host execution.

## 2.3 Independent read-only validator

- Implementer: READY_FOR_REVIEW; independent review: APPROVED.
- RED: valid positive report rejected with the flag OFF; 2 failed / 19 passed
  including a separately corrected test snapshot defect. Flag removed after GREEN.
- Fresh independent verification: 1,016 Python tests and 717 Bun tests passed;
  typecheck and build passed, exit 0. Forty-five shared synthetic vectors agree.
- Independent cross-mode mutation rejects positive live credit from offline data.
- Completion gate: VERIFIED for independent validation. Standard-library-only,
  bounded read-only CLI has fixed sanitized errors and no application/account
  imports. No host-execution or live compatibility claim follows from vectors.

## 3.1 Isolated offline host lifecycle

- Implementer: READY_FOR_REVIEW; independent review: APPROVED.
- RED: 0 pass / 2 fail HOST_DISABLED; flag ON 2 pass, then removed.
- Fresh independent verification: 721 Bun tests / 2,183 assertions, typecheck
  and build passed. Windows native readiness and cancellation smokes passed:
  OpenCode 2.2.5 / Bun 1.4.4 / uv-managed Python 3.12.10; cleanup verified.
- Completion gate: VERIFIED for isolated lifecycle on Windows. Other platforms,
  actual tools, observation chains and live operation remain unverified.
- Windows launch-before-Job cleanup race was reproduced and repaired; the failed
  probe Job was drained and no owned supervisors remained. Its directory remains
  at C:/Users/mariu/AppData/Local/Temp/vibe-tool-host-OtoRTJ.
- Automatic approval review rejected resolved-target PowerShell deletion of
  C:/Users/mariu/AppData/Local/Temp/vibe-debug-edf6d716-fbb6-4720-8d3e-707c2a731f03
  with reason blocked by policy. No alternate deletion was attempted.
- The SDK generate API lacks a location input. Readiness uses the plugin's bound
  context; external location/provider calls carry explicit verified location.

## 3.2 Deterministic streamed inference

- Implementer: READY_FOR_REVIEW; independent review: APPROVED.
- RED flag OFF: 1 pass / 11 fail with synthetic_protocol; flag removed after GREEN.
- Fresh independent verification: 738 Bun tests passed; typecheck and build
  passed, exit 0. Completion gate: VERIFIED for the pure model boundary.
- Installed SDK stream/continuation tests cover nested Unicode, interleaved and
  repeated tools, reversed results, identity drift, bounds, abort and replay.
- No fake executor, host launch, auth change or network fallback. Actual host
  tool execution and continuation evidence remain subsequent integration work.
