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
