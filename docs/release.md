# Portable releases

The user has confirmed correct Vibe quota attribution for the locally installed
OpenCode existing-login route. See [authentication and evidence](vibe-authentication.md).
This does not replace native OS package checks or establish live proxy quota
acceptance. The proxy now defaults to the same shared existing-login session.

Release archives contain the version-matched universal Python wheel and bundled
JavaScript provider/installer. The provider shares OpenCode's plugin SDK and
Effect runtime, which remain external to its bundle. Install prerequisites explicitly: native
uv, Bun 1.4.4 and OpenCode 2. The release is portable across Windows, Linux and
macOS; Python and Python dependencies are resolved for your platform by uv.

Release assets include `mvibe-VERSION.zip`, the standalone
`mvibe_auth-VERSION-py3-none-any.whl` for Python library consumers, and
`SHA256SUMS` covering both. The archive contains the same wheel.

Download the archive and SHA256SUMS from the same GitHub release. Verify the
archive SHA256 against SHA256SUMS (`sha256sum` on Linux, `shasum -a 256` on macOS,
`Get-FileHash -Algorithm SHA256` on PowerShell), then extract to a permanent local
path, including paths with spaces. Keep this directory after installing: OpenCode
loads the provider and Python helper from it.

Run `bash ./install.sh` on Linux/macOS or `./install.ps1` on Windows. Both support
explicit executable paths (`--uv`, `--bun`, `--opencode` or `-Uv`, `-Bun`,
`-OpenCode`) and an explicit config file (`--config-path` or `-ConfigPath`). Use
`--help` / `-Help` for details. uv may download Python 3.12 and locked runtime
dependencies during setup. No source compilation, Bun dependency installation,
login, keyring access or browser launch occurs during release installation.

The installer preserves JSONC comments, unrelated settings and plugin options,
creates a backup before modifying existing configuration, and can be rerun.
New plugin entries explicitly select `authSource: "existing-vibe"`. Complete
normal Vibe browser sign-in, restart OpenCode, connect with **Use existing Vibe
login** and select a model. The shared Python library reads the native Vibe
keyring credential for each request; normal Vibe handles renewal. Connection
verifies that credential without opening a new browser flow or accepting manual
API keys. Keyring presence does not prove issuance or quota entitlement.
Rerunning the installer preserves existing `authSource` choices; existing entries
without this option keep the experimental OIDC ID-token route. Add
`"authSource": "existing-vibe"` deliberately to migrate those entries. Explicit
`"authSource": "oidc"` uses the separate encrypted OIDC store and refresh flow.
See opencode-provider.md in the source repository
for the complete operator and compatibility guide. Do not move/delete this
release directory while its plugin reference remains in your OpenCode config.
The optional proxy is included: run `uv run --offline --no-sync --project . python proxy.py --help`
from this release directory for its options. Authentication and inference remain
explicit operator actions.
To uninstall, remove that plugin entry from OpenCode settings before removing
the directory. To upgrade, extract the new archive into the same permanent
location and rerun its installer (retain the original config backup).

Maintainers build with `uv run --locked python scripts/build_release.py --output
./release-dist` after `bun install --frozen-lockfile --ignore-scripts` in the
provider package. Supply `--bun` with the native executable path on Windows.
The builder uses the root lock's version constraints to produce a separate
runtime-only lock. It copies only explicit public artifacts, excluding source
trees, node_modules, caches, credentials and development environments.

Validate a built archive on each native operating system with
`uv run --locked python scripts/test_release_install.py --archive
release-dist/mvibe-0.1.0.zip --bun /path/to/bun`. An optional `--opencode` selects
a prepared native OpenCode 2 executable; otherwise the smoke compiles a small
native CLI fixture. This fixture checks installer integration; real OpenCode
host compatibility remains a separate mandatory pipeline check. The smoke
isolates application homes, uses paths with spaces, checks backups and reruns,
imports the installed Python library without an account, and checks archive
contents. Each native pipeline must also run the actual OpenCode host integration
suite against the extracted provider via `VIBE_TEST_PROVIDER_ENTRY`; standalone
Bun registration mocks do not establish host compatibility. Dependency downloads are
permitted during preparation; account/network inference is never exercised.
