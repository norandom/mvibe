# Live verification evidence

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
