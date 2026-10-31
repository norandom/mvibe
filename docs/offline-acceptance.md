# Final offline acceptance

Executed on **2026-10-31**, starting **13:26:22 UTC**, against source commit
`12d8425c7b6255f660476fd455ace5c872d49060`. This records task 8.5 evidence;
independent task review and the final implementation validation gate remain separate.

| Evidence class | Observed result |
| --- | --- |
| Mandatory offline pipeline | **PASSED**, exit **0**; all ten mandatory steps passed |
| Requirement mappings | **97 passed**, zero failed/skipped/unverified mappings |
| Local startup | **PASSED**: injected production ASGI lifespan, loopback defaults, health, payload, route rejection and closure |
| Actual pinned OpenCode host | **PASSED**: host/SDK 2.2.5, all twelve synthetic integration checks |
| Live proxy/provider compatibility | **UNVERIFIED**; all eighteen account scenarios remain unexercised |

## Commands and executed checks

From the repository root, using prepared dependencies:

```powershell
./scripts/verify.ps1 -Bun D:/Source/mvibe/.tools/provider-bootstrap/node_modules/@oven/bun-windows-x64/bin/bun.exe -HostBinary D:/Source/mvibe/packages/opencode-vibe-oidc/.host/opencode.exe
```

The runner returned exit **0**. Python executed **919 tests, all passed**
(77.52 seconds); Bun executed **458 tests, all passed**, with 1,432 assertions.
The remaining mandatory checks passed: exact prepared tools/lock agreement,
TypeScript typecheck, provider build, Python wheel/sdist build, native managed
helper lifecycle, root-exits-first descendant cleanup, host setup and actual host
consumer integration. No mandatory check was skipped.

The full Python execution includes the two-process rotation/tenant overwrite
regression in `tests/test_coordination.py`, public-consumer parity and cancellation
in `tests/test_auth_lifecycle.py`, and ten process-level graceful-drain/forced-death/
authoritative-restart cases in `tests/test_bridge_lifecycle.py`. Native Bun harnesses
also verify owned descendant termination and unrelated-process survival.

The actual host checks cover marker-only persistence, authenticated models,
changing request credentials, incremental stream delivery, tool bytes, cancellation,
custom SDK invocation, cleanup and one-attempt 401/503 handling in stateless and
session inference. All upstream responses and credentials are synthetic. Local
startup evidence uses injected ASGI; it does not claim an actual proxy listening
socket or upstream readiness.

Separately, the native `uv` application ran:

```powershell
uv run --locked --offline scripts/live_evidence.py docs/live-evidence-template.json
```

It returned exit **0**, reporting nine **UNVERIFIED** proxy scenarios and nine
**UNVERIFIED** direct-provider scenarios. Structural validity proves no live result.
No live account, real keyring, browser or remote service action was requested by
these runs. No current-release discovery/preparation action ran.

## Evidence and versions

Fresh run directory:
[`reports/verification/4868d4a5fef7458cacbfa276bf403808/`](../reports/verification/4868d4a5fef7458cacbfa276bf403808/).
Its [acceptance.json](../reports/verification/4868d4a5fef7458cacbfa276bf403808/acceptance.json)
contains criterion-specific evidence; `execution.json` records all ten exit codes;
`python.xml` and `provider.xml` contain executed cases; `startup.json` and
`host.json` retain the separate startup/host results. The accompanying
`environment.json` captures every installed Python distribution, provider manifest,
source commit, platform and lock/build hashes. Generated reports are ignored files;
retain this run directory with any exported acceptance record.

Observed environment: Windows 11 build 26200, Python **3.12.10** under uv,
uv **0.13.5**, Bun **1.4.4**, pytest **9.2.0**, TypeScript **5.9.3**.
Library and provider artifact versions are **0.1.0**; private wire protocol is **1**.
Exact provider dependencies are `@opencode/plugin` **2.2.5**,
`@opencode/cli` **2.2.5**, `@ai-sdk/openai-compatible` **2.0.88** and
`@types/bun` **1.3.11**. Python's full resolved graph is in [uv.lock](../uv.lock);
the provider graph is in [bun.lock](../packages/opencode-vibe-oidc/bun.lock).

| Captured input/artifact | SHA256 |
| --- | --- |
| `uv.lock` | `b84441e96431f9077d01b09dfdf46446b0098258983548843b5715c212c46a46` |
| Provider `bun.lock` | `9b31a4ab7df029ee6893f70c47baccc23174348c0ec4f3af0af37d28a5e708c4` |
| Tested `opencode.exe` | `b54a65ca290ace6770d2aaa02df9319b98886c242a7fa0b68d2c45439e7862b4` |
| `mvibe_auth-0.1.0-py3-none-any.whl` | `e74ea9d858f2bb5ef25254cd878773fb2f7bf436ecf99836e9d6713535433d4b` |
| `mvibe_auth-0.1.0.tar.gz` | `12a2033764835736741dd8e629d9d77e7574acba5e142895717c1cbe738d3f9f` |
| Provider `dist/index.js` | `540594185fa44a89b4d6bdf978225d8582e4fc0c8dae8bb0f3b559c56ce7dcab` |

## Source closure and external assumptions

All eight required mapped documentary paths existed during acceptance. Seven were
tracked at the tested commit; `.kiro/specs/vibe-oidc-proxy/verification-plan.md`
was present but untracked and must be captured for clean-checkout acceptance.
The controller was notified. Supporting formal handoff files also awaiting capture
were `brief.md`, `research.md`, `spec.json`, `design-validation.md` and
`task-plan-validation.md` in that specification directory. This run does not prove
that the tested commit alone contains those files.

The four immutable baseline inputs (`LICENSE.vibe`, `_credentials_port.py`,
`_http.py`, `concurrency.py`), requirements and current-state record were tracked.
The [provenance guide](vibe-provenance.md) retains their hashes, source references,
license attribution and implementation deviations.

Verified host compatibility applies to the observed native **2.2.5** binary and
SDK **2.2.5** under synthetic boundaries. User-reported **2.2.6** and later automatic
updates remain unverified. Real issuer registration, redirect acceptance, tenant
schemas, subscription/model entitlements, inference and refresh remain unverified;
follow the [separate live procedure](live-verification.md). External Vibe does not
participate in this library's locks, so concurrent external writes/re-login remain
best effort. Local health establishes liveness only. The [live record](live-results.md)
continues to distinguish unexercised scenarios from entitlement limitations and
established defects.
