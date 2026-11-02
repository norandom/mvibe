# mvibe 1.0.0 — first major release

For students, researchers and educators only. This is untested, experimental
software, not production software. Original mvibe contributions use the Beerware
license; upstream Vibe code retains Apache 2.0 licensing.

Use your normal native Vibe browser login from the **Mistral-Vibe** OpenCode
provider or a local OpenAI-compatible proxy. Both share the Python authentication
library and default to reading Vibe's existing keyring credential, without manual
API-key entry. Experimental direct ID-token OIDC remains a separate opt-in route;
it counted as API usage on the tested account. The existing-login route consumed
the expected Vibe allowance in user-confirmed OpenCode tests.

Download `mvibe-1.0.0.zip` and `SHA256SUMS`, verify the checksum, then extract to a
permanent directory outside a development checkout. Complete normal Vibe browser
sign-in under the same native OS/user first. With uv, Bun 1.4.4 and OpenCode 2 installed:

- Linux/macOS: `bash ./install.sh --interactive`
- Windows PowerShell: `./install.ps1 -Interactive`

Installers preserve existing JSONC settings and create backups. They use prebuilt
Python/JavaScript artifacts; uv may download managed Python 3.12 and locked runtime
dependencies. Choose Desktop, CLI or both and the existing Vibe login. Unattended
flags are documented in the bundled README. Keep the extracted directory after
installation. Restart the OpenCode service and fully quit/reopen Desktop, connect
**Mistral-Vibe**, then select a model. Ctrl+C and `opencode -c` can reuse the same
service; after an upgrade use `opencode service restart`. Avoid editing/building
the loaded installation: host SDK caches can retain a disposed helper after reload.

The standalone wheel is also available for Python library consumers. The archive
includes the optional proxy with `/openapi.json`, Swagger `/docs`, and opt-in
`--log` diagnostics. Plugin diagnostics use `options.diagnostics: true`; the CLI
also needs `--print-logs` for visible events.

## New in 1.0.0

- **Host binding, launchers and Docker**: `proxy.py --host` (default `127.0.0.1`;
  `0.0.0.0` allowed with a stderr warning — the proxy has no caller auth),
  `start_proxy.sh` / `start_proxy.ps1`, and `Dockerfile` + `docker-compose.yml`
  with loopback-published port, non-root container, healthcheck and read-only
  credential mounts.
- **Observability**: `--observe` flag and `MVIBE_OBSERVE_*` env export batched
  logs to an OpenObserve-style `_json` endpoint; optional `otel` extra adds W3C
  trace spans (`proxy.request`, `proxy.stream`, `auth.resolve`, `bridge.call`).
  See `docs/observability.md`. Exports are allowlisted: no bodies, headers, tokens
  or URLs — only the model id.
- **Endpoint compatibility adapters**: Ollama (`/api/version|tags|chat|generate|
  show|embed`, NDJSON streaming) and Anthropic (`POST /v1/messages` SSE streaming,
  `GET /v1/models` Anthropic shape when an `anthropic-version` header is sent).
  Raw `/v1/*` passthrough is unchanged. See `docs/proxy-guide.md`.
- **Reusable live smoketests**: `smoketests/smoke.ps1` (Windows) and
  `smoketests/smoke.sh` (bash) cover all three dialects plus embeddings and exit
  non-zero on failure.

## Verification status

CI builds/checks with Dagger and tests native Linux, Windows and macOS installation
plus the extracted provider in OpenCode 2.2.5 using synthetic accounts. Live
verification on 2026-11-02 through the local proxy passed for health, model lists
(both dialects), chat (stream and non-stream), Anthropic messages (SSE sequence),
Ollama chat, the adapter 404 shape, and embeddings (`mistral-embed`, 1024-dim).
Live Large 4 hello-world file-tool continuation was verified on Windows. Still
unverified: native/MCP tool exchange, token refresh, cancellation timing, search
execution, other models, OpenCode 2.2.6 (tested host: 2.2.5), direct-provider
embeddings, and proxy quota attribution. The checks below establish a limited
baseline, not general reliability or production readiness.
No signing/notarization or PyPI/npm publication is included.

See [installation](https://github.com/norandom/mvibe/blob/main/docs/release.md)
and [compatibility checks](https://github.com/norandom/mvibe/blob/main/docs/tool-compatibility.md).
