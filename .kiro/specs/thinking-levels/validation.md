# Thinking levels: v1.1.0 candidate validation

Date: 2026-11-03. Candidate only: not published or installed in the user's profile.

Decision: offline implementation and packaged-artifact checks VERIFIED.
Publication remains MANUAL_VERIFY_REQUIRED for outstanding human specification
review and the repository's cross-platform release workflow.

## Scope and outcome

The OpenCode adapter exposes Large 4 low/high/max and Medium high/off choices
through native model variants. Exact curated GLM aliases also expose low/high/max;
the flash model has no curated row. Selected settings become the top-level
Mistral `reasoning_effort` field. No selection adds no field. This is distinct
from native Vibe already supporting thinking selection.

The proxy retains OpenAI passthrough and adds Anthropic/Ollama request mappings.
Reasoning-output/history translation for those two adapters is outside this
feature; the operator guide records those limits.

## Requirement coverage

| Requirement | Implementation and evidence |
| --- | --- |
| 1 | `thinking.ts`, `models.ts`; curated catalog/metadata-boundary tests and actual host model-list assertions. |
| 2 | SDK generate/stream tests; actual OpenCode 2.2.5 sessions select low/high/max/off/default and assert upstream effort. Both source and extracted bundle pass. |
| 3 | `reasoning.ts`, `reasoningSupported`; active/off/unknown histories, including tool calls and quoted paths, backslashes, newlines and Unicode. |
| 4 | `test_proxy.py` stream/non-stream request forwarding and byte-exact response passthrough. |
| 5 | `compat.py` Anthropic mapping and malformed-option regressions. |
| 6 | Shared Ollama option mapping and chat/generate tests. |
| 7 | Full Python/Bun suites, typecheck/build/lint, source and packaged host integration, native release installation, complete tool baseline and operator docs. |

Dependency direction and authentication boundaries are unchanged. The SDK hook
already supplies the body containing model and effort to `mistralRequestBody`;
no additional authentication or transport state was introduced. Release work
also updates versions and replaces hardcoded distribution versions in tests.

## Review and corrective evidence

Independent review initially rejected two defects: malformed Anthropic thinking
could activate effort, and unknown models could receive unsupported thinking
history. Seven Python cases and two Bun assertions failed before remediation.
The corrected focused suites passed (17 Python, 12 Bun). Independent re-review
approved the remediations and current source changes.

Original implementation RED evidence is unavailable. These new failures prove
the corrections; they do not reconstruct the original implementation process.

The review also narrowed the GLM wildcard documentation to exact native aliases
and reconciled stale `body` overlay text with `settings.reasoningEffort`.

## Fresh mechanical evidence

- Python: 1,223 passed, one optional telemetry test skipped; Ruff passed.
- Bun: 1,061 passed, zero failed after the readiness regression additions;
  typecheck, ESLint and build passed.
- Source host: `reports/thinking-source-host.json`, OpenCode/SDK 2.2.5.
- Extracted host: `reports/thinking-release-host.json`, all recorded checks pass.
- Native archive installation: backup/idempotency, isolated paths with spaces,
  Python import and prebuilt-artifact checks passed. The smoke accepts an explicit
  system PowerShell executable because Store PowerShell failed under isolated
  application homes on this workstation; isolation was preserved.
- Packaged discovery originally exceeded the fixture's five-second deadline.
  Only that discovery wait now allows twenty seconds; production is unchanged.
- Initial aggregate run `0f85a6b6c80f48d8afee8ac8f16f66cf` failed on a remaining
  version assertion and a workspace-mutation probe. The version assertion was
  corrected and the full Python suite rerun. Run
  `92bd762e9bb14115837fdc21f6489338` passed every step except that same probe.
  Diagnostic tracing captured `ServiceUnavailableError`, service `plugin`,
  `Location plugins did not finish activating` before tool execution. The runner
  now retries only that exact error for startup location/provider queries, within
  its existing abort budget. No inference or tool operation is replayed.
  Independent review approved this narrow fixture correction; lifecycle regression
  tests and the native mutation probe passed. The full tool-baseline rerun then
  passed compilation, contract comparison, query, mutation, command and MCP probes:
  `reports/thinking-tool-baseline-final/baseline.json` (exit 0). Earlier failed
  acceptance reports are retained, not rewritten; their failed startup probe is
  superseded by this fresh successful baseline.
- `git diff --check` passed.

## Release and remaining gates

Candidate: `dist/release-thinking-1.1.0/mvibe-1.1.0.zip`.
SHA256: `9b6eaeebfdebb2dde741ceefe5242d7d16fcfb45672df07b831d2468a61966a1`.
After adding the final release notes, the rebuilt provider was verified
byte-identical to the extracted provider used in the host test.

The user's installation remains the published v1.0.0 release. Never install this
local candidate into that profile. Publish through the repository's gated release
workflow, then download/checksum/install the published artifact and restart the
service. Final user-profile model-catalog verification belongs after that install.

Human review of the fast-tracked specification remains required before merge.
The release workflow must pass Linux, Windows and macOS checks before publication.
Optional live model/level acceptance (task 4.3) remains unverified. The historical
escaping report has not been reproduced as a provider defect; current preservation
regressions pass, which does not establish that every shell command is correct.
