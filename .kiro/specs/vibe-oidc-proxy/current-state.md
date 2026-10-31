# Current Implementation: Vibe OIDC Proxy

## Evidence and Limits
Static inspection on 2026-10-31; no imports, test execution, login, keyring access or upstream requests. README's Vibe 3.0.4 provenance is a repository claim, not an independently verified upstream diff. `LICENSE.vibe` contains Apache License 2.0.

`proxy.py` SHA256: `8D97854E93E4DB1A422FF65CCAC58FBFDBA663EDC5126B8BEB35A05B073E0F54`.

This captures observed code, including gaps. It does not automatically endorse every behavior as a future requirement.

## Authentication Contract
The intended identity is Vibe's separate Mistral browser OIDC/subscription flow, not API-key authentication. The current proxy derives upstream credentials from that flow. A placeholder `apiKey` in an agent's OpenAI-compatible provider configuration is not forwarded as its upstream credential.

## Components
| Source | Responsibility |
| --- | --- |
| `proxy.py` | CLI, browser login integration, FastAPI lifecycle, allowlisted forwarding, streaming |
| `mvibe_auth/_oidc_config.py` | Issuer/client/audience/redirect/scope and URL validation |
| `mvibe_auth/_oidc_service.py` | Login attempt state, cancellation, refresh and persistence |
| `mvibe_auth/_oidc_client.py` | Discovery, PKCE exchange, JWT/JWKS verification and refresh |
| `mvibe_auth/_oidc_callback.py` | Bounded loopback HTTP callback handling |
| `mvibe_auth/_oidc_credentials.py` | Strict token bundle, keyring storage, cache and conditional save |
| `mvibe_auth/_oidc_runtime.py` | Destination policy, ID-token headers, refresh locks and tenant lookup |
| `mvibe_auth/http.py` | HTTPX2 transport, proxy routing, TLS and OIDC-marker hook |
| `mvibe_auth/concurrency.py` | Synchronous bridge to asynchronous operations |
| `mvibe_auth/_credentials_port.py`, `_http.py`, `_oidc_platform_credentials.py` | Typed credential interfaces and separate platform HTTP/UserInfo helpers |
| `mvibe_auth/oidc_marker.py`, `__init__.py` | Marker/feature flag and package exports |

## Runtime and Agent Interface
Python >=3.12 with uv inline dependencies. FastAPI/Uvicorn; Authlib 1.9.1, HTTPX2 2.16.0, joserfc 1.7.7, Pydantic 2.13.4 and keyring 25.7.0 are pinned; several other dependencies are unpinned. Both `httpx` and `httpx2` are declared.

CLI binds `127.0.0.1:8765`; `--port` overrides it. `--login` performs browser login before serving. Import defaults `VIBE_CLI_OIDC_ENABLED` to `1` only when absent. Startup checks stored credentials and logs in if absent or mismatched; credential presence is not an expiry check. One shared HTTP client uses timeout 600 and disables redirects.

| Operation | Observed behavior |
| --- | --- |
| `GET /health` | Returns `{"status":"ok"}`; does not probe upstream readiness |
| `GET /v1/models` | Forwards to configured audience + `/v1/models` |
| `POST /v1/chat/completions` | Forwards raw body; no local chat/tool-call schema translation |
| `POST /v1/embeddings` | Forwards raw body |
| Other GET/POST paths under `/v1/` | Custom 404 unsupported-endpoint response |

Other methods are handled by framework routing. Responses API is not implemented. The local proxy has no caller authentication. Query parameters and buffered raw body are forwarded; incoming Authorization and other caller headers are not forwarded. OIDC/tenant headers and JSON Content-Type are generated locally.

Every upstream response uses StreamingResponse with upstream status, upstream Content-Type (default application/json), and Cache-Control no-cache. Other upstream headers are dropped. Bytes are yielded using `aiter_bytes()` and the upstream response is closed in the generator's finally block. SSE/tool-call compatibility depends on upstream content and actual client handling; it is not proven by the passthrough alone.

Request/auth setup OidcError becomes generic 401 with re-login advice; other exceptions in that block become generic 502. Configuration construction is outside the block. Stream-iteration failures occur after setup and are not covered by that error mapping. No upstream-401 refresh/retry exists.

