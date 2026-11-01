# Scope revision: practical baseline

Requested by the user on 2026-11-01 after the original plan proved excessive.
The old requirements, design and 36-task plan remain recoverable in Git at
42f1fc1. This revision keeps implemented code and tests; it changes the remaining
acceptance target to native files/commands plus one representative MCP workflow.

| Original scope | Revision 2 disposition |
| --- | --- |
| Native definitions/execution/result continuation | Retained in requirements 1.1-1.3 |
| MCP discovery/invocation/error/cancellation | Retained in requirements 2.1-2.2 |
| Scope/correlation/negative-proof machinery | Reuse existing code; no more generic framework construction |
| Offline safety, versions and truthful claims | Retained in requirements 3.1-3.4 and 4.1 |
| Every offered tool must pass before feature completion | Replaced by a declared tested subset with deferred/unverified exclusions |
| Questions, skills, child sessions, session/worktree operations | Follow-up; old 4.4-4.7 are superseded |
| Web/search, private plugin fixtures, experimental media | Follow-up; old 5.3-5.5 are superseded |
| MCP resources/pagination/list-change matrix | Follow-up; core error/cancel parts of old 5.2 remain in the MCP baseline |
| Broad model-specific wire/name/ID/media correction profiles | Deferred; old 6.1-6.5 become minimal fixes only for reproduced baseline defects |
| Exhaustive negative/cancel/drift combinations | Representative baseline cases; old 7.x is superseded by integration task 4.1 |
| Cross-platform auth-path launcher and live certification machinery | Deferred unless a baseline run proves it necessary; existing OIDC remains shared |
| Final pipeline and operator guide | Retained as one integration deliverable; default remains offline |

## Review and history

Original task/design reviews concern the superseded plan, not approval of this
revision. Twelve original tasks remain completed historical work; pending old
tasks are not silently marked done. Existing implementation-log.md and native
artifacts retain their original narrower claims.

The current request authorizes introducing xdist and reshaping the specification.
Revised requirements, design and remaining tasks are presented together for human
review. Do not resume the former autonomous 36-task queue. The explicitly requested
xdist implementation is verified now without starting live native/MCP runs.

Subsequent user approval resumes revision 2. The same instruction adds HTTP/plugin contract comparison and proxy OpenAPI/structured logging; current tasks now have five deliverables. Distributed tracing/export remains a separate initialized observability follow-up. See api-observability-notes.md.
