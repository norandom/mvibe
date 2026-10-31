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
