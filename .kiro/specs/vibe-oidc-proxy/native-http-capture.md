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
