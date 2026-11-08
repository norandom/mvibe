# Research & Design Decisions

## Summary
- **Feature**: `plugin-registration-resilience`
- **Discovery Scope**: Extension (existing TypeScript provider plugin, `packages/opencode-vibe-oidc`)
- **Key Findings**:
  - Live incident (2026-11-08): provider registration took 7–15 minutes or never completed on the real OpenCode 2.2.6 service; isolated probes register in ~4s. Root cause is `setupVibe()` awaiting the first `catalog.refresh()`, which performs helper spawn + keyring + a live `/models` HTTP fetch with no deadline.
  - The only unbounded step is the discovery HTTP fetch (`models.ts:82`); the bridge protocol is already deadline-bounded (hello 10s, `status` 10s, `resolve` 180s). The discovery fetch additionally goes through a per-request `resolve` (`provider.ts:55`).
  - `catch {}` blocks discard real causes in three places: `index.ts:67-69` (setup), `models.ts:103-104` (discovery fetch), `models.ts:161-164` (catalog run). Diagnostics surface only via `report → console.warn` (`index.ts:37`).
  - Bridge abort semantics are session-fatal: aborting an in-flight request retires the whole helper session (`bridge.ts:173-190`), which would also fail unrelated in-flight chat `resolve` calls sharing the session.

## Research Log

### Live incident root cause (2026-11-08)
- **Context**: User reported "plugin takes very long to load, and now does not load anymore at all".
- **Sources Consulted**: `~/.local/share/opencode/log/opencode.log` (runs a0ac3c5f, 121afd90), live `/api/provider/vibe-oidc` queries, isolated standalone probes (release v1.1.0 and dev v3.0.1 on host 2.2.6), `uv run --offline --no-sync` timing (1.4s), process inspection.
- **Findings**: Host switched to npm `@opencode/cli` 2.2.6 (installed 2026-11-08 08:01). Plugin module imports cleanly; isolated probes register `vibe-oidc` in ~4s. Real service: booted 17:46:30Z, 404 at ~17:54Z, registered by ~18:01Z. Registration is per-location; each location boot re-runs plugin setup.
- **Implications**: Not a host-API break. The blocking `await catalog.refresh()` at `index.ts:66` (also release v1.1.0 `dist/index.js:14837`) is the defect: cold helper/keyring/network delays registration by minutes, and an unbounded fetch hang prevents it entirely.

### Catalog and bridge flow analysis
- **Context**: Where exactly can activation block, and what is already bounded?
- **Sources Consulted**: `src/models.ts` (110–183), `src/bridge.ts`, `src/provider.ts`, `src/index.ts`, `src/request-context.ts`; test evidence in `test/bridge.test.ts`, `test/models.test.ts`, `test/host-integration-plugin.ts`.
- **Findings**: `createModelCatalog.run()` per attempt: `active()` → optional invalidate+reload → `bridge.request("status")` (10s bound) → `discoverInventory` → `fetch(baseURL+"/models")` (no bound; internally a `resolve` bridge call, 180s bound) → publish + `input.reload()`. Generations coalesce attempts; `close()` aborts. `snapshot` returns `[]` until first publish. Bridge: lazy single helper process, hello 10s, per-method timeouts, abort = session retire.
- **Implications**: A deadline placed at the catalog-attempt level covers all steps; it must not abort the shared bridge session on timeout (chat `resolve` calls share it).

### Diagnostics channel reality
- **Context**: Requirement 3 needs failure causes visible in the host log.
- **Sources Consulted**: `provider.ts:30-131`, `index.ts:37,64`, `test/host-integration-plugin.ts:28-42`, README grep.
- **Findings**: Plugin→host visibility channels are `console.warn` (catalog messages) and opt-in `console.error` JSONL per request (`options.diagnostics`). The host-integration harness monkey-patches `console.warn` inside the host process — this is the sanctioned, test-observable channel. CLI flag `--print-logs` is required for stderr visibility from `opencode api`-style clients.
- **Implications**: Keep `report → console.warn` as the channel; ensure cause text is included there. Documenting the flag belongs in README/release notes.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| A. Await refresh, add fetch timeout only | Keep blocking setup; bound the fetch | Minimal change | Still delays registration by helper+keyring+network (seconds to minutes); violates 1.1/1.2 | Rejected |
| B. Register-first + background refresh, timeout via abort | Remove await; on timeout abort the attempt's signal | Simple; reuses abort plumbing | Abort retires the shared bridge session → kills in-flight chat resolve calls | Modified → D |
| C. Register-first + detached deadline | Remove await; on timeout abandon the attempt without aborting; discard late results | No cross-request damage; simple generation logic | One dangling fetch per timed-out attempt until it settles; timer must be cleaned on close | Selected |
| Host-side change (publish providers before setup completes) | Fix in OpenCode host | Addresses class of problem | Out of boundary (R5.2); not controllable by this repo | Rejected |

