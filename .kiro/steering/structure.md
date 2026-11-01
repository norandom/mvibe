# Project structure

Updated: 2026-11-01. Bootstrapped around dependency and responsibility boundaries.

- `mvibe_auth/` owns shared authentication, credential coordination, HTTP policy
  and the stdio bridge. Its public sessions are reusable across consumers.
- `proxy.py` composes the FastAPI application and CLI. `mvibe_proxy/` owns
  proxy-specific streaming and structured diagnostics.
- `packages/opencode-vibe-oidc/` owns the TypeScript OpenCode adapter, installer,
  tests and build. Its historical package/provider ID does not select auth mode.
- Dependencies flow from consumers toward shared authentication. The shared
  library remains independent of FastAPI, OpenCode and the proxy application.
- `tests/` holds Python tests and explicit synthetic or pinned native fixtures;
  the package holds provider tests. Ordinary tests never import a sibling checkout
  or depend on a developer's installed Vibe.
- `docs/` holds technical and operator guides. Steering holds durable decisions;
  feature specs hold requirements, implementation tasks and dated evidence.
- Generated captures, reports and temporary artifacts belong outside tracked
  source. Keep real credentials and conversation payloads out of project memory.
