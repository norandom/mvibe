# Build and verify mvibe

These instructions are for maintainers working from the checkout root. Adopters
can start with the [README](../README.md) and [Desktop guide](desktop-setup.md).

The offline suite uses synthetic boundaries. It does not establish real account
entitlement, browser registration or inference. Once dependencies and a native
host are prepared, run the full pipeline from the checkout root:

```powershell
pwsh -NoProfile -File scripts/verify.ps1 -Bun C:/tools/bun.exe -HostBinary D:/Source/mvibe/packages/opencode-vibe-oidc/.host/opencode.exe
```

Use absolute paths to prepared Bun 1.4.4 and OpenCode 2 executables. Reports go
to `reports/verification/<run-id>/acceptance.json`. Focused Python checks use
`uv run --locked --offline pytest`; it uses two xdist workers with scope-based
scheduling. Each worker retains the offline guards and isolated state. Use
`uv run --locked --offline pytest -n 0 tests/test_refresh.py` for serial debugging,
or pass `-n 4` for a different worker count. The full PowerShell runner accepts
`-PythonWorkers 0` for serial execution (default: 2). Packaging uses
`uv build --offline --no-build-isolation`.

Run the [prepared native/MCP baseline and update checks](tool-compatibility.md)
for native files/commands, one MCP tool workflow, contract drift and actual host evidence.
Broader tool families remain explicit follow-ups, not an all-tool support claim.

- [Shared auth API and lifecycle](auth-library.md)
- [Direct OpenCode plugin setup, build and compatibility](opencode-provider.md)
- [Tool baseline, candidate updates and diagnostics](tool-compatibility.md) — logging defaults off; enable proxy `--log` (stdout) or plugin `options.diagnostics: true` (stderr).
- [Live procedure and evidence checklist](live-verification.md)
- [Live results](live-results.md)
- [Offline acceptance](offline-acceptance.md)
- [Synthetic native host evidence](consumer-integration.md)
- [Vibe source attribution and deviations](vibe-provenance.md)

For the runtime-only library, prepare with
`uv sync --locked --python 3.12 --no-dev --no-editable`; add `--extra proxy` when
running the proxy. `uv run --script proxy.py` uses separate inline dependencies
and may download them. When copying that script, retain `mvibe_auth/`,
`mvibe_proxy/` and `THIRD_PARTY_NOTICES`. See `uv.lock` for dependency versions.

## Linux and rolling OpenCode updates

The reference pair is now OpenCode CLI/SDK **2.3.0**. Roll forward by updating both
`@opencode/plugin` and `@opencode/cli`, regenerating the Bun lock, then running the
unit, real-host and tool baseline checks. Keep historical contract snapshots;
review the new contract differences before adding a new versioned reference.
A successful typecheck alone does not establish host/tool behavior.

With Bun 1.4.4 on PATH, run from the checkout root:

```bash
uv sync --locked --extra proxy --extra docker
uv run --script proxy.py --help  # prepare the separate script dependency cache
uv run --locked --offline pytest
uv run --locked --offline ruff check .
uv build --offline --no-build-isolation
cd packages/opencode-vibe-oidc
bun install --frozen-lockfile --ignore-scripts
bun run typecheck
bun run build
bun run lint
bun test
export OPENCODE_TEST_BINARY="$(command -v opencode)"
export VIBE_TEST_PYTHON="$PWD/../../.venv/bin/python"
bun run test/host-integration.ts
bun run test:tool-baseline
```

Python interpreter symlinks are supported. Shell fixtures select native POSIX or
PowerShell syntax; the Linux process supervisor lets the host reap children
before its bounded process-group cleanup fallback.

Verified on Linux x86_64 on 2026-11-17: Python 3.14.4, Bun 1.4.4,
OpenCode CLI/SDK 2.3.0, and rootless Podman 5.7.0. Python: 2,024 passed,
5 optional checks skipped. Provider: 1,460 passed; typecheck, lint, both package
builds, actual host integration, and native query/mutation/shell/MCP baseline
passed. Evidence is in `reports/linux-python.xml`,
`reports/host-compatibility.json`, and `reports/linux-tool-baseline-final/baseline.json`.
The Python 3.12 container built, became healthy, and returned 65 models using the
exported native Vibe credential. Live inference and quota attribution were not
retested. Windows/macOS and ARM were not executed in this Linux run; the Dockerfile
currently embeds an x86_64 mmhmem binary.
