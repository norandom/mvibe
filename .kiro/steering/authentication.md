# Preserve the working native Vibe authentication path

Updated: 2026-11-01 after user confirmation of correct OpenCode Vibe quota usage.

## Decision

Use `ExistingVibeSession` for normal provider/proxy operation. It rereads native
service `ai.mistral.vibe`, entry `MISTRAL_API_KEY`, on each request. Normal Vibe
owns browser sign-in and renewal. The shared session performs no credential
writes, legacy migration, environment API-key fallback or implicit OIDC login.
macOS reads through the native security command; other platforms use keyring.

Provider `authSource: existing-vibe` selects `bridge-vibe` / `login-vibe`.
New installs select it; existing configurations preserve their previous choice
until explicitly migrated. The stable provider ID is still `vibe-oidc`.
The proxy defaults to the same source; `--auth-source oidc` is explicit.

## Two browser flows are not interchangeable

Normal Vibe browser sign-in exchanges through `/vibe/sign-in/.../exchange` and
receives a credential in an `api_key` response field. Experimental CLI OIDC
obtains an ID token and forwards it with external tenant headers. On the tested
account, the first path consumed Vibe quota and the second consumed API usage.
Do not equate browser interaction, token shape or field naming with billing.

The user confirmed the existing-login OpenCode integration following `6fd0853`
works and uses the correct quota. Earlier statements that this check was pending
are historical. This is not a universal quota guarantee or separate live proxy
acceptance. Keep user counter observations distinct from mocked transport tests.

## Boundaries and diagnostic lessons

Existing-login credentials go only to the three allowed HTTPS Mistral inference
paths. They do not inherit OIDC external organization/workspace headers. Account
metadata uses `/api/vibe/whoami`; ID-token mode uses `/api/vibe/account-context`.
Never hardcode the user's Pro plan or treat metadata as a billing selector.

A snapshot's 60-second lifetime bounds local request use; it does not establish
opaque credential expiry. Native credential replacement is picked up on the next
request. Fail safely and direct the user to normal Vibe for reauthentication.

Preserve native auth, environment and model when instrumenting a comparison.
An earlier launcher forced experimental OIDC and separate storage, invalidating
its comparison with normal Vibe. Windows full-bundle keyring writes also failed;
the isolated portable diagnostic workaround is not the recommended default auth.
Do not revive either workaround implicitly.

Ordinary diagnostics contain no credentials, request/response bodies or raw
exception messages. Payload capture is separate, private, bounded and opt-in;
arbitrary secrets in conversations may survive redaction. Never commit captures.
Do not use headers or a successful HTTP response as a substitute for quota checks.

See [the technical writeup](../../docs/vibe-authentication.md) and the
[dated investigation](../specs/vibe-oidc-proxy/quota-investigation.md).
