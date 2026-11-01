# API contracts and observability extension

User requested on 2026-11-01 while approving baseline continuation. Keep the
practical E2E baseline; add change detection and operator diagnostics, not a new
all-tool certification system.

## OpenCode contract evidence

The installed @opencode/protocol 2.2.5 exports ClientApi, an Effect HttpApi.
OpenApi.fromApi(ClientApi) produces OpenAPI 3.1 with 138 path entries locally.
This is a prepared-SDK contract, not proof that an arbitrary running binary
implements it. Record package/CLI versions and binary hash with each run.
The V2 official API reference also links an OpenAPI JSON document.

Plugin context methods include hooks/transforms outside HTTP. Compare the
published plugin TypeScript declaration surface used by this provider alongside
HTTP schemas, compile against the prepared candidate SDK, then run the real host
E2E baseline. Schema equality alone never proves behavioral compatibility.

Keep a reviewed baseline and explicit candidate capture/compare commands. Emit
readable structural differences and nonzero drift result; do not silently update
baselines. Distinguish additive/changed/removed surface without assuming every
difference breaks our provider. Candidate updates must be prepared separately,
never mutate the user's auto-updating installation or fetch during offline tests.

## Proxy API and diagnostics now

FastAPI already serves /openapi.json and /docs, but the current catch-all route
hides the actual supported operations. Publish explicit models, chat completions
and embeddings operations while retaining raw payload passthrough, streaming and
unknown-endpoint handling. Document tool calls as forwarded conversations: the
agent executes tools, the proxy does not run tools or act as an MCP server.

Use structlog-backed structured proxy stdout events with request IDs, phase,
status, elapsed time and fixed actionable hints for auth, endpoint, upstream,
stream and cleanup failures. Do not log prompts, tool arguments/results, headers,
credentials or raw upstream exceptions. No auth/helper protocol stdout changes.
Add only safe metadata needed for diagnosis and preserve upstream status/body,
stream timing and no replay. OpenTelemetry export and distributed tracing are
follow-up work in ../proxy-observability/brief.md.

## Sources

- https://dev.opencode.ai/v2/docs/api
- https://dev.opencode.ai/v2/docs/build/plugins/server/
- https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/structlog/structlog.html
- https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/logging/logging.html
- https://structlog.org/en/stable/contextvars.html

## Concrete offline capture and rerun inputs

The installed contract can be generated without starting a server:

```typescript
import { ClientApi } from "@opencode/protocol/client";
import { OpenApi } from "effect/http-api";
const schema = OpenApi.fromApi(ClientApi);
```

The V2 docs publish https://dev.opencode.ai/v2/openapi.json separately. A fetched
docs-site schema is reference material, not evidence of the installed CLI version.

Provider-used plugin declarations include promise/plugin, provider, integration,
aisdk and model domains; E2E uses session/tool/MCP/permission/shell domains too.
Normalize declaration comments/formatting and compare meaningful surfaces; compile
against the candidate SDK as the authoritative type-use check. Existing bridge
JSON-schema tests continue to cover our own non-HTTP helper protocol.

Future reruns must avoid the current hard-coded reference version barrier in
preparation.ts/runner.ts. Preserve the pinned default but allow an explicitly
selected prepared 2.x candidate with a matching expected version and SDK package
root. Candidate copies also need the original project root for Python helper
imports (runner PYTHONPATH currently derives from package location). Never loosen
private-home isolation or silently accept a version mismatch.

One aggregate baseline command can call the existing query/mutation/command/MCP
probes and reuse each parser. Add its result to the parent acceptance runner
without removing any existing stage or the 97 parent requirement mappings.
