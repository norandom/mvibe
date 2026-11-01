# Authentication writeup composition plan

COBESY inputs: repository implementation and captured requests are the raw
knowledge; audience is maintainers and operators; entry points are README,
provider setup and proxy startup. No wider organizational culture is inferred.

Target behavior: choose the existing normal Vibe login, verify the source before
testing quota, and preserve that source when diagnosing requests.
Hidden assumption to correct: browser sign-in, an ID token and a Vibe-issued
credential are interchangeable for billing. The observed counters disprove it.
High-leverage anchor: maintainers updating defaults and reviewers checking them.

Framing: describe the mistaken baseline and the evidence without blaming users.
Treat user counter observations as live evidence, HTTP 200 as transport evidence,
and offline tests as regression evidence. We must revisit the recommendation if
the same existing-login route on a later account/model bills API usage.

Governing idea: reuse the working native Vibe credential through one Python
implementation, while keeping experimental direct OIDC explicit.

| Section | Reader question | Support and cue |
|---|---|---|
| Result and scope | What works? | User-confirmed native and OpenCode quota; proxy live check separate |
| Two browser paths | Why did the earlier approach fail? | Credential exchange versus direct ID token; no header-based billing claim |
| Shared request path | What does the code do? | Python ownership, fresh reads, destinations, plan endpoints, bridge |
| Operator steps | How do I use it? | Existing login, explicit migration, proxy default and opt-in OIDC |
| Diagnosis | How do I find regressions? | Safe logs, opt-in private payload capture, unchanged auth baseline |
| Evidence and limits | What is still unknown? | Versioned tests, counters, macOS test repair, no universal entitlement guarantee |

Composition: technical prose requested explicitly. Lead with the operational
answer, use a comparison table for the two paths, keep commands at the point of
action, and link implementation rather than duplicate API reference text.
Behavioral routing is limited to documentation links and defaults; no rollout
campaign or organizational safety classification is warranted.

Validation: sections cover separate reader decisions; the opening states the
result and limit; the disproof condition is explicit; prose gets a humanizer
pass for hype, generic transitions, decorative punctuation and vague claims.
