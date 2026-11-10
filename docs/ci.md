# Build, check and release

The GitHub Actions workflow runs the same Dagger Python SDK pipeline used
locally. Dagger 0.21.8 and SDK dependencies are pinned; no Dagger Cloud account is
required. A container engine must be running. On this Windows workstation the
engine is Podman (the `docker` command is Podman's shim) and the Dagger CLI runs
natively; see `.kiro/steering/tech.md`, "Local container tooling":

```powershell
Set-Location D:\Source\mvibe
$env:UV_PROJECT_ENVIRONMENT = 'D:\BuildArtifacts\mvibe-dagger-runtime'
dagger run uv run --locked --project .dagger --python 3.12 python .dagger/ci.py check
dagger run uv run --locked --project .dagger --python 3.12 python .dagger/ci.py build
```

On Linux the same two `dagger run ...` commands apply from the repository root;
the explicit environment location is optional there. Build output is
`dist/release`: portable ZIP, universal wheel and SHA256SUMS.

`check` runs Ruff correctness checks, ShellCheck (`install.sh` and
`start_proxy.sh`), actionlint, ESLint, TypeScript checks/build, Bun tests, the
Python suite with two pytest-xdist workers, and builds the `Dockerfile` proxy
image to verify the embedded `mmhmem` binary version and `proxy.py --help`
entrypoint.
Ruff does not impose a formatting migration. ESLint keeps TypeScript's name/type
checks in tsc and documents narrow exceptions for deliberate cleanup failures
and redacted exceptions. PowerShell scripts use pinned PSScriptAnalyzer 1.25.0:

```powershell
Install-Module PSScriptAnalyzer -RequiredVersion 1.25.0 -Scope CurrentUser
./scripts/lint.ps1
```

Actions first builds/checks in Dagger on Linux, then runs native regression tests
and the exact same archive's install smoke on Ubuntu 24.04 x64, Windows 2025 x64
and macOS 15 arm64. It also loads the extracted JavaScript provider through the
actual OpenCode 2.2.5 native host, using synthetic authentication/inference and
isolated homes. No live credentials, browser login or account access are needed.
Installer tests exercise JSONC preservation, backups, idempotency and paths with
spaces. Other architectures are not native-tested by this matrix.

The provider bundle leaves OpenCode's SDK and Effect runtime external as required
by its loader; the installer bundles its own JSONC dependency. Python dependencies
are installed from the release's lock and prebuilt wheels, never compiled by the
release installer. uv/Bun/OpenCode remain explicit user prerequisites.

Pushes to main, pull requests and manual dispatch run all gates without publishing.
A `vVERSION` tag publishes only after every build/native job passes and the tag
matches the Python version. The builder also verifies Python/provider versions
match. Release creation verifies checksums and the existing remote tag. Only the
publication job has contents-write permission; third-party actions are SHA-pinned.
No PyPI/npm upload, code signing or automatic baseline refresh is configured.

For a future release, update Python/provider versions and their locks together,
run the checks, then push the matching tag. Never move an existing release tag
or replace an already-published artifact silently. See [user installation](release.md).
