# Practical baseline design

Revision 2, 2026-11-01. This replaces the former certification-oriented design.
Use the implemented components; stop expanding the framework.

## Boundaries

- The direct provider and shared Python auth library retain Vibe OIDC login,
  refresh, tenant selection and credential storage. No API-key alternative or
  proxy dependency is introduced.
- OpenCode owns file/shell executors, permissions, sessions and its MCP client.
- Tests own temporary workspaces, a local MCP fixture, synthetic inference and
  bounded observations. Synthetic inference never replaces a tool executor.
- A provider change is justified only by a reproduced baseline defect.

## Reuse the working code

Keep the existing contracts, recorder, TS/Python validators, fixtures and tests.
Do not add more schema layers, a second generation of evidence models or a
per-tool certification engine. Existing all-tool capability checks can remain
unit-tested utilities; completing their broad matrix is outside this baseline.

| Area | Existing implementation and minimal remaining work |
| --- | --- |
| Native host | Reuse test/tool-compatibility/runner.ts, prepared CLI, private paths and owned process cleanup |
| Model boundary | Reuse synthetic-inference.ts and observer transport installed before provider setup; public HTTP hooks were bypassed on 2.2.5 |
| Files/search/changes | Retain query and mutation probes, their exact state checks and correlated continuations |
| Commands | Finish the uncommitted command fixture using task-4.3-debug.md: deterministic Windows PATHEXT and explicit PowerShell exit-code propagation |
| MCP | Add one local official-SDK stdio fixture; configure it only in the isolated host and invoke it via the actual host client |
| Evidence | Reuse recorder/report validation and family artifacts; expose one concise baseline result with versions, outcomes and limitations |
| Operator guide | Document existing direct-provider setup, baseline commands, and a separate explicit live procedure |

Paths above are relative to packages/opencode-vibe-oidc. Existing parent checks
in scripts/verify.ps1 and scripts/verification.py remain authoritative for the
shared library/provider. Add baseline integration only when native and MCP cases
are ready; do not weaken those existing gates to make the baseline pass.

## Python parallel execution

pytest-xdist is a locked development dependency. Default pytest options are
`-n 2 --dist=loadscope`; two workers give predictable resource use. Override with
`-n 0` for serial debugging or another explicit worker count when appropriate.

The pytest controller does not collect tests, so it restores original environment
and external-effect entry points before starting workers. Each worker installs
existing import-time browser/keyring/socket guards, creates its own temporary
homes and uses per-test temporary paths. Child fixture imports retain the same
guards. Preserve the prepared uv cache path for offline installation tests.

The canonical PowerShell runner accepts PythonWorkers (default 2, zero for
serial) and writes one aggregate JUnit report, consumed by the existing reader.
Keep native host/process probes separate from ordinary Bun tests. Do not run
several host probes concurrently merely because Python now uses xdist.

## Baseline workflows

### Native

Reuse verified read/glob/grep and write/edit cases. Keep conditional patch success
scoped to its synthetic fixture model. Finish shell stdout/stderr and exits 0/7,
with denied/invalid cases and independent state checks. Reuse one representative
abort/cleanup check across the workflow; no all-tools-by-all-negatives matrix.

### MCP

One fixture tool returns deterministic structured data from nested/Unicode input.
It also supports a deliberate tool error and cancellable work. Record negotiated
protocol/capabilities, actual discovery/call, result correlation and server cleanup.
Resource helpers, pagination, list changes and media are follow-ups unless needed
to make this specific tool workflow work. Never load private user MCP config.

### Evidence and claims

For each positive case retain the definition/call, actual execution outcome,
independent fixture observation and next model request. Preserve distinct call
IDs, primary request/run scope, errors and cancellation. Use existing helpers to
reject crossed evidence. Do not create another layer to certify those helpers.

A short report lists host/SDK/model/profile, native/MCP cases, outcomes, artifact
links and limitations. Baseline completeness means its declared cases passed;
it does not mean all registered tools were executed. Newly observed tools become
unverified follow-up rows and do not block an otherwise valid baseline.

## Deferred scope

Questions/forms, skills/subagents, session/worktree mutations, native web/search,
experimental images, broad non-text codecs, model-specific name/ID rewriting,
private plugin/memory services and cross-platform certification need a separate
observed need and follow-up. They are neither removed from OpenCode nor declared
supported. A Vibe-specific search service remains a separate feature.

## Execution and review

The remaining tasks are end-to-end deliverables, not a dispatch queue for every
helper. Use existing tests for regressions, add a focused failing test for a real
behavior change, then implement and run the relevant checks. Review each completed
deliverable once; rerun broader checks after fixes or at final integration, not
between every documentation/helper edit. Preserve any uncommitted legacy draft.

This revised feature design awaits review; xdist implementation is authorized by
the current request. No live account tests are part of this revision work.
