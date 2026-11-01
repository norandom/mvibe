# Run the prepared tool baseline

From the checkout root, run every mandatory offline acceptance stage, including
the sequential native/MCP baseline:

```powershell
pwsh -NoProfile -File scripts/verify.ps1 -Bun C:/Users/mariu/.bun/bin/bun.exe -HostBinary D:/Source/mvibe/packages/opencode-vibe-oidc/.host/opencode.exe
```

Prepare dependencies separately: `uv sync --locked --python 3.12 --extra proxy`,
then `bun install --frozen-lockfile` in `packages/opencode-vibe-oidc`, using native
Bun 1.4.4. See [host preparation](opencode-provider.md) for `prepare:host` and the
isolated OpenCode 2.2.5 binary. Preparation may use the network; verification does
not install or update packages. Git and PowerShell must be available for the
native mutation/shell fixtures. Use the uv-created `.venv/Scripts/python.exe`,
never Windows Store aliases. Default Python checks use two isolated xdist workers;
`-PythonWorkers 0` selects serial debugging. Host probes always run sequentially.

To rerun only this baseline from the provider directory:

```powershell
$env:OPENCODE_TEST_BINARY = 'D:/Source/mvibe/packages/opencode-vibe-oidc/.host/opencode.exe'
$env:VIBE_TEST_PYTHON = 'D:/Source/mvibe/.venv/Scripts/python.exe'
& C:/Users/mariu/.bun/bin/bun.exe --no-install run test:tool-baseline --output D:/Source/mvibe/reports/tool-baseline/my-new-run
```

Choose a fresh output directory each time. `baseline.json` records prepared SDK,
protocol/client/CLI versions, binary hashes, platform, synthetic model profiles,
compile/drift/probe outcomes and artifact paths. Each family artifact retains
the existing parser's actual executor/state/correlated continuation evidence and
cleanup result. Exit 1 means failed or missing evidence, including contract drift;
the remaining safe probes still run. The full runner also retains all ten earlier
stages and all 97 parent acceptance mappings. Live results stay `unverified`.

## Tested scope

The reference is Windows x64 OpenCode/plugin/protocol/client **2.2.5**, Bun 1.4.4,
and uv Python 3.12.10. Query, mutation, command and MCP workflows passed locally.
Candidate mode was also exercised against a separately copied provider checkout
using the same prepared 2.2.5 dependencies and native binary; this is a rerun-path
check, not evidence for a newer version.

The latest [complete offline acceptance](../reports/verification/1c057bce385247e5871ab087bbbb26f5/acceptance.json)
passed all 11 stages and 97 parent mappings (1,034 Python and 967 Bun tests).
Its [baseline summary](../reports/verification/1c057bce385247e5871ab087bbbb26f5/tool-baseline/baseline.json)
links the four family artifacts. The explicit
[candidate-copy rerun](../reports/tool-baseline/candidate-task42-final/baseline.json)
also passed compilation, contract comparison and all four workflows. Reports are local ignored artifacts; retain
them with exported evidence. Live verification remains unexecuted.

The baseline uses real read/glob/grep, write/edit, shell output/exit 0/7 and one
official-SDK stdio MCP tool, with representative permission/error/cancellation
checks. Patch execution is conditional on its owned fixture model profile.
MCP uses `codemode=false`, nested/Unicode input and structured results. Synthetic
inference drives the real host's tools; it does not call a live model/account.
Inspect per-case records for exact positive and negative outcomes; a denied or
cancelled operation is not successful execution credit.

Questions/forms, skills/subagents, session/worktree mutation, web/search, images,
broad non-text codecs, private MCP services, other platforms and future updates
remain deferred or unverified. Passing this subset does not imply all-tool support.

## Check a prepared OpenCode update

Keep the checked-in reviewed reference
`packages/opencode-vibe-oidc/test/tool-compatibility/contracts/opencode-2.2.5.json`
unchanged. It contains OpenAPI 3.1 generated locally from the installed
`@opencode/protocol` `ClientApi` (138 paths), plus normalized TypeScript AST-printed
declarations for plugin/provider/integration/aisdk/model/session/tool/MCP/permission/
shell. Comments and formatting are ignored. This is a prepared SDK contract;
the public docs site's latest schema is not installed-binary evidence. Our Python
bridge JSON-schema tests remain part of the existing test suites.

