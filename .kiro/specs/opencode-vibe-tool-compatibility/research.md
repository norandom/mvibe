# Research and design decisions

## Summary
- Feature: opencode-vibe-tool-compatibility.
- Discovery: extension of the installed direct provider, with focused source/API
  investigation and comprehensive primary protocol checks for tool results.
- Requirements and design: user approved the expanded six groups, 33 criteria
  and technical design. The task plan is generated and independently reviewed,
  pending human approval; no implementation or live tool execution was performed.
- Findings: existing hooks can observe the full round trip; registry and session
  offerings differ; the installed V3 serializer has error/media limitations;
  Mistral constraints vary by tokenizer/model; profile isolation needs more than
  OPENCODE_TEST_HOME.
- Core product.md, tech.md and structure.md steering files are absent. Existing
  specification, code and AGENTS.md provide context; steering alignment is not
  independently asserted.

## Research Log

### Installed host and provider boundaries
Sources: packages/opencode-vibe-oidc/src/index.ts, src/provider.ts,
node_modules/@opencode/plugin/dist/promise/session.d.ts, tool.d.ts, aisdk.d.ts,
mcp.d.ts and websearch.d.ts; package.json and current native host fixtures.

Installed reference is OpenCode/plugin 2.2.5, OpenAI-compatible SDK 2.0.88,
@ai-sdk/provider 3.0.19, Bun 1.4.4, TypeScript 5.9.3. Runtime package versions
are evidence inputs, not assertions about all current or future hosts.

The provider registers identity/discovery/custom SDK and guarded current-auth
fetch. It owns no tool executor. Public hooks expose semantic context with
effective definitions, HTTP request/response with request kind, tool execute
before/after, and session/form/child events. They support fixture-only observers
without replacing tools. Their ordering and coverage for custom SDK requests
must be demonstrated by a capability probe before acceptance.

The existing synthetic_echo host test replaces offered tools and interrupts after
argument completion. It is valuable transport evidence, but cannot prove native
execution or tool-result continuation. Its recent model-GET fixture race also
establishes why snapshot provenance must be checked at resolution, rather than
comparing every in-flight request with later mutated storage.

### Inventory and specialized host behavior
Source: tool-coverage.md, installed ToolDomain/SessionContext APIs and native
read-only registry observation already captured in this spec.

The observed profile exposes 24 registered tools, including native/plugin tools,
resource helpers and conditional image generation. It is not the actual session
union or a complete dynamic MCP inventory. Names alone do not establish origin.
A fixture using the same name as a private memory plugin cannot claim execution
of that plugin or service.

The installed question APIs are session.form.list/get/reply/cancel; no ctx.question
contract was found. Native websearch has a public transform/add/execute backend
contract. The installed public plugin context exposes no dedicated image-provider
hook; controlled native image-generation execution remains an implementation
capability gate, not a solved fixture. Generic file-returning tools cannot count
as native image generation.

### SDK conversion and Mistral constraints
Local sources: node_modules/@ai-sdk/openai-compatible/src/chat/
convert-to-openai-compatible-chat-messages.ts and
openai-compatible-prepare-tools.ts; installed @opencode/ai 2.2.5
protocols/mistral-chat.js and protocols/utils/mistral-tool-id.js.

The installed V3 serializer preserves tool names/descriptions/input schemas and
IDs, serializes arguments as JSON, and supports incremental results. It converts
text/error-text alike, json/error-json alike, and content arrays to JSON strings.
A JSON string containing an image description is not actual media delivery.
Do not extrapolate from current AI SDK upstream main, which has moved beyond V3.

