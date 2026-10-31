# Provider development environment

This package currently exports the V2 SDK `Plugin` namespace as a packaging surface. The synthetic plugin in `test/` is a development fixture. Production Vibe provider/authentication behavior belongs to subsequent tasks.

Verified on Windows x64 with Bun 1.4.4, TypeScript 5.9.3, `@opencode/plugin` 2.2.5 and the bundled OpenCode CLI reporting `opencode v2.2.5`. The user reports desktop 2.2.6; that version has not been tested. OpenCode 2 remains the target release line, including automatic updates. New versions remain unverified until compatibility checks run.

`@ai-sdk/openai-compatible` 2.0.88 exposes the LanguageModelV3 contract used by this host. Adapter major 3 currently exposes V4 and is unsuitable for this pair. All resolved versions and integrity hashes are in `bun.lock`. `effect` and SDK modules remain external JavaScript imports; the OpenCode loader supplies its shared runtime instances.

## Explicit preparation

Run from this directory. Install Bun 1.4.4 separately, then prepare dependencies with network access explicitly enabled:

```powershell
bun install --frozen-lockfile
$env:OPENCODE_TEST_SOURCE = 'C:/Users/mariu/AppData/Local/Programs/OpenCode/resources/opencode-cli.exe'
bun run prepare:host
```

`prepare:host` checks the source binary's version and copies it into `.host/`, recording its SHA256. It never launches an active user service. A separately downloaded matching 2.2.5 CLI executable can be supplied instead. The locked `@opencode/cli` package records the host dependency, but its install-time platform/postinstall behavior is not relied on for copying the test binary. Missing executables or mismatched versions fail with setup guidance. Source binaries are never downloaded during tests or requests.

## Offline verification

After preparation, disconnect external network access if an OS-level network boundary is required. These commands install nothing:

```powershell
bun install --frozen-lockfile --offline --ignore-scripts
bun test
bun run typecheck
bun run build
$env:OPENCODE_TEST_BINARY = (Resolve-Path .host/opencode.exe).Path
bun run test:host
```

The smoke uses a fresh temporary project, home, XDG paths, app-data directories and private `--standalone` host; it supplies a minimal environment with no account/provider credentials, disables auto-update and catalog fetch, and deletes only that test directory afterward. The fixture registers provider/integration transforms, a command method emitting a fixed nonsecret marker, and a provider-scoped SDK hook. It exposes one synthetic model and rejects any synthetic inference fetch. It performs no login or inference and does not invoke the marker command. The real host smoke proves loading, transform execution, scoped hook registration and model visibility. Unit checks prove the returned cleanup disposes all registrations; the short-lived `models` CLI does not execute that cleanup before exiting. Real login-marker execution, SDK/fetch invocation, streaming, cancellation and host unload are later integration gates (tasks 7 and 8.1).

## Compatibility preparation for updated OpenCode 2

This is a separate **network-enabled** action, never a request path:

```powershell
bun info @opencode/cli version
bun info @opencode/plugin version
```

Use the official registry/release information to distinguish filtered package-manager results from upstream latest. Prepare a separate checkout/profile with the exact candidate SDK/CLI versions and matching binary; update that checkout's manifest/lock and the explicit expected host version in preparation/smoke, then run the same checks above. Keep the production checkout's verified lock unchanged until review. Record both actual binary output and SDK version. A version number alone does not establish compatibility. Current setup smoke is intentionally narrower than the future full hook/login-marker/fetch/stream/cancellation suite: candidate releases cannot receive that full compatibility claim until those later gates exist and pass.

V2 API references: [server plugins](https://opencode.ai/v2/docs/build/plugins/server), [V1 migration](https://opencode.ai/v2/docs/build/plugins/migrate-v1). Python regressions from the repository root: `uv run --locked --offline pytest` and `uv build --offline --no-build-isolation`.
