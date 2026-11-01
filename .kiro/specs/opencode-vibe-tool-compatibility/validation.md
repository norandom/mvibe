# Practical baseline validation

## Live Large 4 repair (2026-11-01)

The user-reported PowerShell hello-world failure was traced to an actual 422:
`body.messages[2].assistant.reasoning_content`, `extra_forbidden`. The direct
SDK now replays reasoning as Mistral thinking chunks, retaining tool calls/results.
Installed-SDK generate/stream and provider-registration regressions cover the fix.
Independent review ran all 974 Bun tests, typecheck and build successfully.

An explicit live native CLI run used OpenCode 2.2.5, `vibe-oidc/mistral-large-4`,
existing Vibe OIDC credentials and isolated OpenCode paths/workspace. It observed
one `tool_use`, the exact requested `hello.ps1`, final `DONE`, exit 0 and no 422.
The generated script independently ran under PowerShell and printed
`Hello, World!`. This proves the exercised file-write/continuation scenario;
live MCP, other models/tools, refresh and newer host versions remain unverified.
Safe local summary: `reports/live-large4-result.json`. No prompts from the user's
original conversation or credentials were exported.

## Opt-in diagnostic follow-up verification

Task 5.2 independently reviewed: APPROVED. Proxy `--log` enables safe structlog
stdout; direct plugin `options.diagnostics: true` enables safe JSON stderr.
Both default off. Native OpenCode 2.2.5 also needs `--print-logs` for visible
forwarded events. A repeated live Large 4 run created the correct file and final
answer without 422 while exposing five safe successful header-acquisition events.
The plugin does not claim to observe later stream completion.

Fresh canonical `scripts/verify.ps1` exited 0: 1,037 Python tests, 983 Bun tests,
all 11 mandatory stages and all 97 parent mappings passed. Evidence:
[acceptance.json](../../../reports/verification/ba4b5fcf099a40cabafe6ab22c8c0602/acceptance.json).
This report is offline evidence; the separate live observations above establish
only their stated model/workflow scope. The baseline validation below is the
historical pre-follow-up record.

## Follow-up integration gate

- DECISION: GO, scoped to the declared baseline and follow-up tasks 5.1/5.2.
- MECHANICAL_RESULTS: Full canonical command exited 0; 1,037 Python/983 Bun
  tests, builds and all 11 stages passed. Review found no introduced placeholders
  or hardcoded secrets. `git diff --check` passed.
- INTEGRATION: Actual native host setup, consumer integration, native/MCP
  synthetic baseline and separate real Large 4 file-tool continuation passed.
  The request transformer precedes authenticated fetch; diagnostics observe
  headers without consuming the transformed body or changing tool execution.
- COVERAGE: All 13 baseline criteria retain fresh canonical evidence; 4.1 has
  the reproduced reasoning regression plus live continuation, and extended 5.2
  has both consumers' opt-in/privacy/failure-sink tests and CLI visibility checks.
- DESIGN: Dependency direction and boundaries retained. Shared auth owns OIDC,
  OpenCode owns tools/MCP, direct SDK owns Mistral request adaptation, proxy owns
  passthrough/stream lifetime. No exporter or invented trace context introduced.
- OWNERSHIP: LOCAL. UPSTREAM_SPEC: N/A.
- BLOCKED_TASKS: None. Live MCP/other models/tools/updates and distributed
  observability remain outside the exercised scope, not claimed as passed.
- REMEDIATION: None. Both task-local independent reviews APPROVED; fresh-evidence
  completion gate VERIFIED for the stated scope.

## Validation Report
- DECISION: GO
- SCOPE: Prepared Windows x64 / OpenCode 2.2.5 synthetic offline baseline and proxy API/diagnostic foundations. Live accounts, future versions, other platforms and all-tool compatibility are not claimed.
- MECHANICAL_RESULTS:
  - Tests: PASS. Fresh independent canonical scripts/verify.ps1 exited 0: 1,034 Python tests, 967 Bun tests, all 11 mandatory stages and all 97 parent mappings passed.
  - TBD/TODO grep: CLEAN for new implementation.
  - Secrets grep: CLEAN; fixture credentials are synthetic/generated.
  - Static checks: PASS, TypeScript typecheck, provider/Python builds and diff check.
  - Smoke boot: PASS, actual native host/consumer integration and sequential query/mutation/command/MCP probes; injected ASGI startup and proxy CLI checks remain separately scoped.
