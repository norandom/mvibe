# Post-completion amendment: bind host, start scripts, Docker (2026-11-02)

User request after the 31/31-task implementation was validated: make the proxy
easy to start on either OS, allow binding beyond loopback, and run it in a
container with the Vibe credentials mounted. Implemented directly (not through
the task pipeline) with offline regression tests and a live smoke run.

## What was added

- `proxy.py --host HOST` — bind address, default `127.0.0.1` (unchanged
  behavior). Any IP or hostname is accepted; `0.0.0.0` works and prints a
  stderr warning because the proxy has no caller authentication. Invalid
  values are rejected by argparse.
- `start_proxy.sh` / `start_proxy.ps1` (repo root) — thin launchers that run
  the locked project environment (`uv run --locked --extra proxy -- python
  proxy.py`) and forward arguments. Both take `--host`/`-Host` defaulting to
  `127.0.0.1` and pass `--port`, `--auth-source`, `--login`, `--log` through.
- `Dockerfile` + `.dockerignore` + `docker-compose.yml` — container focused on
  the proxy endpoint only. The image installs the locked environment with the
  `proxy` and `docker` extras and runs `proxy.py --host 0.0.0.0 --port 8765`
  as a non-root user; compose publishes `127.0.0.1:${MVIBE_PROXY_PORT:-8765}:8765`
  (host-side loopback only) and mounts credentials read-only:
  - `MVIBE_AUTH_DATA` (default `~/.local/share/mvibe-auth`, Windows
    `%LOCALAPPDATA%\mvibe-auth`) → `/data/mvibe-auth:ro`, with
    `XDG_DATA_HOME=/data` so the encrypted OIDC bundle resolves to
    `/data/mvibe-auth/credentials.v1.enc`.
  - `MVIBE_KEYRING_DATA` (default `~/.local/share/python_keyring`) →
    `/data/python_keyring:ro`, read through
    `PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring` (new optional
    `docker` extra, `keyrings.alt>=5.0`; keyring core has no file backend, and
    a container has no SecretService/D-Bus). This serves both the native Vibe
    entry (`ai.mistral.vibe` / `MISTRAL_API_KEY`) and the OIDC wrapping key
    (`ai.mistral.vibe.oidc` / `mvibe-auth:v1:fernet-key`).
- Auth flow is unchanged and identical to the tested OpenCode provider: the
  container default is `--auth-source existing-vibe`, rereading the native
  credential per request; `--auth-source oidc` remains explicit opt-in.

## Endpoint compatibility (answers the 2026-11-02 question)

| Surface | Status |
| --- | --- |
| OpenAI-compatible `/v1/models`, `/v1/chat/completions`, `/v1/embeddings` | Yes — existing proxy behavior, raw passthrough |
| OpenCode as a consumer | Yes — `providers.mistral` with `baseURL http://127.0.0.1:8765/v1` and placeholder `apiKey: local` (docs/proxy-guide.md, live-tested on OpenCode 2.2.5); the direct `vibe-oidc` plugin bypasses the proxy |
| Swagger UI / OpenAPI | Yes, already built in — `/docs` and `/openapi.json` (FastAPI); now covered by a regression test |
| Ollama-compatible (`/api/chat`, `/api/generate`, `/api/tags`, ...) | No — not implemented; see `../proxy-endpoint-compatibility/brief.md` |
| Anthropic-compatible (`/v1/messages`, Anthropic SSE events) | No — not implemented; same follow-up spec |

## Evidence (2026-11-02)

- `ruff check proxy.py tests/test_proxy.py` — clean.
- `pytest tests/test_proxy.py tests/test_proxy_diagnostics.py` — 49 passed,
  including new tests: `--host` default/`0.0.0.0`/`::1`/`localhost` binding,
  off-loopback warning, invalid host rejection, and `/docs` + `/openapi.json`
  exposing `/health` and the three `/v1` operations.
- `start_proxy.sh --help`, `start_proxy.ps1 -Help` / `-Host 0.0.0.0 -Help`,
  `proxy.py --help` — all render; bash syntax checked.
- Live smoke on port 8799 via `./start_proxy.sh --port 8799`: startup with the
  default existing-vibe flow, `GET /health`, `GET /docs`, `GET /openapi.json`
  and `GET /v1/models` against the real native credential. Results recorded
  in the session log; see docs/proxy-guide.md for the operator commands.

## Known limits

- Windows Credential Manager is not a file, so on Windows hosts the keyring
  entries must be exported once into the mounted `keyring_pass.cfg`, or the
  OIDC login must run inside the container (`docker compose run --rm
  --service-ports proxy --auth-source oidc --login` publishes the loopback
  callback port too).
- On this workstation the Docker daemon runs in WSL Debian (recorded in
  steering/tech.md, 2026-11-02): run compose via
  `wsl -d Debian bash -c 'cd /mnt/d/Source/mvibe && docker compose up --build'`
  and set `MVIBE_AUTH_DATA=/mnt/c/Users/mariu/AppData/Local/mvibe-auth` plus
  `MVIBE_KEYRING_DATA=/mnt/c/Users/mariu/AppData/Local/Python Keyring`, because
  compose mount paths resolve in the WSL filesystem, not on Windows.
- Verified 2026-11-02 in WSL Debian (Docker 29.9.2): `docker compose config`
  passes; `docker build -t mvibe-proxy:local .` succeeds (the first build
  failed because hatchling needs `LICENSE`/`README.md`/`THIRD_PARTY_NOTICES`,
  now COPYed); a container run with a generated `keyring_pass.cfg` mounted
  read-only started cleanly (`Application startup complete`, existing-vibe
  status signed in) and served `/health`, `/docs` (Swagger UI) and
  `/openapi.json` (`/health`, `/v1/models`, `/v1/chat/completions`,
  `/v1/embeddings`) on the published `127.0.0.1` port. The keyring file must
  be generated via `keyring.set_password` (escaped + base64 entries); a
  hand-written plain INI file is silently not found. ShellCheck (via
  `koalaman/shellcheck-alpine`) passes on `start_proxy.sh` and `install.sh`.
- The credential mounts are read-only; in-container OIDC refresh cannot
  persist a renewed token (native Vibe / host-side renewal remains the owner
  for the default existing-vibe flow). Mount read-write only if in-container
  renewal is desired.
- `--host 0.0.0.0` on the host (or publishing `0.0.0.0:8765` in compose) exposes
  an unauthenticated endpoint that spends the Vibe credential — the warning is
  printed, and the compose default stays loopback-only.
