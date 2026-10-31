# Requirements Document

## Project Description (Input)
OpenCode users selecting Mistral-Vibe need compatibility with all tools offered by their installed host, including native file/command tools, other native and plugin tools, and MCP tools/resources. Current synthetic checks preserve tool-call payloads, but do not prove live execution and correlated result delivery for all these tools. Specify compatibility and repeatable end-to-end verification against an actual runtime inventory, retaining OpenCode execution and permissions and making adapter changes only for observed mismatches. See brief.md, tool-coverage.md and verification-plan.md.

## Boundary Context

- In scope: all registered tools offered to the selected OpenCode agent/session,
  including file/command, web, question, skill/subagent, session/worktree,
  model/resource helpers, configured plugin/MCP tools and conditional experimental
  tools; complete round trips, permission/error/cancellation outcomes, and
  separate offline and live evidence.
- Out of scope: replacing OpenCode's executors or MCP client, granting arbitrary
  command permissions, adding a Vibe search endpoint, or promising support for
  every model and future OpenCode version.
- Adjacent expectations: OpenCode owns available tools, execution and permissions;
  MCP server behavior and model capability are external constraints. OIDC-only
  authentication, refresh and inference failure behavior remain governed by
  the parent vibe-oidc-proxy specification.
- Terminology: Integration means the observable OpenCode/Mistral-Vibe tool flow,
  not a new executor. Preserving a tool's identity permits a reversible transport
  representation where a proven model constraint requires it; user-visible
  operation identity, permissions, arguments and result association must remain
  unchanged.

## Requirements

### Requirement 1: Native OpenCode Tools
**Objective:** OpenCode users retain the host's complete offered tool set while selecting Mistral-Vibe.

#### Acceptance Criteria
1. When OpenCode offers native tools to a Mistral-Vibe turn, the Integration shall preserve the host-provided tool identities, descriptions and input constraints.
2. When the model requests an available native file read, create, edit or search operation, the Integration shall let OpenCode execute it under the host's permissions and associate its result with the requested call.
3. When the model requests an approved native command, the Integration shall return the host's command outcome to the model, including the output and exit information supplied by the host.
4. If a native tool is disabled or unavailable, the Integration shall report that outcome without claiming that the operation executed.
5. When OpenCode offers another native or plugin-defined tool, the Integration shall preserve its tool identity, input constraints and host-provided result without requiring a replacement executor in the provider.

### Requirement 2: MCP Tools
**Objective:** The same provider can use tools supplied by configured MCP servers.

#### Acceptance Criteria
1. When OpenCode discovers an enabled MCP server's tools, the Integration shall expose the host-provided tool definitions to a compatible model.
2. When the model requests an available MCP tool, the Integration shall preserve its arguments and let OpenCode invoke the configured server.
3. When the MCP tool completes, the Integration shall associate the host-provided text or structured result with the originating call.
4. If MCP discovery or invocation fails, the Integration shall preserve the host's failure outcome rather than return a fabricated success.

### Requirement 3: Complete Tool Round Trips
**Objective:** Tool requests, execution results and subsequent model turns remain correlated.

#### Acceptance Criteria
1. While tool arguments arrive incrementally, the Integration shall preserve the completed argument values, including nested objects, arrays, Unicode strings, numbers and booleans.
2. When a turn contains multiple tool calls, the Integration shall preserve the association between each call, its arguments and its result.
3. When OpenCode supplies a tool result to the next model turn, the Integration shall preserve the supplied content and success or error meaning.
4. If a model tool request cannot satisfy the host's input constraints, the Integration shall report the invalid request without executing it as a different operation.
5. When an executed tool returns a fixture observation in an end-to-end test, the Verification Suite shall establish that the subsequent model turn received that observation before accepting the final answer as a successful tool round trip.

### Requirement 4: Permissions, Failures and Cancellation
**Objective:** Selecting the provider preserves OpenCode's control over tool execution.

#### Acceptance Criteria
1. If OpenCode denies a file, command or MCP operation, the Integration shall not bypass that decision through another execution path.
2. When OpenCode cancels a turn, the Integration shall propagate cancellation to its pending provider work and preserve the host's cancelled tool outcome.
3. If a tool execution fails, the Integration shall preserve the failure as a tool outcome rather than label it a successful result.
4. If an inference request fails, the Integration shall retain the parent specification's bounded failure and no-replay behavior instead of introducing an unbounded tool retry loop.

### Requirement 5: Verification and Compatibility Evidence
**Objective:** Maintainers can distinguish real interoperability from payload-only or model-only demonstrations.

#### Acceptance Criteria
1. When offline verification runs, the Verification Suite shall exercise the real OpenCode host with owned native-tool and MCP fixtures while preventing real account, browser and credential-store access.
2. When a positive end-to-end case is accepted, the Verification Suite shall record evidence of the offered definition, requested call, executor activity, correlated result and subsequent model response.
3. When reporting compatibility, the Project Documentation shall distinguish native-tool results, MCP results, offline evidence and live evidence.
4. If an operation or model has not been exercised, the Project Documentation shall mark it unverified rather than infer support from another model, tool family or evidence class.
5. When recording a run, the Verification Suite shall identify the observed OpenCode, provider and model versions or identifiers and exclude real credentials, sign-in URLs and private workspace contents.
6. If an OpenCode update changes the observed version, the Project Documentation shall distinguish earlier compatibility evidence from results actually obtained with that version.
7. When a compatibility run begins, the Verification Suite shall record the registered and actually offered tool inventories for the selected host, profile, agent, model and capability state, including tools discovered through MCP.
8. When reporting coverage, the Verification Suite shall account for each discovered tool with separate offline/live results and an explicit executed, unsupported, disabled or unverified status with evidence or a reason.
9. If an update or configuration change exposes an unaccounted tool, the Verification Suite shall fail the inventory-coverage gate rather than silently omit it.
10. When claiming compatibility with all applicable tools in a scoped configuration, the Verification Suite shall require executed positive evidence for every applicable offered tool; unsupported, disabled and unverified entries shall limit that claim.

### Requirement 6: Host-Managed and Conditional Tools
**Objective:** Specialized tool behavior remains controlled by OpenCode rather than being reduced to a generic text-only success.

#### Acceptance Criteria
1. When a question tool requires user input, the Integration shall preserve the host's interaction and cancellation outcome rather than fabricate the user's answer.
2. When a tool loads a skill or delegates work to a subagent, the Integration shall preserve the host's lifecycle and actual returned result.
3. When a tool changes a session or worktree, the Integration shall preserve the host's permission checks and reported mutation outcome.
4. If a resource, web or experimental tool requires capabilities or returns content unsupported by the selected configuration, the Integration shall preserve an explicit unsupported or unavailable outcome rather than silently discard the content or claim success.
5. When OpenCode supplies a supported non-text tool result to a capable model, the Integration shall preserve the supported content and metadata needed for the subsequent model turn.

## Review Status

Requirements review gate passed inline after the all-tool expansion: numeric
headings, EARS criteria, observable outcomes, runtime-inventory coverage and
explicit boundaries checked. The user approved requirements and design;
implementation tasks are generated but not yet approved. The proposed discovery matrix remains
in verification-plan.md.
