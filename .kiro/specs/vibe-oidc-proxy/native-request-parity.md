# Native Vibe OIDC request parity

User explicitly requested on 2026-11-01 that the OpenCode plugin handle the same
authentication, User-Agent and metadata as `../mistral-vibe`. This corrects an
incomplete implementation of the original Vibe-flow requirement. Source:
`D:/Source/mistral-vibe`, commit `338ad4bf0f73c4dd879d667cf7ab011555ae1751`,
version 3.0.4. Do not modify that reference repository.

## Acceptance

1. Keep Vibe OIDC ID-token and tenant resolution unchanged. Send the exact native
   Mistral User-Agent, rather than the temporary adapter-suffixed variant.
2. Add native completion-body metadata using the sibling's actual builder and
   serialization rules: string values, `call_source=vibe_code`, `host_kind=local`,
   platform/architecture/OS/native version and correctly classified call type.
   Map actual OpenCode client/session/message/purpose context; omit unknown
   optional fields, including plan labels. Do not hardcode synthetic identities.
3. Keep metadata construction in shared Python code for reuse by the plugin and
   proxy. The plugin obtains per-request metadata through the existing helper;
   no second auth or native-policy implementation in TypeScript.
4. Preserve prompts, tool schemas/calls/results, reasoning, unrelated settings
   and caller metadata outside explicitly owned fields. Other routes remain
   passthrough. Never forward private bridge-context headers upstream, replay
   inference or log context IDs/payloads. Metadata attachment is the explicit
   native-compatibility exception to otherwise raw chat request forwarding.
5. Compare deterministic serialization to the sibling's actual native builder,
   capture final outbound requests and test concurrent session isolation,
   auxiliary calls and tool continuations through the real OpenCode host.
   Offline parity and real quota-counter outcomes remain distinct evidence.

## Implementation boundary

Add a pure native request-context/metadata module beside the shared auth API,
without adding inference body handling to AuthSession. Extend private resolve
parameters with optional bounded request context and include metadata in its
result only when requested. Existing resolve callers keep their original shape.
Update the canonical protocol schema/types/tests together. Native header policy
is still library-owned. Session hooks supply context through a private header,
removed by authenticated fetch before transport; avoid mutable global context.
Non-session requests omit unavailable session/message fields as native permits.
The fetch boundary merges metadata after SDK serialization/reasoning adaptation.
Proxy chat forwarding uses the same pure builder; response streaming is unchanged.

## Task

- [x] Implement and independently review the complete native request contract,
  with a golden reference and real host request-capture evidence.

Release publication remains held until the user's subsequent quota check.

## Implementation notes

The native host capture gates the first response until both sessions reach
inference, verifies separate nonempty message/session context, executes the real
native `read` tool in both sessions, and verifies both continuation requests.
Prompts are admitted after observing the preceding request to avoid an observed
OpenCode 2.2.5 simultaneous-wake stall; responses still overlap. The precise
upstream scheduling defect is unproven. A real `session.generate` call covers the
auxiliary request route. No live account requests are made by these tests.

Absent or null caller metadata receives native defaults. Invalid scalar/array
metadata remains invalid-input passthrough. Builder-produced keys are owned;
other caller metadata is preserved, including optional fields the builder cannot
derive. The implementation never invents an account plan.

## Review and verification

Independent Kiro review: APPROVED. The reviewer independently verified the
native golden commit and five source hashes, reran lint/type checks and the
strengthened actual-host test, and confirmed all 13 host checks passed,
including the auxiliary route. The null-metadata regression was found during
review and fixed in both adapters before approval.

Dagger `check` passed with 1,073 Python and 1,006 Bun tests. Final native-host
review evidence: `reports/native-parity-host-review-final.json`. The installed
checkout was rebuilt and its OpenCode background service restarted afterward.
Quota attribution remains pending the user's real account counter observation.

The final canonical offline runner also passed all 11 mandatory stages,
including native query/mutation/command and MCP baselines:
`reports/verification/f6945e553a3a4c34a294e40c949a29d0/acceptance.json`.

## Account-context follow-up (2026-11-01)

The user retested the installed build and still observed API allowance usage.
The user confirmed their plan is Pro. A read-only native OIDC
`https://console.mistral.ai/api/vibe/account-context` lookup returned HTTP 200,
`plan_type=CHAT`, `plan_name=INDIVIDUAL`, paid Vibe entitlement true and an API
base matching the configured audience. No inference was sent. Native Vibe maps
this real account context to `user_plan=Pro`; our builder previously omitted
the optional field because no lookup existed. This is a remaining metadata gap,
not an established explanation of billing.

- [x] Resolve optional account-derived plan metadata in shared Python code for
  both adapters, with bounded requests and account/tenant-isolated caching.
  Preserve OIDC-only authentication, destination guards, no redirects/replays,
  cancellation and secrecy. Use native plan mapping, omit unknown/unavailable
  plans, never accept plan labels from private request context. Test real-shaped
  synthetic account responses and both final outbound adapter requests.

This follow-up makes `user_plan` explicitly server-owned: remove caller-supplied
values before merging shared helper metadata. A failed/unknown lookup therefore
omits this field even if caller metadata supplied a label. Other caller metadata
remains preserved. TypeScript only enforces field ownership; all lookup and plan
mapping remain shared Python policy.

Boundary: a shared optional account-context resolver beside native metadata;
keep AuthSession free of inference-body handling. Derive the account URL only
from the configured OIDC registration's trusted console origin, never response
URLs or caller context. Use the already-resolved snapshot, cache by its revision
and tenant, bound/validate response JSON, coalesce concurrent lookups and omit
plan metadata on account endpoint failure as native does. Dispose owned clients
and avoid introducing per-call account traffic or stale cross-account plans.
Independent review and a final host capture remain required. Publication is
still held; the user's observed billing outcome supersedes any inference from
offline parity tests.

Account-context implementation independently APPROVED. Final Windows and Dagger
Linux suites each passed all 1,120 Python tests; Bun passed 1,008 tests. Static
checks passed. The canonical pinned host passed all 13 checks with account-derived
plan metadata, tool continuations and auxiliary requests:
`reports/account-plan-host-final.json`. An initial host activation failure did
not recur after the concurrent full suite finished; its cause is not established.
An existing Windows credential-process test initially missed its five-second
startup deadline, then passed alone and in the final full rerun without test or
timeout changes. Original RED evidence: `reports/account-plan-red-evidence.txt`.

The linked local plugin was rebuilt and `opencode service restart` succeeded.
A capture through the built provider and configured real helper verified
lookup-derived `Pro`, overriding a wrong caller label, with inference intercepted
in memory: `reports/installed-plan-capture.json`. No live model request was sent.
The user's next quota-counter observation remains required before publication.
