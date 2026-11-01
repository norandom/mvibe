> Revision 2 scope: this is the original discovery snapshot, not the current
> execution ledger. Later offline query/mutation evidence is recorded in
> implementation-log.md and its artifacts. Unexercised tools below are deferred
> from baseline completion; their live support remains unverified.
# Tool coverage inventory

Read-only observation on 2026-10-31, native OpenCode CLI 2.2.5: the host's
`ctx.tool.list()` returned these 24 registered tools in the current profile.
No tool execution or inference was performed for this inventory check. This is
an observed registry snapshot, not proof that every tool is offered to every
agent/model, nor a complete inventory of dynamically connected MCP tools.

Every execution result below is **unverified**, independently for offline and
live end-to-end behavior through Mistral-Vibe. Planned cases are requirements
coverage, not implemented tests. Existing synthetic transport tests do not count
as per-tool execution evidence.

| Observed tool ID | Planned controlled case | Offline | Live |
| --- | --- | --- | --- |
| `read` | Read an unpredictable fixture nonce | Unverified | Unverified |
| `write` | Create an owned fixture file and inspect bytes | Unverified | Unverified |
| `edit` | Apply a requested fixture change and inspect diff | Unverified | Unverified |
| `patch` | Apply a multi-file fixture patch and inspect diff | Unverified | Unverified |
| `glob` | Match known fixture paths | Unverified | Unverified |
| `grep` | Return expected fixture matches and locations | Unverified | Unverified |
| `shell` | Execute an approved harmless fixture command | Unverified | Unverified |
| `webfetch` | Fetch a controlled fixture page with known content | Unverified | Unverified |
| `websearch` | Return deterministic fixture search results and source links | Unverified | Unverified |
| `question` | Supply a controlled answer and separately cancel the host interaction | Unverified | Unverified |
| `skill` | Load an owned skill fixture containing a nonce | Unverified | Unverified |
| `subagent` | Observe child execution and its correlated result | Unverified | Unverified |
| `opencode_models` | Return actual scoped fixture model availability | Unverified | Unverified |
| `opencode_session_move` | Move an owned test session and verify its destination | Unverified | Unverified |
| `opencode_session_rename` | Rename an owned test session and verify its title | Unverified | Unverified |
| `opencode_worktree_create` | Create a worktree in an owned temporary repository | Unverified | Unverified |
| `opencode_worktree_list` | Observe the fixture worktree registry | Unverified | Unverified |
| `opencode_worktree_remove` | Remove only the worktree created by the fixture | Unverified | Unverified |
| `opencode_list_mcp_resources` | Enumerate resources from an owned MCP fixture | Unverified | Unverified |
| `opencode_read_mcp_resource` | Read a fixture resource with a known payload | Unverified | Unverified |
| `experimental_image_generate` | Preserve a controlled image result, or report missing capability explicitly | Unverified | Unverified |
| `claude_mem_get_observations` | Retrieve a synthetic observation through a configured test fixture | Unverified | Unverified |
| `claude_mem_search` | Search synthetic observations with known matches | Unverified | Unverified |
| `claude_mem_timeline` | Return the expected synthetic observation order | Unverified | Unverified |

The memory-tool names above are observed profile entries. Their names alone do
not establish whether the host supplied them through a plugin or MCP; the future
harness must record actual provenance. No real memory data is required.

## Discovery and completeness gate

Before a run, reconcile the host registry, selected agent/session's offered tool
definitions, enabled plugin tools, MCP-discovered tools/resources and conditional
capabilities. Capture tool identities, schema fingerprints, prerequisites and
host/model versions without private contents. Repeat discovery after OpenCode
updates or configuration changes.

Each discovered tool must map to a controlled test and separate offline/live
outcomes. Unsupported, disabled and unverified entries require reasons and limit
the scope of a compatibility claim. An unmapped tool fails inventory coverage;
an inventory-complete matrix does not establish execution compatibility.

All mutation tests use owned temporary files, sessions and repositories. Web,
memory, MCP and image fixtures must avoid private services/content. An optional
tool requiring another configured service or capability does not acquire that
capability merely because chat authenticates through Vibe OIDC.
