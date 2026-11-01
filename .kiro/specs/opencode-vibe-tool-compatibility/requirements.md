# Requirements

## Revision and scope

Revision 2 replaces the 33-criterion all-tool certification scope at commit
42f1fc1. The user requested this reduction on 2026-11-01. These baseline criteria
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

5. When checking an OpenCode update, verification shall compare a reviewed HTTP
   OpenAPI snapshot and relevant plugin declarations with an explicitly prepared
   candidate, report structural drift, and compile/run the E2E baseline for that
   pair. It shall record actual versions/hashes and never silently bless a new
   baseline or infer behavioral compatibility from schema equality alone.

## 4. Provider boundary

1. If baseline execution reveals a provider protocol defect, a minimal adapter fix
   shall follow a reproducing regression test. Native execution, MCP ownership,
   Vibe OIDC auth and existing no-replay behavior shall remain intact. Unproven
   content or capabilities shall be explicit limitations rather than guessed codecs.

## 5. Proxy API and diagnostic foundation

1. When proxy API documentation is requested, the proxy shall expose OpenAPI JSON
   and Swagger UI describing its actual models/chat-completions/embeddings routes,
   streaming, error envelopes and agent-owned tool execution. Documentation shall
   not narrow runtime payload passthrough or imply the proxy is an MCP executor.
2. When diagnostics are enabled and an agent request fails or its stream is interrupted, the proxy shall emit
   structured stdout diagnostics with a correlation ID, phase/outcome and safe
   actionable hint. Logs shall exclude credentials, headers, raw prompts/tool
   arguments/results and upstream exceptions; logging shall preserve stream
   ownership and no replay. Use a logging library with an OpenTelemetry path;
   distributed traces/metrics/export remain the observability follow-up.

   The user's live-failure follow-up adds explicit opt-in controls: proxy `--log`
   and direct-plugin `options.diagnostics: true`, both off by default. Direct
   plugin events shall report acquisition/response headers and fixed recovery
   hints on stderr without reading bodies or claiming stream completion. On the
   tested native CLI, document `--print-logs` for outer stderr visibility.

## Approval

The user approved revision 2 and continuation on 2026-11-01, explicitly adding
update/schema checks, proxy API documentation and diagnostic foundations.
Observability tracing/export is captured as a follow-up specification rather
than a prerequisite for completing the E2E baseline.