Pinned official Mistral source commit df71fdd07de23091c9fd1c57f91d6d8ae54479be
(2026-10-28) constrains function names to ASCII letters/digits/underscore/hyphen,
1-64 characters. Legacy V3/V7/V11 tokenizers require nine alphanumeric call IDs;
V13 and later accept nonempty IDs other than null. Pre-V15 tool messages are
text-only despite user-message image/audio support; later versions broaden tool
content. This does not identify a deployed model's tokenizer or entitlement.
[Official pinned validator](https://github.com/mistralai/mistral-common/blob/df71fdd07de23091c9fd1c57f91d6d8ae54479be/src/mistral_common/protocol/instruct/validator.py)

The broader public ToolMessage content schema allows strings/content chunks but
has no top-level isError property. Preserve error meaning inside content and
retain host typed error state; do not add an unsupported API field. Model-specific
media support needs actual route evidence, not merely vision metadata.
[Official OpenAPI](https://docs.mistral.ai/openapi.yaml)
[Official chat API](https://docs.mistral.ai/api/endpoint/chat)

Installed native Mistral code has collision-aware history ID normalization and
image/PDF tool lowering. It is useful reference behavior, but migrating to the
native route would alter V3 registration, auth interception and cancellation.
Prefer bounded corrections with parity tests to a speculative SDK replacement.

### MCP fixture contract and dependencies
Adopt official SDK 1.32.2 as an explicit dev dependency after locked preparation.
It is absent from the existing package/lock; do not depend on an implicit
transitive install. Official v1 docs support protocol through 2025-11-25. New v2
packages/current specifications differ; record negotiation rather than silently
following latest. Registry research verified release 1.32.2 exists.
[Official TypeScript SDK](https://ts.sdk.modelcontextprotocol.io/)
[Registry release](https://registry.npmjs.org/@modelcontextprotocol%2Fsdk/1.32.2)

Initialize and negotiate capabilities before discovery. Exercise tools/list and
tools/call, structuredContent and compatible text, isError tool failures, distinct
JSON-RPC protocol errors, and advertised pagination/list changes. Resources are
separate list/read operations; a tool's resource links need not be listed.
[Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
[Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
[Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)

Stdio is newline-delimited UTF-8 JSON-RPC with protocol-only stdout. Cancellation
is an in-progress notification, can race completion, and does not guarantee
rollback. Observe host cancellation, fixture cleanup and model HTTP abort
separately.
[Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
[Cancellation](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation)

### Live profile isolation
Read-only installed CLI debug-path probes showed OPENCODE_TEST_HOME alone changed
home but left config/data/state/cache under real user paths. With private XDG
roots and temporary paths, every reported path (including database, logs, repos
and binaries) moved beneath the owned root. No account startup was used.

Windows can preserve LOCALAPPDATA for Python credentials while private XDG roots
isolate OpenCode. macOS retains HOME; Linux must restore original XDG_DATA_HOME
and fallback HOME only in the helper, since the auth library uses those paths.
A test-owned launcher using the existing absolute uv-created Python executable
fits current command validation and bridge/login suffix handling. Restore only
path variables and unset semantics, not arbitrary parent environment/credentials.
Cross-platform process and path probes remain implementation gates.

Temporary cleanup limitation: automatic policy review rejected recursive deletion
of probe directories with only the reason 'blocked by policy'. Read-only follow-up
found one remaining owned root with cache/config/data/state/tmp directories:
C:/Users/mariu/AppData/Local/Temp/vibe-path-probe-2dca1aea8ff943d4889048683f6cac61.
It was left untouched after inspection; no cleanup permission was requested.

## Architecture Pattern Evaluation

| Option | Strength | Limitation | Decision |
| --- | --- | --- | --- |
| Rewrite tools/MCP client in provider | Full local control | Duplicates host permissions and semantics | Reject |
| Real host with passive observers and owned external fixtures | Verifies execution and continuation while retaining boundaries | More lifecycle/coverage work | Select |
| Existing synthetic_echo test only | Cheap and stable transport regression | Not execution or all-tool evidence | Retain as baseline only |
| Replace SDK with native Mistral route | Existing normalization/media support | Unproved V3/OIDC/cancellation/no-replay parity | Defer unless evidence justifies migration |
| Narrow typed protocol wrapper after RED reproduction | Limits production change and preserves current auth | Profile/correlation tests required | Select where a mismatch is proved |

## Design Decisions and Synthesis

### Generalization: one proof model, several fixture families
Native/plugin/MCP tools share definition -> call -> execution -> result -> next
request -> response. Use one typed evidence graph with family-specific owned
fixtures. Do not create an executor or adapter abstraction for each of 24 names.

### Adopt: host SDK and official MCP server
Use host APIs for permissions/forms/children/worktrees and the official MCP server
SDK for protocol fixtures. Build only inventory reconciliation, evidence validation,
fixture coordination and bounded model-wire correction not supplied by the host.
No custom JSON-RPC client or replacement native tools are needed.

### Simplify: explicit scope and unsupported outcomes
Separate inventory completeness from support. Controlled profiles prevent private
service access but cannot prove the user's different profile. Missing image/MCP
capabilities stay unsupported/unverified. A fixed baseline list never grants a
future host a compatibility pass.

### Preserve: authentication and discovery lifecycle
No codec is placed in Python auth or proxy routing. Request-local wire conversion
runs before the existing auth fetch; response name restoration uses decoded V3
parts, preserving incremental delivery. Avoid rediscovery during SDK creation,
fixed sleeps and mutable mapping shared across sessions.

## Risks and Mitigations
- SDK hooks may have different ordering or skip custom fetch: capability probe and
  explicit unsupported outcome before claiming complete wire evidence.
- Real profile services contain private data: fresh profiles, owned fixtures,
  origin-aware scope and strict sanitized capture.
- Media/tool schema capability differs by model: verified protocol profiles and
  actual media evidence; no inference from a model name or vision flag.
- Dynamic tools and MCP startup change offerings: reconcile every primary context
  and re-run coverage after updates; readiness uses events and host state.
- Windows/Linux/macOS path separation differs: validate debug paths and helper
  inheritance before live mode; no new auth storage API.
- Host cancellation cannot undo side effects: inspect owned state and record
  committed/unknown outcomes separately from request cancellation.

## References
Primary URLs are attached to each contract above. Local authority is the installed
2.2.5 SDK and checked-in implementation, not generic OpenCode v1 documentation.
Consulted workflow: kiro-spec-design, its design principles/discovery/synthesis/
review rules and repository templates; no live-account or implementation skill
was executed in this design phase.

## Technical Design Review
Mechanical gate covered all 33 criterion IDs, concrete boundaries/files and typed
contracts. Independent first pass found two local evidence-schema gaps. Repair
added explicit scope/tool/request-chain/call identifiers and authoritative negative
case expectations with attached cancellation evidence. Independent second pass
returned PASS. Human design approval is pending; no implementation or live tool
execution is inferred from this technical review. See design-review.md.

## Task planning
The user approved the design. The task plan now contains eight groups and 36
executable subtasks, with five parallel candidates and all 33 criteria mapped.
Mechanical coverage/dependency checks and a fresh independent task-graph sanity
review passed. No plan repair was required. Task approval and implementation are
pending; live tool results remain unverified. See tasks.md and task-plan-review.md.
