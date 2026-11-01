# Practical implementation plan

Revision 2 replaces the 36-subtask queue. The user requested a pragmatic baseline
on 2026-11-01. Three implementation deliverables remain after parallel pytest.
Requirements/design/remaining-task approval is pending; do not automatically
resume the old queue. Historical completed work is retained below.

- [x] 1. Make Python regression runs parallel and repeatable
- [x] 1.1 Enable and verify pytest-xdist
  - Lock the development dependency, use two workers by default and retain a serial override.
  - Keep worker/child offline guards, isolated state, prepared uv cache and aggregate JUnit acceptance working.
  - Complete when the full parallel suite and serial isolation checks pass and the existing acceptance reader consumes its JUnit outcomes.
  - _Boundary: Python test infrastructure_
  - _Requirements: 3.1, 3.2_

- [ ] 2. Complete the native-tools baseline
- [ ] 2.1 Finish commands and retain the verified file workflows
  - Reuse read/glob/grep, write/edit and scoped patch evidence without rebuilding the harness.
  - Apply the confirmed Windows environment/exit-code fixture fix; verify real shell output, nonzero outcome, denied/invalid cases and bounded cleanup.
  - Complete when native query, mutation and command probes pass with actual result continuations and independent state checks; untested profiles remain explicit.
  - _Boundary: Native workspace integration_
  - _Requirements: 1.1, 1.2, 1.3, 3.3, 4.1_

- [ ] 3. Prove one real MCP tool workflow
- [ ] 3.1 Add the owned local fixture and exercise the host client
  - Use the prepared official SDK and real OpenCode MCP discovery/execution for a nested/Unicode structured-result tool.
  - Exercise one deliberate tool error and cancellation, preserving call association and owned cleanup.
  - Complete when real server calls/results reach the next model turn, failures remain failures, and no private MCP config is used.
  - _Boundary: MCP baseline integration_
  - _Requirements: 1.2, 1.3, 2.1, 2.2, 3.3, 4.1_

- [ ] 4. Deliver the repeatable baseline and operator guide
- [ ] 4.1 Integrate verification, report the subset and document live testing
  - Reuse existing reports/validators and retain mandatory parent regression/build checks; add one prepared offline baseline command.
  - Report actual versions/profiles, tested cases and deferred tools, with artifact links and no blanket all-tool claim.
  - Document direct-provider setup and an explicit live native/MCP procedure using Vibe OIDC; leave unexecuted live cases unverified.
  - Complete when the fresh offline pipeline passes, the guide's commands match the runner, and a single final review finds no baseline blocker.
  - _Boundary: Verification and operator integration_
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 3.1, 3.2, 3.3, 3.4, 4.1_

## Preserved implementation history

Twelve original subtasks (1.1-1.3, 2.1-2.3, 3.1-3.4 and 4.1-4.2) were
independently reviewed and committed through 42f1fc1. Their numbering is historical
and must not be confused with this revision's task IDs. See implementation-log.md.
The last committed original task plan is available at that commit.

The uncommitted original 4.3 command draft is retained. Its debugger recommends
RETRY_TASK / CONFIG_GAP: restore minimal Windows PATHEXT and propagate native
PowerShell exit codes explicitly. Do not restart from scratch or include the
separate .task43-debug-* diagnostic copies in an implementation commit.

Remaining original tasks are superseded, not completed. Specialist tool coverage
is deferred in scope-revision.md and verification-plan.md. Preserve existing tests;
no further generic certification work is required for this baseline.
