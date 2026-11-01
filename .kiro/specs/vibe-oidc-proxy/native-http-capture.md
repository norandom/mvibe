# Native Vibe comparison capture

- [x] Provide and independently review a temporary launcher for the installed
  official Vibe CLI with final HTTP request capture in its app-server process.

The user needs native request evidence for the unresolved API-cost issue.
Use the installed uv-managed Vibe runtime, preserving native credentials,
configuration and model selection. Instrument only the inference HTTP boundary;
no API-key substitution, modified sibling checkout, inferred billing fix, extra
inference, response-body reads or transport retries. Capture bounded JSON and
original response status/trace headers privately with credential redaction,
tenant pseudonyms and explicit limits. Failures in capture must preserve normal
transport behavior. Supply a PowerShell launch command and offline tests with
synthetic transport, including streamed-response preservation. The user sends
the actual comparison prompt and observes quota attribution.

Verification 2026-11-01: independent Kiro review APPROVED; ten focused offline
tests and Ruff passed. Installed uv Vibe 3.0.4 Python ran a synthetic transport
smoke after the native-style auth hook, with actual organization/workspace
headers redacted and model token limits preserved. Zero network calls.
PowerShell wrapper passed actual installed Rust CLI `--help`. Windows directory
and file ACL inspection showed current user, SYSTEM, Administrators and OWNER
RIGHTS, without general-user access. Documentation reflects those permissions.
No live native inference or quota result is claimed.

## Login diagnostic amendment

- [x] Preserve capture across native app-server restarts with exclusive private
  per-process child directories. Record safe exception class chains and a
  certificate-verification-failure boolean for failed auth-host HTTP requests,
  without login payloads, URLs, exception text, claims or credentials. Include
  only the exception type in capture initialization warnings. Preserve original
  exceptions and transport behavior; do not weaken TLS or change persistent auth.

The user reports login failure, possibly SSL. Public discovery GETs from the
installed native runtime returned 200 with both certificate stores. Existing
native logs show capture initialization failure, but no specific login failure.
No SSL root cause is established yet.

The user supplied the generic issuer/registration/keyring error. Include safe
exception-type diagnostics for failed native `keyring.set_password` calls,
without examining or recording arguments or changing storage behavior. Previous
Windows CredWrite failure is recorded in implementation-log.md; it is a relevant
hypothesis, not proof of this failure.

Amendment verification: independent review APPROVED; 16 focused offline tests
and Ruff passed. Installed uv Python synthetic capture smoke and PowerShell
native `--version` passed. No inference or keyring writes performed. The user
must retry native sign-in to produce failure evidence; login is not claimed fixed.

## Native Windows persistence workaround

The retry produced `auth_storage_failure.exception_types=["error"]` during
native keyring writes, with no recorded auth HTTP transport failure. Native
Vibe directly saves its full token bundle to Windows Credential Manager.

- [x] Enable isolated portable persistence for the diagnostic native launch,
  reusing the existing shared encrypted store with a separate native comparison
  namespace, file, encryption key and native lock. Do not import or mutate
  OpenCode credentials or the native legacy credential slot. Native browser
  OAuth, token validation, refresh and HTTP generation remain native; only the
  persistence adapter changes. Convert token schemas explicitly, preserve CAS
  and cancellation behavior, and fail safely on incompatible schemas or storage
  errors. Test large bundles and isolation offline; independently review.

The installed native runtime needed filelock for reuse of the shared store.
Installed `filelock==3.20.3` using uv into that existing uv-managed tool runtime;
no other package change was requested. This workaround changes the diagnostic
storage baseline and must be identified in the comparison evidence.

Verification: independent Kiro review APPROVED; 18 focused offline tests and
Ruff passed. Tests cover large token
bundles with a simulated small keyring limit, isolated slots, lifecycle, stale
CAS, tenant semantics and cancellation. Installed native runtime smoke from a
foreign working directory initialized an empty encrypted native comparison
store and short Windows keyring key, then loaded the signed-out state. The
guard refused to initialize over an existing file. No browser login or inference
was performed. The PowerShell launcher enables portable storage automatically;
the installed native Vibe source and OpenCode credentials remain unchanged.
