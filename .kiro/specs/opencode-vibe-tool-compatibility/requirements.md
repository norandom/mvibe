# Requirements Document

## Project Description (Input)
OpenCode users selecting Mistral-Vibe need both the native filesystem and command tools and tools supplied by MCP servers. Current synthetic checks preserve tool-call payloads, but do not prove live execution and correlated result delivery through the direct Vibe OIDC provider. Specify compatibility and repeatable end-to-end verification for both tool families, retaining OpenCode execution and permissions and making adapter changes only for observed mismatches. See brief.md and verification-plan.md for the user-authorized boundary and proposed evidence matrix.

## Boundary Context

- In scope: normal OpenCode file and command tools, configured MCP tools, complete
  tool round trips, permission/error/cancellation outcomes, and separate offline
  and live end-to-end evidence for both families.
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
**Objective:** OpenCode users can use the normal file and command tools while selecting Mistral-Vibe.

#### Acceptance Criteria
1. When OpenCode offers native tools to a Mistral-Vibe turn, the Integration shall preserve the host-provided tool identities, descriptions and input constraints.
2. When the model requests an available native file read, create, edit or search operation, the Integration shall let OpenCode execute it under the host's permissions and associate its result with the requested call.
3. When the model requests an approved native command, the Integration shall return the host's command outcome to the model, including the output and exit information supplied by the host.
4. If a native tool is disabled or unavailable, the Integration shall report that outcome without claiming that the operation executed.

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

## Review Status

Requirements review gate passed inline: numeric headings, EARS criteria,
observable outcomes, coverage of both requested tool families and explicit
boundaries checked. These are generated requirements for human review; design,
tasks and implementation are not approved. The proposed discovery matrix remains
in verification-plan.md.