- INTEGRATION:
  - Cross-task contracts: PASS. Existing recorder/parsers, real host executors, official MCP server/client and next-request assertions are reused by one baseline command.
  - Shared state consistency: PASS. Private owned workspaces, exact fixture-state checks and bounded cleanup; no private MCP or live account configuration loaded.
  - Boundary audit: CLEAN. Auth/helper ownership and provider no-replay retained. Proxy diagnostics do not buffer or interpret tool execution. No OpenTelemetry exporter installed.
- COVERAGE:
  - Requirements mapped: 13/13, below.
  - Coverage gaps: None for the declared offline baseline; explicit live procedure is documentation, not an executed live result.
- DESIGN:
  - Architecture drift: None found by independent final review.
  - Dependency direction: Preserved; OpenCode executes tools/MCP, shared library owns Vibe OIDC, proxy forwards conversations.
  - Integration structure: Baseline/capture/compare commands, candidate preparation and operator guide match the approved extension.
- OWNERSHIP: LOCAL
- UPSTREAM_SPEC: N/A; parent vibe-oidc-proxy acceptance remains 97/97 passed.
- BLOCKED_TASKS: None in the revised baseline. The observability follow-up remains initialized for later requirements/design work.
- REMEDIATION: None.

## Requirement coverage

| Criterion | Implementation/evidence |
| --- | --- |
| 1.1 | Real query/mutation/command family probes; actual definitions/permissions and conditional patch profile retained |
| 1.2 | Existing recorder/synthetic-inference checks actual correlated results in the next primary request and independent state |
| 1.3 | Native denial/schema/nonzero outcomes, MCP cancellation and streaming cleanup/no-replay regressions |
| 2.1 | Official SDK 1.32.2 stdio fixture initialized/discovered/invoked by real OpenCode; nested Unicode structured result |
| 2.2 | Actual tool-error continuation and SDK AbortSignal, no positive error/cancel credit, owned MCP PID cleanup |
| 3.1 | Locked xdist, two default workers, controller/worker isolation and serial override; aggregate JUnit |
| 3.2 | scripts/verify.ps1 retains ten old stages plus mandatory tool-baseline; parent validator still maps all 97 criteria |
| 3.3 | Baseline versions/hashes/model/profile/limitations and docs/tool-compatibility.md identify tested subset |
| 3.4 | Separate explicit live native/MCP procedure; default runs offline and leave live unverified |
| 3.5 | Prepared HTTP OpenAPI and plugin declarations; immutable reference, located drift, matching candidate compile/E2E and selected-root regression |
| 4.1 | Demonstrated reasoning replay adapter with installed-SDK regression and live Large 4 continuation; native/MCP ownership and shared OIDC remain intact |
| 5.1 | Explicit OpenAPI/Swagger operations, common tool/SSE/error documentation, raw payload passthrough regressions |
| 5.2 | Opt-in proxy structlog stdout and plugin JSON stderr, safe IDs/phase/status/timing/hints; cancellation/privacy/logging-failure tests and native --print-logs visibility |

## Fresh evidence

Independent final review: APPROVED. Full pipeline:
[acceptance.json](../../../reports/verification/f0f7b55625f84eb8a6e4e51b6e849ce6/acceptance.json).
Earlier implementer final PASS:
[acceptance.json](../../../reports/verification/1c057bce385247e5871ab087bbbb26f5/acceptance.json).
The first failed integration run is preserved separately; it caught a reference
error-message regression, which was repaired before final verification.

Candidate-mode evidence:
[baseline.json](../../../reports/tool-baseline/candidate-task42-final/baseline.json).
This is a separately copied provider using the same prepared 2.2.5 dependencies
via a documented local junction and the same native binary, not a tested upgrade.
Both protocol and Effect resolve from the selected tree; original Python project
root remains explicit. No global installation or account changes occurred.

Independent CLI negative checks removed an HTTP operation and changed a plugin
declaration, returning two located differences and exit 1. Malformed snapshots
and reference-overwrite attempts also returned exit 1. Reference SHA256 remained
883b83e376dc644d740b3f8dc0a3852484c8bcb13cd1d5019a590ea07a549b03.
Reports are local ignored execution artifacts; keep them with exported results.

## Operator entry points

See [the baseline/update/proxy guide](../../../docs/tool-compatibility.md).
Proxy Swagger and OpenAPI are /docs and /openapi.json after starting the updated
proxy. Its stdout correlation IDs match response X-Request-ID headers.
[Observability follow-up](../proxy-observability/brief.md) covers future tracing,
metrics and opt-in export; selecting structlog does not claim those are implemented.
