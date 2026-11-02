# Way forward (2026-11-02)

Status after the [host-docker amendment](host-docker-amendment.md): the proxy
spec is implementation-complete (31/31 tasks, GO for offline + proxy/direct
smoke), and the 2026-11-02 amendment added `--host`, `start_proxy.sh`/`.ps1`
and Docker/compose with read-only credential mounts. The proxy was re-tested
live the same day (health, swagger/openapi, models, one chat completion) using
the default `existing-vibe` flow — the same auth flow as the tested OpenCode
provider (2.2.5).

## Open items

1. **proxy-observability** (spec initialized, requirements stub only):
   structlog `--log` baseline exists; OTel traces/metrics/export are not
   implemented. Next phase: requirements for that spec.
2. **Live unverified** (from spec.json): native/MCP tool execution and token
   refresh through the proxy; user-reported OpenCode 2.2.6 is unverified
   (tested: 2.2.5). **Embeddings through the proxy are now verified**
   (2026-11-02, `mistral-embed` 1024-dim via `POST /v1/embeddings`; see
   `docs/live-results.md` and `smoketests/`).
3. **proxy-endpoint-compatibility** (new brief, initialized 2026-11-02):
   Ollama-compatible (`/api/*`) and Anthropic-compatible (`/v1/messages`)
   adapters do not exist. OpenAI-compatible `/v1/*`, OpenCode-as-consumer
   (`providers.mistral` baseURL) and Swagger (`/docs`, `/openapi.json`) are
   already supported and now regression-tested.
4. **Docker hardening**: Windows Credential Manager is not a file — export the
   two keyring entries once into the mounted `keyring_pass.cfg`, or sign in
   inside the container; read-only mounts mean in-container OIDC refresh
   cannot persist; keep the compose default loopback-published.

## Proposed order

1. ~~Re-run the full offline gate~~ **Done 2026-11-02**: `scripts/verify.ps1`
   — all 11 mandatory steps passed, 1179 Python + 1049 Bun tests; only the
   pre-existing unverified live/documentary criteria 9.4–9.6 remain (same as
   the pre-amendment baseline runs). Docker image built and container smoke
   passed in WSL Debian (see host-docker-amendment.md).
2. **proxy-observability**: DONE 2026-11-02 — requirements approved, design
   approved, tasks implemented (OpenObserve-style export: native `_json` logs
   to stream `mvibe` + optional OTel extra for spans/metrics; plugin stderr
   parity; operator guide `docs/observability.md`). See that spec's
   implementation-log.md; full gate re-run in progress.
3. **proxy-endpoint-compatibility**: DONE 2026-11-02 — implementation-complete;
   Ollama (`/api/version|tags|chat|generate|show|embed`) and Anthropic
   (`/v1/messages` + dialect-switched `/v1/models`) adapters shipped in
   `mvibe_proxy/compat.py`, live-smoked against the real proxy (both dialects,
   streaming included); gate run `d290287a…` all steps passed. See that spec's
   implementation-log.md.
4. Schedule the remaining live checks (tools, refresh, OpenCode 2.2.6) as one
   coordinated live session; keep them distinct from offline evidence.
   ~~Embeddings~~ **Done 2026-11-02** via the proxy (`smoketests/`,
   `docs/live-results.md`); direct-provider embeddings remain UNVERIFIED.
5. Docker follow-ups (optional rw mount for in-container OIDC refresh,
   in-container OIDC login via published callback port) can ride along with
   2–3.
