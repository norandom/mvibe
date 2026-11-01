# Practical implementation plan

Revision 2 replaces the 36-subtask queue. The user requested a pragmatic baseline
on 2026-11-01. The E2E baseline is approved. The current request also adds contract drift checks,
proxy API docs and diagnostic foundations; tracing/export is a follow-up.
The user approved revision 2 and continuation on 2026-11-01. Implement the
baseline below; do not resume the superseded queue. Historical completed work is retained below.

- [x] 1. Make Python regression runs parallel and repeatable
- [x] 1.1 Enable and verify pytest-xdist
  - Lock the development dependency, use two workers by default and retain a serial override.
  - Keep worker/child offline guards, isolated state, prepared uv cache and aggregate JUnit acceptance working.
  - Complete when the full parallel suite and serial isolation checks pass and the existing acceptance reader consumes its JUnit outcomes.
  - _Boundary: Python test infrastructure_
  - _Requirements: 3.1, 3.2_

- [x] 2. Complete the native-tools baseline
- [x] 2.1 Finish commands and retain the verified file workflows
  - Reuse read/glob/grep, write/edit and scoped patch evidence without rebuilding the harness.
  - Apply the confirmed Windows environment/exit-code fixture fix; verify real shell output, nonzero outcome, denied/invalid cases and bounded cleanup.
  - Complete when native query, mutation and command probes pass with actual result continuations and independent state checks; untested profiles remain explicit.
  - _Boundary: Native workspace integration_
  - _Requirements: 1.1, 1.2, 1.3, 3.3, 4.1_

- [x] 3. Prove one real MCP tool workflow
- [x] 3.1 Add the owned local fixture and exercise the host client
  - Use the prepared official SDK and real OpenCode MCP discovery/execution for a nested/Unicode structured-result tool.
  - Exercise one deliberate tool error and cancellation, preserving call association and owned cleanup.
  - Complete when real server calls/results reach the next model turn, failures remain failures, and no private MCP config is used.
  - _Boundary: MCP baseline integration_
  - _Requirements: 1.2, 1.3, 2.1, 2.2, 3.3, 4.1_

- [x] 4. Deliver API diagnostics and the repeatable baseline
- [x] 4.1 Publish proxy API docs and structured diagnostic logs
  - Expose accurate OpenAPI/Swagger for supported routes, passthrough tool conversations, streaming and errors.
  - Add structlog-based safe stdout events with request IDs, failure phases and actionable hints, without changing helper protocol stdout or buffering responses.
  - Complete when offline route/schema and failure/stream tests pass, secret-bearing inputs never appear in logs, and existing proxy behavior is retained.
  - _Boundary: Proxy API and diagnostic integration_
  - _Requirements: 3.2, 3.3, 3.4, 4.1, 5.1, 5.2_

- [x] 4.2 Integrate E2E reruns, compare update contracts and document operation
  - Reuse existing reports/validators and retain parent regression/build checks; add one prepared offline baseline command.
  - Capture and compare the prepared OpenCode HTTP OpenAPI plus plugin declaration contracts, compile against a prepared candidate SDK and rerun actual native/MCP workflows. Report drift/failures; never automatically refresh the reviewed baseline.
  - Report actual versions/profiles, tested cases and deferred tools, with artifact links and no blanket all-tool claim.
  - Document proxy Swagger/logging, direct-provider setup and explicit live native/MCP tests using Vibe OIDC; leave unexecuted live cases unverified.
  - Complete when the fresh offline pipeline passes and a separate candidate can be checked without changing the reference pair, global installation or private config.
  - _Boundary: Verification and operator integration_
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 5.1, 5.2_

## Live failure follow-up (2026-11-01)

The user requested repair of the actual Large 4 hello-world 422, opt-in logging
for both consumers, and continuation after a short handover. This is a focused
extension of the approved baseline, not a new all-tool certification queue.

- [x] 5.1 Repair the demonstrated reasoning replay incompatibility
  - Reproduce the rejected assistant `reasoning_content` in an installed-SDK regression.
  - Preserve reasoning as Mistral thinking chunks, text and correlated tool calls/results.
  - Verify package regressions and an actual native OpenCode Large 4 hello-world continuation.
  - _Boundary: Direct provider request serialization_
  - _Requirements: 1.2, 3.3, 3.4, 4.1_

- [x] 5.2 Add explicit diagnostic logging controls for both consumers
  - Add an opt-in proxy CLI flag and plugin option with safe correlated status/phase/hints.
  - Keep helper protocol stdout clean, payloads and credentials excluded, streaming and no replay intact.
  - Document enablement, diagnostic destinations and the observed live outcome.
  - _Boundary: Consumer diagnostics and operator guide_
  - _Requirements: 3.3, 5.2_

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
