# Requirements

## Revision and scope

Revision 2 replaces the 33-criterion all-tool certification scope at commit
42f1fc1. The user requested this reduction on 2026-11-01. These ten criteria
cover a practical baseline; historical criteria and approvals do not transfer
automatically. Shared auth/proxy requirements remain in vibe-oidc-proxy.

## 1. Native tools

1. When a native baseline runs, the suite shall use real OpenCode file/search,
   write/edit and shell executors in an owned temporary workspace, retaining
   offered definitions and host permissions. Existing conditional patch evidence
   may be reused only for its recorded fixture model profile.
2. When a baseline tool succeeds, verification shall check the actual operation,
   independent fixture state or output/exit, and the correlated result in the
   next model request. A plausible final answer alone shall not establish success.
3. When testing denial, malformed arguments, nonzero exits or cancellation, the
   suite shall preserve the host outcome, prevent unintended effects and release
   owned resources within a timeout. A negative outcome shall not earn successful
   execution credit or cause an inference replay.

## 2. MCP tools

1. When the MCP baseline runs, OpenCode's real MCP client shall initialize and
   discover an owned local server built with the prepared official SDK, invoke
   a tool with nested/Unicode arguments, and deliver its correlated structured
   result to the next model request.
2. When that fixture returns a tool failure or is cancelled, the suite shall
   observe the actual server/host outcome and bounded cleanup without replacing
   the MCP client or confusing a tool error with successful execution.

## 3. Practical verification

1. When ordinary Python tests run, pytest-xdist shall use two local workers by
   default, with isolated test state and offline guards in each worker. A serial
   override shall remain available, uv shall manage Python, and aggregate JUnit
   evidence shall preserve executed-test identities and outcomes.
2. When validating the feature, the existing Python/Bun tests, builds and parent
   acceptance checks shall remain required. Reuse existing validators and reports;
   no additional generic report format or duplicate validator is required.
3. When reporting results, the suite shall identify the actual host, model/profile,
   tested tools and offline/live mode. Other tools and untested updates shall be
   listed as deferred or unverified; baseline success shall not imply all-tool support.
4. When live verification is requested, the operator shall explicitly select a
   harmless native/MCP workflow and model using existing Vibe OIDC credentials.
   Default tests shall not access live accounts or launch sign-in. Instructions
   shall state prerequisites and leave unexecuted live cases unverified.

## 4. Provider boundary

1. If baseline execution reveals a provider protocol defect, a minimal adapter fix
   shall follow a reproducing regression test. Native execution, MCP ownership,
   Vibe OIDC auth and existing no-replay behavior shall remain intact. Unproven
   content or capabilities shall be explicit limitations rather than guessed codecs.

## Approval

This revision is ready for review. Its requirements, design and remaining tasks
have not yet received human approval. The requested xdist change is authorized
and verified separately; this does not approve the remaining implementation queue.
