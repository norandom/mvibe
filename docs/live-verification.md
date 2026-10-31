# Separate live verification

This is a manual operator procedure for requirements 11.1–11.7. Task 8.4 does
not execute it. Running `scripts/verify.ps1` or the default pytest suite never
starts this procedure. `scripts/live_evidence.py` only reads a local JSON file;
it cannot sign in, open a browser, access keyring, launch a client or send requests.
An `--offline` argument to uv prevents dependency downloads; it does **not** make
a production proxy or provider invocation safe from live account activity.

Complete [offline preparation and operator setup](opencode-provider.md) first.
Run the offline runner and review its fresh report separately from
[local startup evidence](consumer-integration.md). Neither proves browser login,
subscription entitlement, live refresh or live inference. Choose to begin the
following procedure explicitly, using an account and harmless synthetic content
that you are authorized to use. Real browser/keyring/network access occurs only
when the operator deliberately starts the production consumer. Stop other Vibe,
proxy and plugin re-login activity: they share one credential slot. Do not clear,
export or edit that slot for these checks.

## Prepare an evidence copy

Copy `docs/live-evidence-template.json` to a new local JSON report. Keep the
checked-in template untouched. The validator is the executable schema; it accepts
exactly the fields and enum values described below, rejects additional keys at
every object boundary, duplicate JSON keys, oversized input and inconsistent
results, and never echoes rejected input. It has no live execution flag:

```powershell
uv run --locked --offline scripts/live_evidence.py path/to/local-report.json
```

Exit 0 means valid report structure, including an entirely UNVERIFIED report.
It is not a live pass, authenticity check, secret scanner or readiness verdict.
No free-form notes, evidence URLs, headers, bodies or logs are accepted. Never
encode private content into otherwise valid version numbers or timestamps.
The validator cannot detect deliberate covert encoding or establish the truth
of an observation; the operator remains responsible for accurate safe recording.

Record actual versions in `consumers.proxy` and `consumers.direct_provider` before
marking an operation exercised. `versions` contains numeric `major.minor.patch`
strings for `client` (the actual calling client), `provider` (mvibe-auth for the
proxy; plugin package for direct), `python` (the uv-managed runtime), `host`
(OpenCode) and `bun`. Host/Bun may be null for a proxy-only client; direct provider
requires all five. Record installed versions, not a user report or package latest.
Pre-release or nonnumeric versions are outside schema 1: leave the affected
consumer UNVERIFIED until an explicit schema revision supports them. Do not
substitute a stable version for a different runtime.

`configuration` deliberately records categories instead of raw values:

| Field | Allowed values |
| --- | --- |
| `issuer`, `client`, `audience` | `default`, `custom` |
| `tenant` | `automatic`, `organization_override`, `workspace_override`, `both_overrides` |
| `callback_port` | Actual loopback callback port, integer 1–65535 |
| `transport` | `default`, `custom_ca`, `proxy`, `proxy_custom_ca` |
| `model_selection` | `account_catalog`, `none` |

Inspect the actual configuration locally to confirm matching audience, callback,
scope, issuer and tenant selections; record only the categories. Exclude account,
organization/workspace identifiers, filesystem paths, proxy credentials, model
names with private account data and raw environment/configuration dumps. If a
configuration cannot be represented accurately, leave it UNVERIFIED rather than
adding private free text. A report describes one version/configuration snapshot
per consumer; use a separate report if either changes.

## Exercise each consumer separately

For the **proxy**, deliberately start `uv run --locked --offline proxy.py --login`
from the prepared checkout for a fresh browser OIDC flow. Its default loopback
base is `http://127.0.0.1:8765/v1`. Use an OpenAI-compatible client with a placeholder
`apiKey` such as `local` if required; this is not an upstream credential. Local
caller authentication is absent. `/health` is local liveness only.

For the **direct provider**, configure the prepared plugin/helper according to
the [operator guide](opencode-provider.md), then select **Vibe OIDC** and its
browser connection method in OpenCode. This connects directly through the plugin,
not through the proxy. The host's `vibe-oidc-connected` marker is bookkeeping;
real credentials remain in the Python library. Use the real host/client versions
for this run, including an auto-updated OpenCode 2 version if applicable.

Perform this sequence for each route and update its nine operation rows only
after observation. Inspect responses privately; do not save raw response bodies.

1. **browser_oidc**: complete browser sign-in and callback through the selected
   consumer. Confirm it reports completion or explicitly reused-current identity.
   A browser-selected account is not proof that concurrent storage was replaced.
   Do not copy the authorization URL, callback URL, code, state or tokens.
