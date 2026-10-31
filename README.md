# mvibe

Shared Vibe browser OIDC authentication for a local OpenAI-compatible proxy and
the direct OpenCode 2 `vibe-oidc` provider. Python 3.12+ and uv are required;
use uv-created environments throughout. No Vibe installation, Rust build or
symlinks are needed. Real browser login, subscription entitlement and upstream
inference remain unverified by the offline suite.

- [Library API and lifecycle](docs/auth-library.md)
- [Direct OpenCode provider and loopback client setup](docs/opencode-provider.md)
- [Vibe source hashes, license and deviations](docs/vibe-provenance.md)
- [Synthetic native host and startup evidence](docs/consumer-integration.md)

## Prepare Python

Online preparation may download the interpreter and dependencies:

```powershell
uv sync --locked --python 3.12
```

This prepares the library, test/build tools and proxy dependencies. For the
runtime-only library use `uv sync --locked --python 3.12 --no-dev --no-editable`;
FastAPI/Uvicorn are absent. To run the proxy without development dependencies use
`uv sync --locked --python 3.12 --no-dev --no-editable --extra proxy`.
The reference Python is CPython 3.12.10 with uv 0.13.5; resolved dependencies are
in `uv.lock`. Distribution/API version 0.1.0 is separate from copied Vibe 3.0.4
source provenance and private bridge protocol 1 (accepted API major 0).

## Run the proxy

After proxy preparation, the following uses installed cached dependencies.
Offline dependency resolution does **not** prevent this operator action from
opening a real browser or making account requests:

```powershell
uv run --locked --offline proxy.py
```

Startup reuses stored matching credentials or opens browser sign-in before
serving. Force re-login with `--login`; override port with `--port`.
`uv run --script proxy.py` remains available with its own inline PEP 723
dependencies (first use may download). When copying the script, retain both
`mvibe_auth/` and `mvibe_proxy/` alongside it and retain `LICENSE.vibe`.
A library wheel contains the two packages; the source distribution also retains
`proxy.py`. A library-only environment cannot run the proxy until its extra is
installed.

Point an OpenAI-compatible client at `http://127.0.0.1:8765/v1`, use a placeholder
`apiKey` such as `local` if required by its SDK, and select a model ID returned by
`GET /v1/models`. The placeholder is ignored upstream. Supported operations are
models, chat completions (streaming/tool-call payloads) and embeddings; Responses
API is unsupported. Local caller authentication is absent. `GET /health` is local
liveness only, not credential/upstream readiness. Account entitlement determines
models and capabilities.

`VIBE_CLI_OIDC_*` overrides select issuer/client/audience/callback/scope and tenant.
The proxy opts in when `VIBE_CLI_OIDC_ENABLED` is absent; explicit disablement
wins. Standalone bridge/login commands also opt in when absent; library users
must supply an enabled configuration. Sessions capture
configuration at construction. Inference uses ID-token and tenant headers;
UserInfo uses the distinct access token. No API-key fallback exists.

Vibe, proxy and plugin share the same OS credential slot. Avoid concurrent
re-login: installed Vibe may ignore library coordination and overwrite a value.
No inference replay or redirect following occurs. Stream cleanup is shielded
with a two-second response-close deadline and a separate two-second client-close
deadline. Failed cleanup retires the client; restart the proxy after sanitized
502 failures. Health can remain healthy during retirement.

## Prepare and verify the OpenCode plugin

The [provider guide](docs/opencode-provider.md) gives the production plugin
configuration and explicit uv-managed `helperCommand`. Prepare Bun **1.4.4**,
then from `packages/opencode-vibe-oidc` run:

```powershell
bun install --frozen-lockfile --ignore-scripts
bun run typecheck
bun run build
```

The reproducible reference pair is OpenCode CLI / `@opencode/plugin` **2.2.5**,
with `@ai-sdk/openai-compatible` **2.0.88**. User-reported OpenCode **2.2.6** is
unverified. Use the native executable from the explicitly prepared CLI package,
`node_modules/@opencode/cli-windows-x64/bin/opencode.exe` on Windows x64, or copy a
verified 2.2.5 binary with `OPENCODE_TEST_SOURCE` and `bun run prepare:host`.
Do not point tests at an active user service/profile. Current stable OpenCode 2
updates are a separate explicit action: `bun run compatibility:current
--discover-current`, then `bun run compatibility:current --prepare-current`.
These network-enabled actions never run inside the offline verifier. Discovery
alone does not prove compatibility; unknown host versions remain unverified.

## Offline checks

After preparation, run the complete mandatory pipeline from the checkout root.
Pass absolute paths to your prepared Bun and native host:

```powershell
pwsh -NoProfile -File scripts/verify.ps1 -Bun C:/tools/bun.exe -HostBinary D:/Source/mvibe/packages/opencode-vibe-oidc/node_modules/@opencode/cli-windows-x64/bin/opencode.exe
exit $LASTEXITCODE
```

The runner installs nothing from the network. It executes Python tests, Bun
tests/typecheck/build, helper process lifecycle and isolated native host/startup
checks, then validates every requirement's executed or documentary evidence.
Results go to `reports/verification/<run-id>/acceptance.json`; missing, stale,
skipped or failed mandatory checks cause nonzero exit. Offline evidence and local
startup are separate from live compatibility; unexercised live checks remain
unverified. Until the separate live-procedure artifacts are delivered, their
required documentary mappings also keep overall acceptance incomplete.

Focused Python checks and packaging build use the canonical commands:

```powershell
uv run --locked --offline pytest
exit $LASTEXITCODE
```

```powershell
uv build --offline --no-build-isolation
exit $LASTEXITCODE
```

`tests/test_installation.py` copies the source to a temporary directory, runs
locked offline uv sync in a clean no-dev non-editable environment, proves web
dependencies are absent, checks installed bridge hello/protocol errors, installs
the proxy extra and exercises both packaged and PEP 723 `--help` entry points.
`tests/test_auth_lifecycle.py` executes the library guide's public examples with
synthetic boundaries. All default verification avoids real keyring, browser,
sign-in and remote services. Missing prepared dependencies fail rather than
silently downloading. Build/source capture retains Apache attribution in
`LICENSE.vibe` and the original authentication helpers.
