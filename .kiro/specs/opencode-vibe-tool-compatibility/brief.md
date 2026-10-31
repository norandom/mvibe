# OpenCode Vibe tool compatibility

## User intent

OpenCode users selecting Mistral-Vibe need all tools offered by their installed
host, including normal filesystem/command tools and tools supplied by MCP servers.
The user subsequently asked whether coverage includes every OpenCode tool; the
draft now requires a runtime inventory rather than treating those two examples
as an exhaustive tool list. The existing provider preserves streamed
tool-call payloads in synthetic tests, but that does not establish real execution
of either tool family through the direct Vibe OIDC provider. The user explicitly
requested both and authorized a separate specification and end-to-end testing.

## Intended change

Establish and verify the complete round trip: OpenCode supplies tool definitions,
the model requests a tool through the direct provider, OpenCode executes it under
its permissions, and the correlated result reaches the model for the next turn.
Retain existing OpenCode tools and MCP client behavior. Any compatibility change
must address an observed transport/model mismatch in the adapter; do not rewrite
the filesystem, command executor, or MCP client as provider functionality.

## Boundary candidates

- Provider adapter: tool definitions, streamed arguments, call/result correlation,
  error representation, cancellation propagation, and any proven normalization.
- OpenCode host: native filesystem/command execution, MCP server connections,
  permissions, scheduling and user interaction. Exercise these through the real
  installed host rather than replacing them with a fake executor.
- Test harness: temporary workspaces, owned deterministic MCP fixtures, versioned
  offline evidence, and separately authorized live account verification.
- Shared auth library: existing OIDC login, persistence, rotation and tenant
  selection; tool execution must not introduce another inference auth mechanism.

## In scope

All offered native/plugin tools, including file read/create/edit/search, commands,
web tools, questions, skills/subagents, session/worktree operations, model helpers,
MCP discovery/resources/invocation and conditional experimental capabilities;
structured and text tool results; errors and denied permissions;
multiple calls and streamed JSON arguments; cancellation; model/version evidence;
successful tool results included in the next model turn and final response.

## Out of scope

A replacement tool system, arbitrary command auto-approval, new inference API-key
authentication, a Vibe search endpoint, general MCP server implementation, and a
claim that every model or future OpenCode update supports every tool feature.
Search can be a separately configured OpenCode/MCP tool; this does not establish
or implement a Vibe-authenticated search service.

## Existing evidence and next phase

The parent specification is `vibe-oidc-proxy`, especially requirement 12.6.
Synthetic payload preservation and native host checks are useful baseline
evidence; live native-tool and MCP round trips remain unverified. Capture actual
host, SDK, model and adapter versions for each future run. Core steering files
are absent. Requirements are approved and the technical design has been
generated for review. Design/task approval and implementation remain pending.