2. **credential_reuse**: close the consumer cleanly and restart/reconnect normally
   without forced login. Confirm a supported request succeeds using stored identity
   and no new browser opens. A marker alone does not prove credential reuse.
3. **tenant_selection**: use automatic selection or intentionally configured
   organization/workspace overrides. Confirm locally that the selected tenant is
   the intended accessible tenant; record only the selection category. If tenant
   correctness cannot be observed safely, retain UNVERIFIED.
4. **models**: request authenticated `GET /v1/models` through the proxy or invoke
   the direct provider's authenticated model discovery. Select a returned model
   with verified/operator metadata; do not invent model IDs or capabilities.
   An empty catalog or missing metadata needs investigation, not a fabricated pass.
5. **chat**: send a minimal nonstreaming chat using the selected exact model ID,
   with public test text such as `Reply with OK.` Confirm successful status and
   a usable response privately; retain only the operation outcome.
6. **streaming**: send the same harmless chat with streaming enabled; confirm data
   arrives incrementally before completion and completes cleanly. Buffered output
   is insufficient evidence. Cancel a separate stream and observe termination
   if supported; the schema records the primary streaming scenario only.
7. **tool_exchange**: provide a harmless local tool `echo_number` with one numeric
   argument. Request its use, observe the tool call, return the numeric result
   with its matching call ID, and observe the final model response. Do not run
   shell/network tools or use private documents. Merely receiving a tool-shaped
   response is insufficient for a complete exchange.
8. **embeddings**: if entitled, send public text `hello` using an actual embedding
   model to `POST /v1/embeddings` through the proxy. For the direct route use the
   plugin-backed SDK/client embedding interface only if that host exposes one;
   verify it really traverses the plugin's authenticated fetch. A proxy request
   cannot stand in for direct provider evidence. If no direct client interface is
   available, leave direct embeddings UNVERIFIED; a missing host interface is not
   an account entitlement limitation or an established proxy defect.
9. **refresh**: only when practical, keep the consumer open until its normal
   freshness threshold and repeat a harmless supported operation. Mark PASS only
   with safe independent observation that refresh occurred and the subsequent
   request succeeded. A second successful request alone could reuse a fresh token.
   Never dump/decode tokens, alter expiry or edit keyring to force refresh. If
   refresh cannot be established without exposing credentials, retain UNVERIFIED.

Cleanly close each consumer and its owned helper before moving to the other.
Do not infer parity or a direct provider pass from proxy results.

## Record only safe outcomes

Every row requires `consumer` (`proxy` or `direct_provider`), one of the nine
operation codes above, `status`, `reason` and `observed_at` (UTC
`YYYY-MM-DDTHH:MM:SSZ`). All 18 unique consumer/operation pairs are mandatory.

| Status | Allowed reason | Meaning |
| --- | --- | --- |
| `UNVERIFIED` | `not_exercised` | Not established; timestamp must be null |
| `PASS` | `observed` | Operator observed that complete scenario |
| `ENTITLEMENT_LIMITATION` | `model_unavailable`, `capability_unavailable` | Account limitation independently established |
| `FAIL` | `authentication_failed`, `service_error`, `client_error`, `unknown_failure` | Observed failure, cause not automatically an established defect |

Non-UNVERIFIED rows require actual versions, categorized configuration and a
timestamp. A skipped, inconclusive or unavailable test remains UNVERIFIED. Do not
label every 401/403, missing model, incomplete metadata or unsupported client UI
as entitlement: confirm the account restriction first. An entitlement outcome
does not pass the scenario and does not establish a proxy defect. Investigate a
suspected defect separately using synthetic reproductions and sanitized findings.

Exclude real tokens (including access/ID/refresh tokens), authorization codes,
transient sign-in URLs, raw errors, private prompts, responses, account contents,
screenshots, terminal transcripts and credential-store data from all artifacts.
Do not add such content as notes or attach it next to the report. Unknown fields
and arbitrary text are rejected rather than redacted. If private data was captured
accidentally, remove the unsafe artifact; do not commit it as evidence.

Maintain `offline` and `local_startup` status/timestamp separately, based on their
actual fresh reports. They never populate any live row. Keep untouched refresh
and all other unexercised scenarios UNVERIFIED even after offline tests pass.
Publish only a validated sanitized report with a reviewed human summary, keeping
offline results, local startup and per-consumer live compatibility separate as in
[the initial evidence record](live-results.md). No combined readiness GO is
produced by this validator.
