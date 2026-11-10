# mvibe 3.1.1 — upstream rate-limit queueing and proxy stats

For students, researchers and educators only. This is untested, experimental
software, not production software. Original mvibe contributions use the Beerware
license; upstream Vibe code retains Apache 2.0 licensing.

Use your normal native Vibe browser login from the **Mistral-Vibe** OpenCode
provider or a local OpenAI-compatible proxy. Both share the Python authentication
library and default to reading Vibe's existing keyring credential, without manual
API-key entry. Experimental direct ID-token OIDC remains a separate opt-in route;
it counted as API usage on the tested account. The existing-login route consumed
the expected Vibe allowance in user-confirmed OpenCode tests.

Download `mvibe-3.1.1.zip` and `SHA256SUMS`, verify the checksum, then extract to a
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
service; after an upgrade use `opencode service restart`. Avoid editing/building the
loaded installation: host SDK caches can retain a disposed helper after reload.

The standalone wheel is also available for Python library consumers. The archive
includes the optional proxy with `/openapi.json`, Swagger `/docs`, and opt-in
`--log` diagnostics. Plugin diagnostics use `options.diagnostics: true`; the CLI
also needs `--print-logs` for visible events.

## New in 3.1.1

- **First published release of the 3.1 line.** The `v3.1.0` tag was never
  published: its release checks failed in the native OpenCode host test.
- **Plugin: concurrent sessions overlap again.** In 3.1.0 the provider plugin
  sent one request per model at a time even without a configured rate, so two
  sessions on the same model could not run side by side. Models without a
  configured rate now send concurrently as before; an upstream 429 still holds
  the model and re-sends, and models with a configured rate still queue and
  are spaced by their interval. `maxQueue` (default 64) bounds the requests
  waiting or in flight. The proxy is unchanged: it still sends one request per
  model at a time until the previous response starts.
- **Proxy `/stats`:** a request counts as held only from 1 ms of queue time.
- **Docker Compose** passes `MVIBE_RATE_LIMIT_RPS` and
  `MVIBE_RATE_LIMIT_MODEL_RPS` through from an untracked `.env`.

## New in 3.1.0

- **No more rate-limit wall.** The proxy and the OpenCode provider plugin now
  detect an upstream 429 by status, hold the request and re-send it before any
  response byte reached the consumer, instead of failing. Each model has its
  own FIFO queue. A hold lasts for `Retry-After` when present, otherwise an
  exponential backoff with jitter (capped at 30 s); a request is given up after
  120 s of waiting or when 64 requests are already queued, and only then is
  the 429 passed on with `retry-after` and the `x-ratelimit-*` headers. This
  is on by default and needs no configuration.
- **Optional pacing per model.** To stay under a known limit ahead of time:
  - Proxy: `MVIBE_RATE_LIMIT_MODEL_RPS=model=rate[,model=rate]` (for example a
    large model at `0.5`) and `MVIBE_RATE_LIMIT_RPS` as the default for every
    other model; also `MVIBE_RATE_LIMIT_MAX_WAIT_S`, `MVIBE_RATE_LIMIT_MAX_QUEUE`
    and `MVIBE_RATE_LIMIT_BACKOFF_CAP_S`. Invalid values refuse to start. With
    Docker Compose put the two rate keys in an untracked `.env` next to
    `docker-compose.yml`; rates are account-specific and never hardcoded.
  - Plugin: `options.rateLimit` with `requestsPerSecond`,
    `modelRequestsPerSecond` (an object of model names to rates), `maxQueue`
    and `backoffCapMs`. The plugin talks to Mistral directly, so its rates are
    configured separately from the proxy's and the two do not share a budget.
  - Queues are keyed by the model name the client sends; an alias and its
    dated id are two queues. The proxy sends requests per model one at a time
    until the previous response starts, also without a configured rate; the
    plugin does so only for models with a configured rate (since 3.1.1).
- **Diagnostics.** Proxy and plugin events carry `queued_ms`,
  `rate_limit_rejections` and the outcome `rate_limited`; the proxy logs one
  warning per hold.
- **Proxy stats.** `GET /stats` returns the proxy's counters since start
  (requests by outcome and status, token usage overall and per model as
  reported by the upstream, economics and queueing aggregates) next to
  `mmhmem stats --json`. Startup prints the stats URL. Request diagnostics
  carry `usage_prompt_tokens` and `usage_completion_tokens`.
- **Persistent mmhmem state with Docker Compose.** `./mmhmem-data` next to the
  compose file is bind-mounted as the child's data directory, so summaries,
  caches and statistics survive container recreation.
