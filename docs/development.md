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
`mvibe_proxy/` and `LICENSE.vibe`. See `uv.lock` for dependency versions.