Optional network-enabled discovery/preparation is explicit:
`bun run compatibility:current --discover-current`, then
`bun run compatibility:current --prepare-current` from the provider directory.
This creates `.compat/<version>/provider` with its own lock/dependencies and native
CLI, refuses an existing destination, and runs build/host/baseline checks. Nothing
changes your global OpenCode installation. A new version remains unverified until
its actual compile, contracts and E2E results have been reviewed.

For an already prepared stable 2.x pair, run the reference command with explicit
candidate options (substitute the exact prepared directory, version and binary):

```powershell
$env:OPENCODE_TEST_BINARY = 'D:/prepared/2.x.y/opencode.exe'
$env:VIBE_TEST_PYTHON = 'D:/Source/mvibe/.venv/Scripts/python.exe'
& C:/Users/mariu/.bun/bin/bun.exe --no-install run test:tool-baseline --provider-root D:/prepared/2.x.y/provider --project-root D:/Source/mvibe --expected-version 2.x.y --output D:/Source/mvibe/reports/tool-baseline/candidate-new-run
```

Replace `2.x.y` with a numeric stable release. The candidate provider copy must
contain the current `src`, `test`, `scripts`, TS configs, package manifest/lock and
prepared node_modules. The host/plugin/CLI/protocol/client must match the exact
expected version. The original project root supplies Python helper imports;
private-home isolation and synthetic auth guards remain active. Capture and probe
scripts execute inside the selected provider copy so SDK/Effect imports and type
compilation use its prepared dependency tree. Captures record resolved module
paths. Keep the reference checkout as the command entry point to compare with its
reviewed reference; copied reference files are not silently updated.

Capture or compare contracts separately, using native Bun from the selected
provider directory and the exact version environment:

```powershell
$env:VIBE_EXPECTED_HOST_VERSION = '2.2.5'
& C:/Users/mariu/.bun/bin/bun.exe --no-install run contracts:opencode capture D:/reports/new-candidate-contracts.json
& C:/Users/mariu/.bun/bin/bun.exe --no-install run contracts:opencode compare D:/reports/new-candidate-contracts.json D:/Source/mvibe/packages/opencode-vibe-oidc/test/tool-compatibility/contracts/opencode-2.2.5.json
```

Capture requires an existing parent directory and refuses an existing file.
Compare performs no network access and never writes either snapshot. It prints
`added`, `removed` and `changed` locations with exit 1 for structural drift. These
labels do not classify every change as breaking. Equal schemas do not prove hook
ordering, streaming, process cleanup or behavioral compatibility. Review changes
and their separate compile/runtime results before deliberately replacing a
reference in a future reviewed change; there is no automatic blessing command.

## Diagnose the proxy route

With the deliberately started proxy, inspect
[OpenAPI JSON](http://127.0.0.1:8765/openapi.json) and
[Swagger UI](http://127.0.0.1:8765/docs). They describe models, chat completions
(including streaming/error envelopes) and embeddings. The proxy forwards tool
conversations; the agent executes tools and owns MCP. The direct plugin bypasses
the proxy entirely. See [provider setup](opencode-provider.md) for both routes.

Responses carry `X-Request-ID`. Match that value to structured stdout, for example:

```json
{"event":"proxy_request","request_id":"0123456789abcdef0123456789abcdef","phase":"upstream","outcome":"upstream_error","status":403,"elapsed_ms":12.5,"hint":"Check Vibe tenant and model access; the agent owns tool permissions."}
```

Authentication hints direct explicit OIDC re-login; 400 hints point to model/tool
schema fields, 403 to tenant/model access, 429 to upstream rate guidance, and
stream cancellation/interruption asks the operator to inspect agent state before
retrying. Cleanup failure requires proxy restart. Events omit headers,
credentials, prompts, tool arguments/results and raw upstream exceptions. Logging
preserves stream ownership and never replays inference. These are structlog
stdout diagnostics; [traces/metrics/export](../.kiro/specs/proxy-observability/brief.md)
remain follow-up work.

## Opt in to a harmless live workflow

Live testing is separate and was not executed for this baseline. Follow
[existing Vibe OIDC setup and evidence procedure](live-verification.md); default
checks never launch sign-in or access live accounts. Explicitly select a model
returned by your account with verified tool capability, an owned empty workspace,
and either a native read/write of one synthetic text file or an owned local MCP
echo tool with public nested/Unicode values. Request that tool, observe its actual
execution and matching call/result continuation, then clean up the owned fixture.
Retain only safe versions/outcomes and leave unrun cases unverified. Use the
existing Vibe OIDC credentials/login flow; no API-key alternative is introduced.
