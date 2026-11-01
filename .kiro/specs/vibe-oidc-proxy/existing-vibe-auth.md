# Approved working Vibe authentication integration

User approval follows captured native Vibe quota success. Implement the working
existing native credential source in the shared library, direct provider and
optional proxy mode. Experimental ID-token OIDC remains explicit and separate.

Requirements/design: read only native keyring service `ai.mistral.vibe`, entry
`MISTRAL_API_KEY`, freshly for each request. Do not read environment API keys,
import ID tokens, modify native credentials, or claim keyring origin proves a
browser-issued credential or billing entitlement. Missing credentials instruct
the user to sign in through normal Vibe. Credential renewal remains native;
re-reading observes native replacement. Restrict this source to trusted Mistral
inference destinations; omit OIDC external tenant headers. Reuse native Vibe
User-Agent and existing metadata builder. Keep OpenCode identity truthful.

Expose a reusable ExistingVibeSession with safe status/resolve/close semantics,
opaque snapshot kind, bounded snapshot lifetime and non-secret revision. Extend
the bounded account-plan resolver to native `/api/vibe/whoami` for this kind,
preserving `/api/vibe/account-context` for ID tokens; never hardcode Pro.
Bridge v1 shape remains unchanged, with explicit `bridge-vibe` / `login-vibe`
entry points. login-vibe verifies existing credentials and emits the existing
OpenCode connection marker; missing credentials give actionable native-sign-in
instructions, never launch experimental OIDC or accept manual API keys.

Provider option `authSource: existing-vibe` selects those helper commands;
absent option retains compatibility. Installers recommend this explicit mode
for new installs; local configuration is updated after verification. Do not
rewrite the chosen model alias to explain billing: native `mistral-large-4-0`
versus OpenCode `mistral-large-4` remains a comparison detail.

- [x] Shared Python session, bridge/CLI, plan lookup and optional proxy source;
  focused tests and independent review.
- [x] Provider and installer integration, docs and focused tests; independent
  review, local build/install and service restart.

Use offline synthetic tests for source isolation, replacement/missing/error,
destination policy, secret-safe diagnostics, lifecycle/cancellation and plan
endpoint selection. Verify the built direct provider with actual configured
helper and a synthetic inference transport; no paid inference. Actual quota
confirmation is the user's final live check. Release remains held until then.

CI repair discovered during integration: the earlier diagnostic portable-store
contract tests loaded a machine-specific Windows Vibe installation and failed
on Linux with missing APPDATA. Replace that dependency with a pinned native
source test fixture, retaining provenance and license, so all platforms exercise
the same contract without a local Vibe installation or skipped coverage.

## Verification — 2026-11-01

- Independent Python and TypeScript reviews APPROVED. Windows full Python suite
  passed 1,160 tests; the subsequent fixture repair passed its 21-test focused
  suite and independent review. Full Bun suite passed 1,032 tests. Ruff, ESLint,
  TypeScript type checking and production build passed.
- WSL Dagger Linux check exited zero: 1,161 Python and 1,032 Bun tests, with
  static/build checks. Evidence: `reports/existing-vibe-dagger.log`.
- Pinned OpenCode 2.2.5 consumer integration passed with synthetic transport,
  including streaming, tools, cancellation and no replay. This verifies host
  compatibility, not live native credential billing.
- Built provider + actual `bridge-vibe` helper passed an intercepted inference
  check: lookup-derived Pro, Vibe User-Agent, no external tenant headers, no
  inference sent. Only credential reads and the account lookup were live.
- Existing local JSONC backed up, only `authSource=existing-vibe` changed;
  linked plugin rebuilt and official OpenCode service restart exited zero.
- Actual OpenCode quota attribution remains unverified until the user counter
  check. No release was published. Existing catalog warnings can still mention
  OIDC generically; source-specific request authentication/401 guidance is fixed.