- **Known limitations.**
  - The proxy's own token spend is not yet shown by `mmhmem stats`: mmhmem
    0.5.2 stores client-reported usage only together with a checkpoint save,
    which the proxy does not send. `GET /stats` shows the spend; savings and
    worker usage appear in `mmhmem stats` as before. `MVIBE_STATS_USAGE=0`
    turns the reports off.
  - `mistral-large-4` cannot serve as the mmhmem worker model (it rejects the
    worker's temperature 0 without `top_p` 1); use e.g. `mistral-small-latest`.
    `WORKER_API_URL` must be the full chat-completions URL.
  - Token-per-minute limits are not paced ahead of time; they are handled when
    a 429 arrives.
- Version metadata moved 3.0.2 → 3.1.0 in `pyproject.toml`,
  `packages/opencode-vibe-oidc/package.json` and `uv.lock`. The helper API
  major is unchanged (3); no protocol or installation-layout changes.

## New in 3.0.2

- **Provider registers immediately.** Plugin setup no longer waits on the auth
  helper, keyring or the upstream model endpoint: the Mistral-Vibe provider
  appears within moments of OpenCode startup with an empty model list plus a
  one-time "Model discovery pending" notice, and models publish as soon as the
  first authenticated discovery completes (typically seconds later). A slow,
  hung or failing upstream can no longer delay or prevent registration.
- **Bounded model discovery.** Every discovery attempt now has a 30-second
  wall-clock deadline. On timeout the attempt is abandoned, the last known
  model list is kept, and a timeout diagnostic names the deadline — without
  aborting the shared helper session, so in-flight chats are unaffected.
  Failed attempts retry on the next credential/connection event or reload.
- **Visible failure causes.** Discovery failures are classified
  (`unreachable`, `unauthorized`, `invalid`) with the underlying error text,
  and setup failures name the failing phase (`request-context`,
  `provider-transform`, `integration-transform`, `aisdk-hook`) with the
  original error attached as `cause`, replacing the previous generic-only
  message. Diagnostics stay single-line and secret-free; see them with
  `--print-logs` (CLI) or `options.diagnostics: true` (per-request JSONL).
- Version metadata moved 3.0.1 → 3.0.2 in `pyproject.toml`,
  `packages/opencode-vibe-oidc/package.json` and `uv.lock`. The helper API
  major is unchanged (3); v3.0.2 installs the v3 helper and provider together
  with no protocol or installation-layout changes.

## New in 3.0.1

- **Pre-built, checksum-verified `mmhmem` `0.5.2` release binary in Docker**:
  `Dockerfile` and `docker-compose.yml` now embed the published
  `mmhmem-v0.5.2-x86_64-unknown-linux-musl.tar.gz` binary verified against
  `SHA256SUMS` at image build time (`ARG MMHMEM_VERSION=0.5.2`), replacing the
  multi-stage `rust:1-slim` git-clone and source compile from `3.0.0`.
- **Single-pipeline Dagger container verification**: `.dagger/ci.py check` now
  builds the `Dockerfile` image, verifies `/usr/local/bin/mmhmem --version`
  matches the pinned `MMHMEM_VERSION`, and smoke-tests `.venv/bin/python
  proxy.py --help` in both local WSL and GitHub Actions, backed by an offline
  version/pipeline coupling test (`tests/test_version_coupling.py`).

## New in 3.0.0

- Optional **token economics** for the proxy: when `MVIBE_ECON_MMHMEM_BIN`
  selects the mmhmem binary embedded in the Docker image (built from the pinned
  `MMHMEM_REF`), the proxy replaces *historical* file-read tool results with
  deterministic, clearly marked summaries from a supervised `mmhmem stdio` child
  in the same container, cutting the prompt cost of resent history. Off by
  default and fail-open on every error; `--econ` makes incomplete configuration
  a startup error. Tool-result contents never traverse a network on this path,
  and the new allowlisted `econ`/`econ_warm` diagnostics report counts, bytes
  and a labelled token estimate — never content. See
  [token economics](token-economics.md).
- **Docker deployment** (user-directed amendment): the proxy image embeds the
  mmhmem child, speaks newline-delimited JSON-RPC over pipes to it, binds
  loopback-only ports and mounts credentials read-only. `docker compose config`
  validates and `mvibe-proxy:local` builds and runs `mmhmem 0.5.1` on the
  pinned `MMHMEM_REF` (see the spec's validation record).
- The offline acceptance gate passed end to end for the first time
  (`reports/verification/dfe9d546a3c44182bc7788d8ba5dbf05/`): 1,395 Python
  tests and 1,061 provider tests green with the full eleven-step suite. Two
  gate defects were fixed along the way (a stale document anchor and
  skip-treated-as-failure), and the bridge cleanup bounds now tolerate busy
  hosts while staying bounded.
- Version metadata moved 2.0.0 → 3.0.0 in `pyproject.toml`,
  `packages/opencode-vibe-oidc/package.json` and `uv.lock`. Because the
  helper reports its distribution version as the API version, the provider's
  expected helper API major moved 2 → 3 with the release: v3.0.0 installs
  the v3 helper and provider together from the same archive, and v2.x
  helpers are rejected as incompatible. The repository test keeps release
  major and helper API major in lockstep.

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

The v3.0.0 offline acceptance run is green end to end: 1,395 Python tests /
5 documented conditional skips, 1,061 provider tests, typecheck and both
builds, both bridge process suites, synthetic and real OpenCode 2.2.5 host
integration, and the four-probe tool baseline
(`reports/verification/dfe9d546a3c44182bc7788d8ba5dbf05/acceptance.json`).
Token-economics latency stayed under the 5 ms budget in three-run measurement
(0.62 ms micro apply, +0.7 ms full-app, +1.5–2.3 ms under four-client
concurrency; MVIBE_ECON_PERF gates the benchmarks). The Docker image
`mvibe-proxy:local` builds from the pinned `MMHMEM_REF` and its embedded
mmhmem runs on the runtime stage.

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
embeddings, live proxy quota attribution, and live token-economics savings.
The checks below establish a limited baseline, not general reliability or
production readiness.
No signing/notarization or PyPI/npm publication is included.

See [installation](https://github.com/norandom/mvibe/blob/main/docs/release.md)
and [compatibility checks](https://github.com/norandom/mvibe/blob/main/docs/tool-compatibility.md).
