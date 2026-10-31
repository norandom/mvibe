# Offline consumer integration evidence

Task 8.1 checks the production public proxy factory and production OpenCode plugin entry point. It does not use a real account. Local startup, host compatibility and live compatibility have separate results.

## Reproduction

Use the already prepared uv environment and locked provider dependencies. From the repository root:

```powershell
uv run --locked --offline pytest tests/test_consumer_startup.py tests/test_startup.py tests/test_proxy.py tests/test_streaming.py
```

This writes `reports/local-startup.json`. The public factory runs its injected lifespan, verifies local health without an upstream probe, forwards synthetic tool bytes, rejects unsupported routes and closes its session. `main([])` is checked for `127.0.0.1:8765`; the report explicitly does not claim an actual proxy listening socket. Existing controlled ASGI streaming tests verify first chunk timing and response cleanup.

From `packages/opencode-vibe-oidc`, with an explicitly prepared host and absolute uv-managed interpreter:

```powershell
$env:OPENCODE_TEST_BINARY = (Resolve-Path .host/opencode.exe).Path
$env:VIBE_TEST_PYTHON = (Resolve-Path ../../.venv/Scripts/python.exe).Path
bun run test:consumer-host
```

The harness starts the actual host on an ephemeral numeric loopback port in a temporary profile. Its test-only plugin calls production `setupVibe`; no production remote input selects a fake mode. The child uses the real login CLI, NDJSON dispatcher and managed process transport with a synthetic public-session dependency. Test-only fetch rejects every destination except the synthetic configured audience and supplies controlled responses. Python blocks browser, credential-store, DNS and socket connections except the Windows stdlib socketpair needed by asyncio.

`reports/host-compatibility.json` records the actual SDK version, host version and binary SHA256. It verifies marker-only host credentials, authenticated model visibility, actual custom SDK/language hooks and fetch calls, changed snapshots on consecutive requests, first host text delta before upstream completion, exact synthetic tool-call argument JSON through native host events, and cancellation reaching the held transport. Tool IDs are correlated through the host's native start/end events because OpenCode remaps upstream IDs. It also checks both stateless generation and native session inference send an upstream 401 or 503 exactly once. Cleanup completes before success evidence is written. Missing evidence, timeout, cleanup failure or an assertion failure returns nonzero; checks are not skipped.

## Current V2 compatibility is a separate action

The installed desktop CLI was rechecked on 2026-10-31 and returned `opencode v2.2.5`. The reported 2.2.6 remains unverified. The tested pinned pair is host 2.2.5 / `@opencode/plugin` 2.2.5. Discovery during this task also returned stable npm CLI/SDK 2.2.5.

These commands are explicitly network enabled and are excluded from the offline suite:

```powershell
bun run compatibility:current --discover-current
bun run compatibility:current --prepare-current
```

Discovery writes `reports/current-v2-discovery.json` with compatibility initially unverified. Preparation requires matching stable V2 CLI/SDK releases, copies the adapter into a fresh ignored `.compat/<version>/provider` directory, installs exact direct dependencies there with lifecycle scripts disabled, records a generated lock and host hash, and runs strict typecheck, build and the same account-free host integration harness. It uses the platform package's prepared executable directly. The separate result is `reports/current-v2-compatibility.json`. Reusing that prepared directory and frozen lock supports offline reruns; preparation refuses to overwrite it. Future automatic host updates remain unverified until these checks execute. No request handler downloads dependencies.

## Defects demonstrated and repaired

The tests were written before these production fixes. Actual native-host failures established the following discrepancies:

1. New command sign-in produced `credential.updated` and `credential.switched`; listening only for `integration.updated` left authenticated discovery at zero requests. The catalog now observes all three relevant events. Unit regressions filter actual event types and exercise credential update/switch publication and disconnection.
2. The real helper reports distribution/API version `0.1.0`; the provider incorrectly expected major 3 from Vibe source provenance. The default handshake now expects the library's API major 0. Synthetic helper fixtures report `0.1.0`; explicit incompatible major rejection remains tested.
3. Native generation failed with `Provider package @ai-sdk/openai-compatible does not export model(modelID, settings)` and zero SDK-hook calls. The provider package now uses V2's `aisdk:` prefix, selecting the intended custom SDK route. [Tagged provider source](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/provider.ts) defines that routing distinction.
4. With SDK `maxRetries: 0`, native stateless 503 inference still sent four requests (`4 !== 1`). The provider response now adds `x-should-retry: false`, preserving upstream status, content and the same body stream. This is the explicit override consumed by [V2's one-shot retry policy](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/model-retry.ts) and the installed `@opencode/ai` retry classifier. [The SDK bridge](https://github.com/anomalyco/opencode/blob/v2.2.5/packages/core/src/aisdk.ts) preserves response headers in HTTP errors. Actual host tests verify one upstream attempt for both 401 and 503; unit tests additionally verify 429, immutable upstream headers and unchanged error payloads.

The final reports contain only synthetic check results and counters. Live login, entitlement, real inference, account refresh and compatibility with untested future versions remain unverified.
