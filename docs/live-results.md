# Live verification evidence

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
