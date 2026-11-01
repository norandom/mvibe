# Opt-in HTTP capture for quota diagnosis

The user requested packet-payload visibility or equivalent HTTP instrumentation
for actual OpenCode requests. Implement a local capture immediately before the
provider transport, after auth and body adaptation, plus correlated upstream
status and response headers before local response-header changes.

- [x] Add and independently review opt-in bounded capture, install locally and
  enable it for the user's next OpenCode request.

Capture is off by default and independent of ordinary safe diagnostics. An
explicit absolute capture directory enables request/response-header records.
An additional explicit body flag permits final outgoing JSON payloads, including
conversation/tool content, for this diagnostic session. Never capture bearer
tokens, API keys, cookies or raw tenant identifiers. Header allowlists preserve
useful client identity and upstream request IDs; redact unknown header values.
Pseudonymize tenant identifiers for correlation. Redact credential-like JSON
fields and known resolved credential values; document that arbitrary prompt
secrets cannot be reliably recognized. Keep files private, bounded, exclusive,
local and ignored by Git. Limit records/session size; indicate skipped/truncated
captures explicitly. Capture failure must not replay or change inference.

Do not buffer response SSE or change response-body ownership. Response capture
means original upstream status/headers, not generated content. Preserve
cancellation, no-replay and redirect controls. Add focused tests for final
outgoing values, secret redaction, disabled behavior, limits, write failures and
stream/cancellation preservation. Document how to inspect and disable capture.
Use a synthetic transport for verification; root enables capture and restarts
OpenCode only after review. No additional paid inference is implied.

## Verification — 2026-11-01

- Independent Kiro review: APPROVED after regression tests exposed and fixed
  plain token/bearer JSON fields and unmarked shortened header values.
- Final plugin suite: 1,016 passed, zero failed; 2,898 assertions. Capture suite:
  eight passed, 47 assertions. ESLint, TypeScript noEmit and production build
  exited zero.
- Real Windows filesystem smoke using built capture module: two correlated
  files, resolved credentials absent, request ID present, zero network calls.
  Session ACL inspection showed only current-user access.
- Local linked plugin rebuilt. Existing JSONC backed up and only capture options
  enabled: `captureDirectory=D:/Source/mvibe/reports/http-capture`,
  `captureBody=true`. Official OpenCode service restart exited zero.
- Capture stores application requests before TLS, not packet-level headers
  added by the HTTP transport. Response bodies remain unread. Actual user
  inference capture and quota diagnosis are pending; API-cost symptom is not
  resolved and release remains held.
