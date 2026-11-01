# Quota investigation, 2026-11-01

## Latest outcome

The user retested the installed account-context change (`d3554be`) and again
reported API allowance consumption. The plan lookup did not resolve the quota
issue. Treat this as an unresolved live acceptance failure, not merely an
unperformed check. Do not publish a release claiming Vibe allowance support.

The available client source does not establish the server's billing selector.
Its OIDC implementation is explicitly feature-gated as a CLI experiment.
This is not proof that native OIDC uses API allowance. A confirmed comparison
with native Vibe using the same OIDC registration, account, tenant and model,
or authoritative server-side classification evidence, is still missing.
OpenCode identity fields intentionally differ from native CLI identity;
native cache-affinity headers also differ. Neither is established as a billing
cause. Do not relabel them as a quota fix without evidence.

The user retested installed commit `0b69a29` and reported API allowance usage.
This is not a successful Vibe quota result. Release publication remains held.

## Evidence

- A fresh read-only runtime audit found the configured checkout provider and
  native uv offline/no-sync helper, not another installation. In-memory
  TypeScript emit matched all eight installed JavaScript artifacts. The
  background service started after the build and loaded that provider.
- A read-only query of V2 `session_message` selected only creation timestamps
  and assistant model/provider fields. The latest ten assistant records at
  10:22:09–10:24:13 UTC all select `vibe-oidc/mistral-large-4`; there is no
  evidence those requests switched to the built-in Mistral provider. This
  verifies persisted routing intent, not upstream billing attribution.
- Native source OIDC registration, audience, scopes, ID-token choice and
  tenant headers match the adapter. An offline capture using the native pinned
  Mistral SDK 3.5.0 confirmed the completion endpoint and exact Vibe User-Agent.
  The capture used a synthetic token and an in-memory transport, with no account
  or inference calls. Artifact: `reports/native-sdk-wire-synthetic.json`.
- The user confirmed the plan is Pro. A single read-only native OIDC account
  lookup returned HTTP 200, `plan_type=CHAT`, `plan_name=INDIVIDUAL`, paid Vibe
  entitlement true and an API base matching the configured audience. No
  inference request was made. No tokens or account identifiers were printed.
- Native `vibe/setup/auth/whoami.py` uses `/api/vibe/account-context` for OIDC
  and maps `CHAT` / `INDIVIDUAL` to `Pro`. The previous adapter had no account
  lookup and therefore omitted `user_plan`. Its absence is a metadata gap;
  it has not been established as the billing cause.

## Bounded next step

The shared account-context task is implemented and independently reviewed in
[native-request-parity.md](native-request-parity.md). Query the trusted account
endpoint before attaching a plan, validate the response, reuse bounded cached
results only for the same authenticated identity and tenant, and omit the field
if lookup fails or the plan is unknown. No hardcoded account or plan assumption,
API-key fallback, extra inference, or claimed billing fix. Both final full Python
suites passed 1,120 tests; the plugin suite passed 1,008 tests. The rebuilt local
provider and configured helper produced lookup-derived `Pro` in an intercepted
outgoing request, without live inference. OpenCode service restart succeeded.
Quota attribution still needs a subsequent user counter check.

## Actual OpenCode HTTP capture

The user again reports API costs and requested HTTP payload instrumentation.
The next diagnostic step is opt-in capture at the provider's final fetch
boundary, after auth and metadata adaptation, with correlated original upstream
status and headers. See [http-capture.md](http-capture.md). This records the
application request before TLS; it does not claim packet-level transport headers
or server billing classification. No new inference is sent by instrumentation.
Compare a subsequent user request against the native source/SDK evidence before
changing attribution fields again. Release publication remains held.

### User retest captured — 2026-11-01 11:09–11:10 UTC

The user reports API usage again. Actual local HTTP capture contains 14 request/
response pairs: two model-list requests and 12 completions (one main call, ten
continuations classified secondary, one title generation), all HTTP 200. Every
completion selected `mistral-large-4` and included `user_plan=Pro`,
`call_source=vibe_code`, `host_kind=local`, and exact User-Agent
`mistral-client-python/Mistral-Vibe/3.0.4`. Tenant header pseudonyms were consistent.
This verifies final application request values, not backend quota classification.
No prompts, account identifiers or credentials are included in this report.

Observed differences remain: OpenCode client/entrypoint identities and additional
OpenCode/session headers; native affinity uses `x-affinity`. There is no evidence
that these differences determine billing. Do not change them speculatively.
The existing synthetic native SDK capture does not establish that the official
CLI with this same account/model consumes Vibe quota.

Capture limitation: upstream returned `mistral-correlation-id` and
`x-kong-request-id`, both redacted by the current header allowlist. Existing
captures cannot recover those IDs. A future bounded capture amendment should
preserve those actual server trace headers. HTTP 200 itself carries no recorded
proof of Vibe billing. Root cause remains unresolved; a native CLI comparison
under the same account/model or server-side attribution evidence is required.

## Working native baseline supplied by user

The user reports normal `vibe` uses Vibe quota, while the instrumented launcher
uses API quota. The latter forced experimental CLI OIDC and separate storage;
it was therefore not a neutral native capture. A read-only installed resolver
check returned `oidc_enabled=False` and credential source `keyring`, without
printing the credential. Normal native browser sign-in exchanges a browser
session for a Vibe credential (`api_key` response field), whereas experimental
CLI OIDC forwards an ID token. These are distinct auth paths. Do not assume that
any browser login or direct OIDC token implies the same subscription attribution.

Capture must preserve existing native authentication by default. The user asks
to reuse the working Vibe auth. The existing proxy/provider direct OIDC contract
has not yet been changed; request metadata parity alone is not a demonstrated
quota solution.

## Captured working native quota path — 2026-11-01

The user confirms the corrected default capture launcher consumes Vibe quota.
The captured native completion requests use an opaque bearer credential from
the existing native keyring path, without the OIDC external organization/workspace
headers. They retain the same exact Vibe User-Agent, `call_source=vibe_code`,
`host_kind=local` and `user_plan=Pro`. Native client identity is `vibe_rs`, and
`x-affinity` is present. HTTP responses are 200, with server correlation IDs
preserved privately. No credential or conversation is included in this report.

The working native model identifier is `mistral-large-4-0`; previous OpenCode
captures used `mistral-large-4`. This difference must remain visible in the
comparison rather than asserting credential type alone proves server routing.
The combined source and user-counter evidence establishes a working existing
native authentication path. It does not establish a billing rule for arbitrary
opaque keys or prove the OpenCode provider is fixed. Next integration should
explicitly reuse native Vibe authentication, preserve its credential provenance,
and avoid silent fallback to user-supplied general API keys. Direct ID-token OIDC
must not be described as quota-equivalent on this account.

The user approved the existing-auth integration. It is implemented, independently
reviewed and locally installed with `authSource=existing-vibe`; see
[existing-vibe-auth.md](existing-vibe-auth.md). The actual helper and account
lookup produced Pro in an intercepted outgoing request without inference.
This is a pending live counter check, not a confirmed OpenCode quota fix.
