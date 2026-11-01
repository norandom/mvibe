# Live verification evidence

## Direct Large 4 file-tool continuation, 2026-11-01

A user-reported HTTP 422 was traced to the SDK replaying assistant history with
`reasoning_content`, rejected as `extra_forbidden`. The direct provider now maps
that reasoning to Mistral thinking chunks without discarding it or changing tools.

An isolated native OpenCode 2.2.5 CLI run selected `vibe-oidc/mistral-large-4`
and reused matching stored Vibe OIDC credentials without opening a browser.
The public test prompt requested one `hello.ps1` file. Observed: one file-tool
event, exact requested file content, a final `DONE` after tool execution, exit 0,
and no 422. Running the checked generated file under PowerShell printed
`Hello, World!`. The safe summary is `reports/live-large4-result.json`.

The test was repeated with `options.diagnostics: true` after the logging change.
It again created the correct file and completed without 422. On native 2.2.5,
adding `--print-logs` exposed five safe `provider_request` events on outer stderr
(models and chat/completions, HTTP 200, `headers_received`). The plugin option
alone did not forward them there. These events establish header acquisition;
the independent CLI/file observations establish the tested continuation outcome.

This establishes the exercised direct file-write and reasoning/tool-result
continuation. It does not establish live MCP, all file/command tools, refresh,
other models, or future OpenCode updates. The older records below retain their
original test dates and scope; the blank template is still not an executed run.

## Authorized live smoke test, 2026-10-31

The first proxy launch received the browser OIDC callback and reached validated
token persistence, then failed in Windows `CredWrite` before application startup.
A separate synthetic keyring probe saved 1,280 characters and failed at 1,281
with error 1783. No token values or callback URLs were retained as evidence.
User-directed task 9.1 replaced full-bundle keyring writes with portable
encrypted-file persistence and a short OS-keyring key. Independent review
approved it after the complete offline pipeline passed: 934 Python tests,
458 Bun tests / 1,432 assertions, all ten mandatory steps. Evidence:
`reports/verification/fd6226fd347d46e5a87b0b65e0d13404/acceptance.json`.

The live retry completed on 2026-10-31 by 14:04 UTC:

| Check | Observed result |
| --- | --- |
| Browser OIDC and persistence | PASS: callback, token validation, encrypted commit, application startup |
| Stored identity reuse | PASS: a new session reported matching credentials, required no browser and returned `reused_current` |
| curl `GET /health` | PASS: HTTP 200, local status `ok` |
| curl `GET /v1/models` | PASS: HTTP 200, 64 returned models; raw inventory not retained |
| curl `POST /v1/chat/completions` | PASS: HTTP 200, `ministral-3b-latest`, public prompt answered `OK` |
| OpenCode CLI through proxy | PASS: native 2.2.5, isolated configuration, `run --standalone --model mistral/ministral-3b-latest`, assistant answered `OK`, exit 0; proxy logged HTTP 200 chat requests |

The client used a non-secret `local` placeholder; upstream authentication was
Vibe OIDC. The fresh PowerShell profile used isolated XDG paths and
`OPENCODE_TEST_HOME`, with automatic update and model fetching disabled for the
test. The proxy retained the normal user's credential store. Python was invoked
through the locked uv project environment; curl.exe reports 8.21.0.

The local native CLI reports OpenCode 2.2.5; the user's separately reported
2.2.6 is not established by that binary's version output. This smoke test proves
the proxy-backed CLI route, not the direct `vibe-oidc` plugin. Live refresh,
embeddings, explicit tool exchange, cancellation and search execution were not
tested. CLI output demonstrates streamed inference interoperability, but no
separate timing or cancellation measurement was taken. Account entitlement is
established only for the observed operations/model. The original blank template
below remains unchanged as a starting point for a complete per-operation run.

## Direct-provider installation and smoke test, 2026-10-31

The PowerShell installer was executed with the prepared native uv 0.13.5,
Bun 1.4.4 and OpenCode 2.2.5 executables. It prepared locked dependencies,
built the direct plugin and updated the user's `opencode.jsonc`. A backup was
created; a read-only comparison confirmed unrelated settings were preserved and
exactly one direct-plugin entry was installed. A subsequent PowerShell install
with `-SkipPrepare` reported configuration unchanged.

| Check | Observed result |
| --- | --- |
| Direct OIDC connection | PASS: `auth login vibe-oidc --method vibe-browser --standalone` connected using existing OIDC credentials |
| Provider label | PASS: native provider API returned `Mistral-Vibe` |
| Model selection | PASS: native CLI listed 18 `vibe-oidc/` model IDs |
| Direct inference | PASS: fresh CLI `run --standalone --model vibe-oidc/ministral-3b-latest` answered `OK`, exit 0 |
| Reinstallation | PASS: no duplicate entry, no config change |

Final independent offline acceptance also passed all ten stages: 934 Python
tests, 492 Bun tests / 1,548 assertions, and all twelve native-host checks.
Evidence: `reports/verification/4e1f12b27eec438999372e15b7dd8b8d/acceptance.json`.
Earlier failed runs were retained. A synthetic rotation assertion was corrected
only after recording a model request that resolved before rotation and reached
transport afterward; inference freshness and captured-snapshot provenance remain
strictly checked.

The direct plugin uses `https://api.mistral.ai` and the shared Python OIDC
library; it does not route through the local mvibe proxy. Tests used an empty
temporary workspace and a public prompt requesting no tools. The temporary
diagnostic plugin was removed before the successful production-plugin test,
and the separate owned OpenCode test server was stopped afterward.

Live checks exposed catalog initialization and refresh races beyond the initial
synthetic coverage. Regressions now cover late host metadata, an initial
discovery superseded by a credential event, same-connection atomic replacement,
and SDK creation without rediscovery. No tool executors or MCP client code were
changed. Native filesystem/command and MCP tool round trips remain unverified;
their separate scope and test matrix are captured in
[the draft compatibility requirements](../.kiro/specs/opencode-vibe-tool-compatibility/requirements.md)
and [verification plan](../.kiro/specs/opencode-vibe-tool-compatibility/verification-plan.md).

## Original task 8.4 template

No live login or inference was performed for task 8.4. The
[structured template](live-evidence-template.json) is a blank record, not a run.
All live scenarios for both the proxy and direct provider are UNVERIFIED:
browser OIDC, credential reuse, tenant selection, models, chat, streaming,
tool exchange, embeddings and refresh. Account entitlement is also unverified.

| Evidence class | Initial live evidence record | Source of actual evidence |
| --- | --- | --- |
| Offline tests | UNVERIFIED in the blank template | Fresh `scripts/verify.ps1` acceptance report; synthetic inputs only |
| Local startup | UNVERIFIED in the blank template | Fresh isolated startup checks described in [consumer integration](consumer-integration.md) |
| Live proxy compatibility | UNVERIFIED | Explicit operator procedure and validated per-operation report required |
| Live direct provider compatibility | UNVERIFIED | Separate explicit operator observations required |

Existing offline and synthetic host results do not populate this live record.
The tested reference host 2.2.5 and user-reported 2.2.6 do not establish live
compatibility for either version. Actual client/provider/runtime versions and
sanitized configuration must accompany any future exercised operation.

Follow [the separate live procedure](live-verification.md). Keep an account
entitlement limitation distinct from an observed failure and from an established
proxy defect. Leave refresh UNVERIFIED unless refresh itself was observed.
Exclude credentials, authorization codes, sign-in URLs and private prompt/account
contents. Validation success means the report has the allowed safe structure;
it is not proof of execution, a secret-scan guarantee or an overall readiness GO.
