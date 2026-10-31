# Vibe source provenance

Inspected on **2026-10-31**. The original repository identifies the copied
authentication/HTTP helpers as **Mistral Vibe 3.0.4**. This is a repository
provenance assertion: no independently obtained upstream source tree was compared
byte for byte. The immutable hashes below identify the actual examined repository
inputs, not a verified upstream release. The historical
[current-state](../.kiro/specs/vibe-oidc-proxy/current-state.md) and
[research baseline](../.kiro/specs/vibe-oidc-proxy/research.md#source-baseline-sha256)
capture pre-implementation inspection; descriptions there are historical.

`mvibe-auth` distribution/public API **0.1.0** is independent of Vibe **3.0.4**.
The private adapter protocol is **1**, and the provider accepts API major **0**.
The original HTTP transport version remains unchanged. Preserve
[LICENSE.vibe](../LICENSE.vibe), Apache License 2.0, with derived source and
distribution. This project retains attribution to Mistral Vibe; packaging a new
facade does not change the origin or license of copied code.

## Code-observed flows in the implemented checkout

| Flow | Source and observed contract |
| --- | --- |
| Sign-in | [api.py](../mvibe_auth/api.py), [_oidc_service.py](../mvibe_auth/_oidc_service.py), [_oidc_callback.py](../mvibe_auth/_oidc_callback.py), [login_cli.py](../mvibe_auth/login_cli.py): captured config, snapshot before browser, discovery and PKCE/state/nonce, callback listening before URL, conditional commit, stable completed/reused-current result. Proxy/login CLI own browser presentation. |
| Validation | [_oidc_client.py](../mvibe_auth/_oidc_client.py), [_oidc_config.py](../mvibe_auth/_oidc_config.py): exact discovery issuer and endpoint policy, Authlib/joserfc signature/claims/JWKS validation, nonce/client audience/subject checks, finite JWT dates and refresh subject continuity. Inference uses ID token; [_oidc_platform_credentials.py](../mvibe_auth/_oidc_platform_credentials.py) uses access token for UserInfo. |
| Storage | [_oidc_credentials.py](../mvibe_auth/_oidc_credentials.py), [_coordination.py](../mvibe_auth/_coordination.py), [_credentials_port.py](../mvibe_auth/_credentials_port.py): portable Fernet-encrypted file and short OS-keyring key, with one-way legacy import from `ai.mistral.vibe.oidc` / `cli-oidc:v1:tokens`. This user-authorized post-live amendment replaces Vibe-slot-only persistence. Unreadable differs from absent, conditional writes compare token revision, native lock precedes slot RLock. Blocking workers drain before lock release. |
| Refresh | [_oidc_service.py](../mvibe_auth/_oidc_service.py), [_coordination.py](../mvibe_auth/_coordination.py): authoritative reload per resolution, more than 30 seconds freshness for access/required ID token, one shared conflict retry, omitted fresh ID/refresh token retained, rejected unchanged refresh never retried. |
| Tenant | [_oidc_runtime.py](../mvibe_auth/_oidc_runtime.py): organization override or exact/first workspace using paginated `/v1/users/me/workspaces`; tenant binds current token revision and selection; recheck after discovery, reject empty IDs, tolerate tenant persistence failure only with current usable credentials. |
| Forward | [proxy.py](../proxy.py), [http.py](../mvibe_auth/http.py), [provider.ts](../packages/opencode-vibe-oidc/src/provider.ts): approved audience/route before credential attachment, caller auth discarded, raw bodies and repeated query values preserved, no redirect/replay. TLS/proxy/NO_PROXY policy remains in HTTP helpers; runtime resolver is injected. |
| Cleanup | [api.py](../mvibe_auth/api.py), [streaming.py](../mvibe_proxy/streaming.py), [bridge.py](../mvibe_auth/bridge.py), [bridge.ts](../packages/opencode-vibe-oidc/src/bridge.ts), [windows-job.ts](../packages/opencode-vibe-oidc/src/windows-job.ts): owned callbacks/workers drain, terminal bridge writers complete before shutdown, bounded response/client close and retirement on failed cleanup, owned helper tree termination before replacement. |

## Evidence categories and discrepancies

**Code-observed** means source inspection plus the named offline checks in
`tests/traceability.json`, not live service success. **Intended** means the
approved requirements/design; an observation does not automatically establish
compliance. **Proven defects in the examined baseline** and their disposition:

- Callback parsing discarded blank values, allowing blank-plus-nonblank duplicate
  required parameters through cardinality checks. `test_oidc_login.py` demonstrates
  rejection with retained blanks; callback parsing was corrected.
- Signed JWT non-finite `exp`, `iat` and `nbf` values were accepted by the
  dependency validation path. Synthetic signed regressions in `test_oidc_login.py`
  proved the discrepancy; a finite-date supplement now follows library validation.
- Initial login could overwrite a newer stored identity, and independent refresh
  writers could clobber rotated credentials/tenant. Conditional login snapshots,
  coordinated refresh and revision-bound tenant tests prove the implemented fix
  for participating consumers. External uncooperative writers remain a limitation.

**Suspected gaps are not compliant observations.** The historical concern that
query conversion might collapse repeated keys was resolved by concrete
`test_streaming.py` multiplicity/order checks and explicit ordered-pair forwarding.
Baseline generator cleanup alone was insufficient evidence of cancellation-safe
ownership; ASGI stream/cleanup checks now cover that path. During implementation,
native host checks exposed incorrect API-major alignment, package prefix,
credential-event handling and host retry after 401/503; these were corrected and
recorded in [consumer-integration.md](consumer-integration.md). A shutdown/EOF
terminal-writer cancellation race produced duplicate replies; deterministic
`test_bridge_dispatch.py` regressions now require writer drain. Neither defect is
described as acceptable behavior.

**Unverified external assumptions:** real issuer registration/redirect acceptance,
subscription entitlement, live workspace schemas, real chat/stream/tools/embeddings,
and live refresh remain unverified. OpenCode/SDK **2.2.5** was exercised with
synthetic boundaries in the actual native host; user-reported **2.2.6** remains
unverified. Current release discovery alone establishes no compatibility.
Offline tests, local startup and live results are separate evidence classes.

## Deviation ledger from the captured inputs

- Local package/import paths, independently versioned installable distribution,
  dependency locks, inert exports and optional proxy dependencies replace the
  source-copy-only arrangement. Original credential JSON and slot are retained.
- Immutable `_auth_config.py` / `_auth_contracts.py` capture configuration once,
  require a usable audience and inject marker resolution into transport instead
  of importing runtime upward. Proxy OIDC defaulting no longer mutates environment.
- Callback blank-duplicate handling and bounded close; signed finite JWT dates
  supplement existing Authlib/joserfc validation. No independent JWT validator.
- Absent/unreadable storage distinction, expected-snapshot conditional save/clear/
  tenant writes, cancellation-safe worker drain and native coordination; bounded
  refresh/tenant conflict cycling replaces process-local cache assumptions.
- Pre-browser login snapshots prevent observed stale login commits; outcomes
  distinguish completed from reused-current. Tenant caches bind revision/selection.
- Public `AuthSession` and immutable safe outcomes expose existing services to
  consumers; bridge/login CLI implement lifecycle and sanitized versioned NDJSON.
- Proxy app factory uses the public facade; ordered query values, response ownership,
  shielded bounded cleanup/client retirement, stream termination and sanitized
  failures are explicit. `mvibe_proxy` is a new packaged helper required by script.
- OpenCode V2 provider is new: command browser login with non-secret host marker,
  managed helper per request, authenticated strict model metadata, custom fetch,
  no redirect/inference replay, cancellation and owned Windows job-tree cleanup.
- Default-deny synthetic tests, installed/script checks, native host evidence,
  executable traceability and offline orchestration are new verification work.

## Immutable examined input hashes

These SHA256 values were captured before implementation on 2026-10-31. They are
not hashes of files after the deviations above. The four unchanged source/license
inputs are required source-capture artifacts even if they were initially untracked:
`LICENSE.vibe`, `mvibe_auth/_credentials_port.py`, `mvibe_auth/_http.py`, and
`mvibe_auth/concurrency.py`. A clean checkout/build must retain all four.

License SHA256: `815AD2F988BC0E55678643CCD41CE6B17F879C7A5A78857E546AF1BC26D0D401`.

| File | SHA256 |
| --- | --- |
| proxy.py | 8D97854E93E4DB1A422FF65CCAC58FBFDBA663EDC5126B8BEB35A05B073E0F54 |
| README.md | 01AE00064472D9BFEC044133DA13977A89092902F07C5F1940F4F96F473BCDC1 |
| mvibe_auth/__init__.py | DCC9DE903F85E0B1DE2E2431E1D0E4BB1674FA06C24ED504EBF6363B602115FC |
| mvibe_auth/_credentials_port.py | CBCA7ACDC66D91C7448A37A81F0F8A02C68ACCF5DECCFC24C3799239CBFE11A7 |
| mvibe_auth/_http.py | CB49FB19FD86165F653634D438B89B7B71E8EE8E43145FF342D8E6BCA5FA61F4 |
| mvibe_auth/_oidc_callback.py | 2A6B54AA88CD8AEFC8BE4CC7BBB36E3773517D944D266D43CFDD58641B36F9A3 |
| mvibe_auth/_oidc_client.py | 976BD9C3FDC74DB0E1687ADBF7E4EFA586CEFACA52A2D8542E2A35CE8E95C277 |
| mvibe_auth/_oidc_config.py | 4B4E87BACBF3D8B11B78426F51E2285D75C874EA5AF8E7588CD5F41196A6F7EF |
| mvibe_auth/_oidc_credentials.py | 451ED14867D0BD52638B1566179A3A5F45E12CDD38F0E21D098AC60A478C2E3A |
| mvibe_auth/_oidc_platform_credentials.py | F64F5813520B98792C3C479CD8932200AAC42812BECCE68BE8FADB5C016C6F24 |
| mvibe_auth/_oidc_runtime.py | 246A972E749192B91DEAAD10D55E99D62A058AFAEA1FFF3A8A8F567045D3AEF0 |
| mvibe_auth/_oidc_service.py | 9C8D4E8DAFF19DD97F52E28AF8AF42F53181E4DF696F76482AF51ADC39673253 |
| mvibe_auth/concurrency.py | FAE2F4CBAA7F571196616FF4AA328AB331035C1285D4C4FBED70B9A436DDAB79 |
| mvibe_auth/http.py | C1C072A977464B235412F8B3338285222CD5A8343BDDD219E02946CDFEE30BEB |
| mvibe_auth/oidc_marker.py | 711A2FDF30008221031D7100995847F331F0A66FEADF73154E6374CBC9BA6EBA |