## Design Decisions

### Decision: Register-first, non-blocking setup trigger
- **Context**: 1.1/1.2 — registration must not wait on helper/network.
- **Alternatives Considered**: 1) await refresh (status quo) 2) register-first with fire-and-forget first refresh.
- **Selected Approach**: `setupVibe()` performs all registrations, then starts the first refresh without awaiting it; setup returns after synchronous registration work only.
- **Rationale**: Registrations (`index.ts:47-60`) are already synchronous host transforms; only the trailing `await` blocks.
- **Trade-offs**: Provider initially visible with zero models (per user decision) until first discovery publishes; background failure reporting relies on the existing `report` channel.
- **Follow-up**: setup tests must assert setup resolves while refresh is still pending; host-integration asserts provider query succeeds before discovery completes.

### Decision: Deadline by detachment, not session-fatal abort (user-visible decision)
- **Context**: 2.1/2.2 — bounded attempts, keep last-good list.
- **Alternatives Considered**: Abort controller on timeout (session-fatal per `bridge.ts`) vs abandon-and-discard.
- **Selected Approach**: Each catalog attempt gets a wall-clock deadline (default 30s) via an injectable timer (mirroring `bridge.ts` io injection). On expiry the attempt is abandoned: its generation is marked stale, a timeout diagnostic is reported, last-good snapshot is kept, and the late result is discarded when it eventually settles. `close()` keeps its abort semantics.
- **Rationale**: Aborting would retire the shared helper session and fail unrelated in-flight chat requests — trading a slow catalog for broken chats.
- **Trade-offs**: A hung TCP fetch lingers until it settles; bounded to one per attempt; no retry storm (next trigger reuses generation coalescing).
- **Follow-up**: timer cleanup on close and on supersede; test that close() during a pending attempt does not leak the timer.

### Decision: Empty model list until authenticated discovery (user decision 2026-11-08)
- **Context**: R1.3 amendment — baseline-from-host-catalog vs empty-until-discovery.
- **Selected Approach**: Register with empty list + "discovery pending" diagnostic; entitlement evidence continues to come exclusively from the authenticated upstream inventory (existing `models.ts` policy comment).
- **Rationale**: Host catalog entries are not entitlement evidence; showing them could expose models the Vibe account cannot use.
- **Trade-offs**: Model picker shows no Mistral-Vibe models for the first seconds after startup (healthy path); with unreachable upstream it stays empty with a visible diagnostic instead of a missing provider.

### Decision: Failure classification and cause propagation
- **Context**: 3.1–3.3 — real causes in the host log; no generic substitution.
- **Selected Approach**: `setupVibe` wraps each registration step with a step label and throws `BridgeError` carrying `cause` and a message naming the step; `discoverInventory`'s catch classifies the error (timeout/unreachable/invalid) and includes the underlying message; `run()`'s catch includes the attempt's failure class. `report` messages gain a structured prefix but stay single-line warn text.
- **Rationale**: Minimal, typed change; no new logging dependency; secrets policy unchanged (fetch/bridge errors carry no credentials; diagnostics never include tokens, headers, prompts or bodies — existing allowlist policy in steering and `docs/vibe-provenance.md`).
- **Trade-offs**: Error text is best-effort classification, not a taxonomy overhaul.
- **Follow-up**: unit tests assert cause presence and classification strings; secret-scan existing diagnostics tests stay green.

## Risks & Mitigations
- Detached attempts accumulate under a pathological upstream that accepts connections but never responds — Mitigation: generation coalescing means at most one dangling fetch per trigger; document in README.
- Host publishes providers only after setup completes (observed 2.2.x behavior) — Mitigation: this design removes all IO from setup, so the gate is irrelevant; recorded as revalidation trigger if host semantics change.
- `console.warn` visibility differs by client (TUI vs `opencode api`) — Mitigation: document `--print-logs` in README/release notes.
- Release coupling: no protocol/HELPER_API_MAJOR change → patch/minor release (e.g. 3.0.2/3.1.0) suffices; `tests/test_version_coupling.py` unchanged — verify at release time.

## References
- Live evidence: `~/.local/share/opencode/log/opencode.log` runs a0ac3c5f/121afd90 (2026-11-08); probe scripts `C:\Users\mariu\AppData\Local\Temp\opencode\mvibe-probe\`.
- Code: `packages/opencode-vibe-oidc/src/{index,models,bridge,provider}.ts`; tests `test/{models,setup,registration,host-integration-plugin}.test.ts`/`.ts`.
- Steering: `.kiro/steering/{product,tech,structure}.md`; `docs/vibe-provenance.md` (diagnostics allowlist); tech.md release-versioning invariant.
