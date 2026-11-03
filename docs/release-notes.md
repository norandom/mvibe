# mvibe 2.0.0 — second major release

For students, researchers and educators only. This is untested, experimental
software, not production software. Original mvibe contributions use the Beerware
license; upstream Vibe code retains Apache 2.0 licensing.

Use your normal native Vibe browser login from the **Mistral-Vibe** OpenCode
provider or a local OpenAI-compatible proxy. Both share the Python authentication
library and default to reading Vibe's existing keyring credential, without manual
API-key entry. Experimental direct ID-token OIDC remains a separate opt-in route;
it counted as API usage on the tested account. The existing-login route consumed
the expected Vibe allowance in user-confirmed OpenCode tests.

Download `mvibe-2.0.0.zip` and `SHA256SUMS`, verify the checksum, then extract to a
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

## New in 2.0.0

- Major version bump to 2.0.0. There are no breaking changes and no code changes
  since v1.1.0 beyond version metadata; this release carries the full v1.x
  feature set, including the curated thinking variants from v1.1.0 and the
  host binding, launchers, Docker packaging, observability and endpoint
  adapters from v1.0.0.
- Version metadata moved 1.1.0 → 2.0.0 in `pyproject.toml`,
  `packages/opencode-vibe-oidc/package.json` and `uv.lock`. Because the
  helper reports its distribution version as the API version, the provider's
  expected helper API major moved 1 → 2 with the release: v2.0.0 installs
  the v2 helper and provider together from the same archive, and v1.x
  helpers are rejected as incompatible. A repository test now enforces that
  the release major and the helper API major stay in lockstep.

## New in 1.1.0

- The OpenCode **Mistral-Vibe** provider exposes curated thinking variants:
  Large 4 offers `low`, `high`, `max`; Medium 3.5 aliases offer `high`, `off`;
  GLM 5.3's exact curated aliases offer `low`, `high`, `max`.
- Selected variants use the host's `settings.reasoningEffort` overlay, which
  reaches Mistral as `reasoning_effort`. Off maps to `none`. No selection keeps
  the server default. Models without a curated row gain no invented variants.
- Assistant reasoning history is replayed only for curated reasoning models
  when reasoning is not explicitly off; ordinary content and tool history survive.
- The proxy maps Anthropic `thinking` and Ollama `think` request options.
  Malformed options add no effort. OpenAI passthrough is unchanged. These are
  request mappings, not complete Anthropic/Ollama reasoning-output or history
  compatibility; see the support table in `docs/proxy-guide.md`.

The native Vibe login and quota policy are unchanged. Upgrade from the published
archive and restart the OpenCode service; v1.0.0 does not contain these variants.

## Verification status

The v2.0.0 tree is code-identical to the v1.1.0 candidate, which passed Windows source and extracted-bundle OpenCode 2.2.5
checks for model variants and outgoing `reasoning_effort` (`low`, `high`, `max`,
`off` to `none`, and no selection). Native archive installation, JSONC backup
and rerun checks passed in an isolated path with spaces. These checks use
synthetic accounts and requests; they do not establish live acceptance of each
model/level pair. Quoted paths, backslashes, newlines and Unicode survive the
reasoning/tool-history regression; the earlier reported shell issue has not
been reproduced as a provider escaping defect.

The release pipeline builds/checks with Dagger and gates publication on native Linux, Windows and macOS installation
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
