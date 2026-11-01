# OpenCode Vibe tool compatibility

Revision 2, requested 2026-11-01: finish a useful native-tools and MCP baseline
without building a general compatibility certification framework.

Mistral-Vibe is a direct OpenCode provider using the shared Python Vibe OIDC
library. OpenCode owns native executors, permissions and the MCP client. Keep
those boundaries and verify that actual results reach the next model turn.

The baseline covers files/search, file changes, shell commands, and one owned
MCP server with a structured tool result, failure and cancellation. Reuse the
existing harness and regression tests. Broader tools remain visible as follow-ups,
with no claim that all tools or future versions have been tested.

Twelve legacy implementation tasks through 4.2 were independently reviewed and
committed. Legacy 4.3 is an uncommitted draft; its Windows environment issue is
identified in task-4.3-debug.md. Preserve that work and finish only what the
baseline needs after revised-spec review. The user subsequently approved continuation, prioritizing E2E, and requested
OpenCode contract comparisons, proxy API docs and structured diagnostics. The
observability follow-up is initialized separately.

See requirements.md, design.md and tasks.md for the current plan;
scope-revision.md explains what replaced the original 36-task plan. Historical
reports and implementation-log.md remain evidence for their original scopes.
