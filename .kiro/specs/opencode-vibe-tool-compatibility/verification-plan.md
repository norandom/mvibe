# Proposed end-to-end verification

This is a discovery test matrix, not executed evidence or an approved task plan.
Use the installed OpenCode 2 CLI and direct `vibe-oidc` provider. Discover actual
tool IDs and event schemas from that host before implementing the harness.

| Scenario | Controlled input | Required evidence |
| --- | --- | --- |
| Native file read | A random nonce in a temporary file | Host tool execution, exact file contents returned, next model turn receives result |
| Native create/edit | A requested change confined to the temporary workspace | Expected file diff plus execution trace and correlated result |
| Native file search | Known matching and nonmatching fixture files | Actual search invocation and expected matches, not a guessed answer |
| Native command | A harmless command with an unpredictable fixture result | Approved execution event, exit status/stdout, result delivered to model |
| MCP discovery | An owned local stdio MCP fixture | The real OpenCode MCP client discovers the fixture schema |
| MCP invocation | A typed echo/calculation fixture with nested arguments | Exact MCP arguments, real server invocation, correlated structured result |
| MCP search-shaped result | Deterministic local fixture results and source links | Tool output and citations preserved; no claim of internet search |
| Multiple tool calls | Two distinguishable fixture operations | Distinct call IDs and arguments; no crossed results |
| Streamed arguments | Unicode and nested JSON delivered in chunks | One valid reconstructed argument object with no dropped or duplicated bytes |
| Tool failure | A fixture failure with a safe message | Error reaches the model as a tool outcome; bounded continuation and no secret leakage |
| Permission denial | A denied file/command/MCP operation | No execution side effect and a visible denied outcome; no provider bypass |
| Cancellation | An owned long-running fixture | Host cancellation, provider request cancellation, and owned-resource cleanup |
| Restart and reuse | A fresh host process with existing OIDC state | Correct provider/model visibility and successful tool round trip after restart |

For each positive case, verify the definition, requested call, executor event,
result association, subsequent model input, and final outcome. A tool-shaped
payload or plausible final answer alone is insufficient.

## Offline and live separation

Offline runs use synthetic inference with the real native OpenCode host and real
temporary file/command/MCP fixtures. Deny external account, keyring, browser and
network access by default; permit only explicitly owned test resources. Invoke
Python fixtures through prepared uv environments. Never use Windows Store Python.

Live runs use harmless fixture content and explicitly selected tool-capable
models. Verify native and MCP cases independently, record versions and per-case
outcomes, and leave unexercised cases unverified. Account/model limitations,
OpenCode failures and provider defects must remain distinguishable. Retain safe
event summaries and fixture hashes, excluding tokens, sign-in URLs and private
workspace/account content. No live tests have been executed for this new spec.
