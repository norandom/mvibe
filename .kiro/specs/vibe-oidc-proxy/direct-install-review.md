# Direct provider installation review

## Review Verdict
- VERDICT: APPROVED
- TASK: 9.2, implementation review before modifying the user's installation
- MECHANICAL_RESULTS:
  - Tests: PASS. `pwsh -NoProfile -File scripts/verify.ps1 -Bun D:/Source/mvibe/.tools/provider-bootstrap/node_modules/@oven/bun-windows-x64/bin/bun.exe -HostBinary C:/Users/mariu/AppData/Local/Programs/OpenCode/resources/opencode-cli.exe` exited 0. All ten mandatory steps passed: tools, Python, provider, typecheck, provider build, Python build, bridge process, root-exits-first cleanup, host setup, and native host integration. Python: 934 passed; Bun: 478 passed, 0 failed, 1,491 assertions. Native host: OpenCode 2.2.5 with SDK 2.2.5.
  - TBD/TODO grep: CLEAN. No placeholder markers in the new installers, configuration editor, provider changes, affected tests, README or operator guide.
  - Secrets grep: CLEAN for concrete private-key, token and API-key patterns; synthetic fixtures use visibly synthetic values. Metadata tests explicitly reject copying private headers and settings.
  - Static checks: PASS. Strict typecheck and both builds passed in the canonical pipeline. Git Bash syntax and help and PowerShell help each exited 0; install.sh contains zero CR bytes. `.gitattributes` fixes its checkout line endings to LF. `git diff --check` exited 0. No project lint command exists; runtime-sensitive imports, dependency declarations, lock consistency and wrapper argument handling were inspected.
  - Boundary: WITHIN. Changes cover installer wrappers/editor/tests, locked JSONC parser dependency, provider display/catalog integration, operator documentation, task/design amendment and specification status. The shell line-ending attribute supports the installer boundary. No authentication implementation or proxy route was added.
  - Boundary audit: CLEAN. Shared authentication remains in Python. The native SDK editor exposes immutable source definitions, including inactive providers. Only validated name, capabilities, token limits, release time, cost and status cross from matching canonical Mistral entries. Authenticated upstream IDs remain authoritative; explicit operator metadata takes precedence. Source connection binding, late-result checks and cancellation remain intact.
  - RED phase: VERIFIED from supplied original implementer/controller transcripts, not independently reproduced. Installer RED: missing `../scripts/install.js`, exit 1, 0 pass / 1 fail / 1 error. Provider RED: missing `metadataFromHostCatalog` export and actual `Vibe OIDC` versus expected `Mistral-Vibe`, exit 1, 9 pass / 2 fail / 1 error, 36 assertions. These failures directly exercise the added implementation.
- FINDINGS:
  1. FYI: No blocking findings. The installer parses before mutation, preserves unrelated JSONC settings and operator metadata, creates an exclusive timestamped backup, replaces through a same-directory temporary file, and avoids duplicate package entries on repeat installation. It checks native uv, pinned Bun, OpenCode major version, locked preparation and helper imports before editing configuration.
  2. FYI: This review used isolated offline configuration, synthetic authentication and controlled transport. It did not modify the user's OpenCode configuration, access the real keyring, launch browser login or perform live inference. Task 9.2 still requires the already authorized actual PowerShell installation and native direct-provider model/inference verification before completion.
  3. FYI: Windows behavior and Git Bash syntax/help were exercised. A native macOS/Linux installation was not executed. Core steering files product.md, tech.md and structure.md are absent; no steering content was inferred.
- REMEDIATION: None required before proceeding with the authorized installation. Record live results separately and retain incomplete status until those steps finish.
- SUMMARY: The direct installer and Mistral-Vibe catalog/display changes satisfy the approved installation amendment and requirement 12 boundaries and pass independent offline verification.

## Scope and evidence

Read task 9.2, the design's 2026-10-31 installation amendment, requirement 12, actual tracked diff and all new installer/editor files. Reviewed documentation and the specification status adjustment. The report is written in the specification's configured English language.

- Routine command output: `reports/install-direct-independent.log`.
- Canonical acceptance: `reports/verification/3df5e8d819a34bfd9cfa63f795ed933e/acceptance.json`.
- Per-step exit codes: `reports/verification/3df5e8d819a34bfd9cfa63f795ed933e/execution.json`.
- Python and provider JUnit reports and native host/startup evidence are in that same run directory.

The original RED transcript excerpts supplied for this review were:

```text
error: Cannot find module '../scripts/install.js' from 'D:\Source\mvibe\packages\opencode-vibe-oidc\test\install.test.ts'
0 pass / 1 fail / 1 error
Ran 1 test across 1 file. [60.00ms]
```

```text
SyntaxError: Export named 'metadataFromHostCatalog' not found in module 'D:\Source\mvibe\packages\opencode-vibe-oidc\src\models.ts'.
Expected name: Mistral-Vibe
Actual name: Vibe OIDC
9 pass / 2 fail / 1 error / 36 expect() calls
Ran 11 tests across 2 files. [413.00ms]
```

No runtime logic changed during the independent pipeline. The subsequent LF-only Git attribute was inspected separately and does not require another full suite.
