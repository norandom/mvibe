# mvibe

Standalone local OpenAI-compatible proxy with browser OIDC sign-in. Requires Python 3.12+ and uv. No Vibe installation, Rust build, or symlinks required.

```powershell
uv run --script proxy.py
```

First launch opens the browser for Mistral sign-in. Credentials persist in the Windows credential store using Vibe's original keyring slot, and tokens refresh automatically. An existing Vibe login can be reused. Force a fresh browser login with:

```powershell
uv run --script proxy.py --login
```

OpenCode provider: use `@ai-sdk/openai-compatible` with baseURL `http://127.0.0.1:8765/v1`, a placeholder apiKey such as `local`, and a model ID returned by `GET /v1/models`. Supports chat completions (including streaming and tool calls), models, and embeddings. The Responses API is not implemented.

The proxy binds to 127.0.0.1 only (default port 8765; override with --port). It has no local authentication. Subscription and model access depend on your Mistral account. VIBE_CLI_OIDC_* environment overrides are supported, including organization/workspace selection. Avoid concurrent re-login from Vibe and the proxy because they share credentials.

The mvibe_auth directory contains authentication and HTTP helpers copied from Mistral Vibe 3.0.4, with imports adapted to a local package. Original license: LICENSE.vibe. Keep this directory alongside proxy.py when moving the proxy. Browser login and upstream inference require a live account and have not been verified by the automated checks.

## Python package setup and offline checks

The shared `mvibe-auth` distribution supports Python 3.12 or newer. The prepared
reference environment uses CPython 3.12.10 and uv 0.13.5. Distribution version
0.1.0 is independent of the copied Vibe 3.0.4 source version; the existing
transport version remains unchanged. `LICENSE.vibe` is included in built packages.
The public session facade is planned separately; packaging does not change the
existing authentication interfaces.

Online preparation (downloads interpreter/dependencies if needed):

```powershell
uv sync --locked --python 3.12
```

This installs the library plus development tools, without the optional proxy web
framework. Runtime, proxy-extra and test/build dependency versions are recorded
in `uv.lock`. Install the proxy extra explicitly with
`uv sync --locked --python 3.12 --extra proxy`. For a runtime-only library environment,
use `uv sync --locked --python 3.12 --no-dev --no-editable`.
The existing `uv run --script proxy.py` entry point remains available and uses its
own inline dependency metadata; it is a live account operation and is not the
locked verification command.

After online preparation, run the mandatory Python checks offline:

```powershell
uv run --locked --offline pytest
exit $LASTEXITCODE
```

A failed check returns a nonzero exit status. Missing cached dependencies or a
stale lock fail instead of downloading. These packaging checks import the library
in an isolated subprocess with credential, browser and socket access blocked,
and run an asyncio synthetic smoke. Windows' event-loop socketpair is prepared
before those guards. No browser login, account credentials or inference is used.
The complete consumer verification pipeline is added in later implementation tasks.

Build a wheel and source distribution using the prepared build dependencies:

```powershell
uv build --offline --no-build-isolation
exit $LASTEXITCODE
```

To verify a clean, non-editable library-only installation, use a separate environment
(the sync step may download dependencies; the subsequent smoke is offline):

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.venv-packaging'
try {
    uv sync --locked --python 3.12 --no-dev --no-editable
    if ($LASTEXITCODE -ne 0) { throw 'Library environment preparation failed' }
    & .\.venv-packaging\Scripts\python.exe tests/test_packaging.py
    if ($LASTEXITCODE -ne 0) { throw 'Installed library smoke failed' }
} finally {
    Remove-Item Env:UV_PROJECT_ENVIRONMENT
}
```

The last example assumes `UV_PROJECT_ENVIRONMENT` was initially unset. Keep the
checkout when using the script entry point; a library-only wheel contains the
shared library, not the standalone proxy script. The source distribution retains
that script and attribution.
