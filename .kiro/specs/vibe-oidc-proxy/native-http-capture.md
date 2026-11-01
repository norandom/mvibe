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
