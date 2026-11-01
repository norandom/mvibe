# Baseline verification

Revision 2: a small end-to-end baseline with honest limits.

| Layer | Required check | Completion evidence |
| --- | --- | --- |
| Python | Full guarded suite with two xdist workers; serial isolation/debug override | Aggregate JUnit with executed outcomes, no worker failures/skips |
| Existing provider | Bun unit tests, typecheck/build and current parent acceptance | Existing runner output; no removed mandatory checks |
| Native files | Reuse real read/glob/grep, write/edit and scoped patch probes | Actual operation, independent state and correlated next request |
| Native commands | Owned stdout/stderr and exit 0/nonzero; denied/invalid case | Actual host outcome, unchanged unrelated files, continuation and cleanup |
| MCP | One official-SDK local server, discovery, nested/Unicode call and structured result | Actual server/host invocation and correlated next request |
| MCP negatives | One tool error and cancelled operation | Correct negative outcome and bounded server/host cleanup |
| Live | Separately invoked harmless native/MCP run with existing Vibe OIDC | Actual selected model/profile and outcomes; otherwise unverified |

Use prepared uv-managed Python, locked Bun dependencies and a recorded native
OpenCode binary. Default verification installs nothing and accesses no accounts.
Host probes keep private homes/config and permit only owned resources. Never
load the user's private MCP configuration or replace native executors.

Reuse existing proofs and validators. Do not add a new evidence format or a
second certification framework. A successful offline baseline does not establish
live model support. Capture fixture data/hashes and fixed outcomes, not secrets,
headers, sign-in URLs or private workspace contents.

## Explicit follow-ups

Keep tool-coverage.md as historical discovery context. Additional registered tools
are unverified/deferred rather than blockers for this baseline. Questions/forms,
skills/subagents, session/worktree tools, web/search, resource helpers, pagination,
media codecs and private plugin services need separately justified follow-up work.
No all-tool compatibility claim is made. Host updates require rerunning the
baseline and recording changed versions before carrying forward its results.

## Update and proxy checks

Capture reference HTTP/plugin contracts from prepared packages, compare an
explicitly prepared candidate, then compile and rerun the same native/MCP E2E
cases. Keep drift results separate from execution failures and record exact
versions/hashes. No automatic downloads or baseline rewrites during offline runs.

Proxy checks cover OpenAPI JSON/Swagger routes and structured stdout events for
endpoint/auth/upstream/stream failures, correlation and secret exclusion. Preserve
byte streaming, status passthrough and helper protocol stdout. Trace export and
metrics are deferred to proxy-observability; logging foundations are implemented now.