## Browser OIDC Flow
Default issuer is `https://auth.mistral.ai`, client ID `829da7e7-628f-4795-829a-8cca609c76d4`, audience `https://api.mistral.ai`, redirect `http://127.0.0.1:8766/callback`, and scope `openid offline_access email profile`. The API audience URL does not imply API-key authentication.

`VIBE_CLI_OIDC_ISSUER`, `_CLIENT_ID`, `_AUDIENCE`, `_REDIRECT_URI` and `_SCOPE` override configuration. Custom issuers require a client ID; code can accept an empty custom audience. A built-in alternate issuer registration also exists. URL validation generally requires HTTPS, permits numeric-loopback HTTP, and restricts callback redirects to HTTP loopback with a fixed nonzero port. Scope must include openid.

Login sequence:
1. Fetch issuer discovery and check exact issuer and endpoint policy.
2. Generate state, nonce and PKCE S256 verifier/challenge; start callback listener before exposing the authorization URL.
3. Open browser and await callback within the overall 300-second login timeout.
4. Validate callback method/path/state/code or OAuth error; exchange code as a public client with no client secret.
5. Validate token response and signed ID token using JWKS, issuer, client audience, nonce and access-token context; require subject.
6. Persist the credential bundle and clean up the attempt/listener.

Callback limits include an 8192-byte reader limit, eight concurrent handlers and five-second read/drain timeouts. Token validation accepts selected RS/ES algorithms intersected with issuer metadata, uses 30-second claim leeway, and requires positive finite access-token lifetime. Refresh cannot change subject.

## Credential and Request Flow
One shared keyring service `ai.mistral.vibe.oidc`, entry `cli-oidc:v1:tokens`, stores a versioned token bundle. It includes access/refresh/ID tokens, configuration identity, subject, expiry, original nonce and optional tenant. Secret representations are masked, but storage serialization necessarily contains token material.

Runtime header resolution restricts destination origin, then resolves credentials in a thread under a process-wide refresh lock. Service refresh starts when access expiry is within 30 seconds. Runtime additionally forces refresh for missing/stale ID-token expiry, then rejects an ID token still stale. Refresh preserves the prior refresh token and ID token when omitted by the response. Thus refresh success alone may not make an expired ID token usable.

**Inference requests use bearer ID tokens.** The separate UserInfo probe uses bearer access tokens. Do not replace this distinction with a generic API-key model.

Tenant lookup uses `VIBE_CLI_OIDC_ORGANIZATION_ID` and optional `_WORKSPACE_ID` overrides, or audience `/v1/users/me/workspaces` with bearer ID token, page size 100 and offset pagination. It chooses the first available workspace by default or an exact selected ID, then caches/persists the tenant. Tenant persistence failure does not prevent the request.

Storage has an in-process cache/RLock and conditional token save to avoid overwriting changed credentials; no OS-wide refresh lock exists. Installed Vibe and this proxy can still contend for the shared slot. `VIBE_TEST_DISABLE_KEYRING=1` disables loading and makes save/clear fail; it is not a substitute for a successful fake store in tests.

## HTTP Internals
HTTPX2 helpers reconstruct environment/system proxy routes with NO_PROXY/CIDR handling unless transport/proxy/mounts are explicitly injected. TLS uses certifi and optional additional/system trust configuration. The OIDC client uses no redirects, trust_env false and a 20-second timeout. An exact bearer marker can be replaced by runtime OIDC headers in the shared HTTP helper; the proxy normally already supplies resolved ID-token headers.

## Unverified Questions
- Can the declared dependencies install/import and interoperate on the target Python/Windows environment?
- Does the live Vibe registration accept this redirect, scope and audience, and does refresh issue usable ID tokens?
- Are tenant response schema, workspace selection and account subscription/model access as assumed?
- Do streaming, tool calls, errors and cancellation work with the selected OpenCode provider and version?
- What should the desired contract be for configuration errors, stream failures, empty custom audience and loopback HTTP audience?
- Is loopback-only access without local caller authentication sufficient for the intended operator environment?

No live compatibility or test-pass claim is made.
