# First mvibe release

Use Vibe OIDC authentication from the **Mistral-Vibe** OpenCode provider or a local
OpenAI-compatible proxy. Both share the same Python authentication library and
encrypted credential store. No Mistral API key is required.

Download `mvibe-0.1.0.zip` and `SHA256SUMS`, verify the checksum, then extract to a
permanent directory. With uv, Bun 1.4.4 and OpenCode 2 installed:

- Linux/macOS: `bash ./install.sh`
- Windows PowerShell: `./install.ps1`

Installers preserve existing JSONC settings and create backups. They use prebuilt
Python/JavaScript artifacts; uv may download managed Python 3.12 and locked runtime
dependencies. Keep the extracted directory after installation. Restart OpenCode,
connect **Mistral-Vibe**, then select a model in `/models`.

The standalone wheel is also available for Python library consumers. The archive
includes the optional proxy with `/openapi.json`, Swagger `/docs`, and opt-in
`--log` diagnostics. Plugin diagnostics use `options.diagnostics: true`; the CLI
also needs `--print-logs` for visible events.

CI builds/checks with Dagger and tests native Linux, Windows and macOS installation
plus the extracted provider in OpenCode 2.2.5 using synthetic accounts. Live Large
4 hello-world file-tool continuation was verified on Windows. Live MCP, other
models/tools, refresh, other architectures and future OpenCode updates remain
unverified. No signing/notarization or PyPI/npm publication is included.

See [installation](https://github.com/norandom/mvibe/blob/main/docs/release.md)
and [compatibility checks](https://github.com/norandom/mvibe/blob/main/docs/tool-compatibility.md).
