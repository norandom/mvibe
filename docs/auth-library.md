# Shared authentication library

`mvibe_auth` exposes `AuthConfig`, `AuthSession`, immutable `AuthSnapshot` and
typed login/error results. Both Python consumers and the provider's Python helper
use this implementation for login, validation, refresh, storage and tenant
selection. The library has no dependency on the proxy application or OpenCode.
The [provider guide](opencode-provider.md) covers the adapter and both consumer
routes; these examples exercise the Python API. Prepare the independent library
with `uv sync --locked --python 3.12 --no-dev --no-editable`. FastAPI/Uvicorn are
optional proxy dependencies. Distribution/API 0.1.0 and bridge protocol 1 are
independent of Vibe 3.0.4 source provenance; the adapter accepts API major 0.
Use a uv-managed environment and interpreter, never a bare Python/WindowsApps
alias. The [README](../README.md) separates online preparation from offline checks.

## Explicit sign-in

Pass an enabled `AuthConfig`, or use `AuthConfig.from_env()` after setting
`VIBE_CLI_OIDC_ENABLED=true` and the issuer/client/audience settings appropriate
to your registration. Configuration is immutable and captured at construction;
create a new session to change it. Python 3.12 or newer is required.

This function asks the caller to present a browser URL. `start_login` returns
only after the callback listener is ready. A matching stored identity returns
an empty URL and `reused_current`; `force=True` requests browser authorization.

```python
from mvibe_auth import AuthSession, AuthFailure, AuthError

async def sign_in(config, present_url, *, force=False):
    session = AuthSession(config)
    try:
        handle = await session.start_login(force=force)
        if handle.authorization_url:
            present_url(handle.authorization_url)
        outcome = await session.wait_login(handle.attempt_id)
        if isinstance(outcome, AuthFailure):
            raise AuthError(outcome)
        return outcome
    finally:
        await session.close()
```

`completed` means validated credentials were committed. `reused_current` means
the current stored identity was retained; it does not claim that a newly
browser-selected account was saved. A matching stored identity can still require
refresh or re-login when resolving a request. The library never opens a browser
from `status()` or `resolve()`. `status()` reports only `signed_in` and
`config_match` and does not validate freshness or entitlement.

## Resolve for every request

Keep one session for a consumer's lifetime and close it in the consumer's
`finally`/shutdown path. Call this function immediately before each approved
inference request. The caller owns its HTTP transport, disables redirects and
must avoid logging or persisting the returned headers.

```python
async def prepare_request(session, target_url):
    snapshot = await session.resolve(target_url)
    return snapshot.headers
```

The immutable snapshot contains inference ID-token and tenant headers,
`expires_at`, an opaque `revision` and the validated `target`. It contains no
refresh token. Use the headers only for that target; do not forward them after
a redirect. Snapshot representations hide headers, but explicit access to
`snapshot.headers` yields secrets. Authorization URLs and callback values must
also be kept out of diagnostics. `AuthError.failure` has a safe code/message
and `retryable` flag; it does not expose raw issuer or keyring errors.

Both access and inference ID tokens need more than 30 seconds of remaining
lifetime. A resolution reloads authoritative storage, refreshes when necessary
and binds tenant selection to the current identity/revision. An explicit
organization bypasses discovery; otherwise selection uses the exact configured
workspace or the first accessible workspace. Cached tenant information is used
only for the corresponding identity and selection. No API key fallback exists.

## Cancellation and competing writers

`cancel_login(attempt_id)` cancels a login; `wait_login` returns its stable typed
terminal result while the session remains open. Cancelling only a wait does not
cancel the underlying login. `close()` stops admission, cancels owned activity,
closes callbacks and drains blocking storage workers. It is idempotent and can
be called concurrently. Repeated cancellation of the caller still drains work
before cancellation propagates; a later `close()` can observe completed cleanup.
After closure, other public operations fail with a `cancelled` AuthError.

Persistence can finish after cancellation was requested. A login commit that
finishes successfully is reported internally as completed; cancellation does
not undo a committed write. Do not assume that killing a process rolls back
its final write: a new session must reload the authoritative store.

Participating library sessions/processes use the same per-user native lock for
the encrypted credential store. They serialize reload/refresh/conditional writes;
the coordinator is acquired before the in-process slot lock and is not released
until an owned storage worker finishes. Native acquisition has a 90-second
deadline and returns a retryable transient failure if unavailable. Browser waits
hold no coordinator lock.

The portable store encrypts token JSON with Fernet and keeps only a short
encryption key in the OS keyring. An absent store can read the legacy Vibe slot;
the first mutation writes the new store without changing Vibe's slot. Subsequent
Vibe logins and refreshes are separate. A signed-out tombstone prevents old
credentials from being imported again after clear. Missing keys or corrupt
encrypted data fail closed, with no plaintext fallback. This is cross-platform
storage, not a portable backup: copying the encrypted file alone to another
machine does not transfer its OS-keyring key.

The encrypted file is `mvibe-auth/credentials.v1.enc` under `%LOCALAPPDATA%`
on Windows, `~/Library/Application Support` on macOS, or `$XDG_DATA_HOME`
(default `~/.local/share`) on Linux. The keyring service remains
`ai.mistral.vibe.oidc`; the short key uses entry `mvibe-auth:v1:fernet-key`.
Use the public library to sign out; deleting the file manually can re-enable
legacy import. Loss of the OS-keyring key makes an existing encrypted file
unreadable, so moving the file alone is not a recovery procedure.

Conditional comparison detects changes observed before a write, discards stale
candidates and allows one conflict retry shared across token and tenant
resolution. Repeated changes produce a safe retryable `conflict` failure.
All consumers must use the library's native coordinator for mutation.

## Offline evidence

The installed private helper runs as `uv run --offline --no-sync --project
<prepared-checkout> -m mvibe_auth bridge`, or with the absolute interpreter from
that uv-created environment and arguments `-m mvibe_auth bridge`. Its private
stdin/stdout are protocol-only NDJSON, with hello/status/login/resolve/shutdown
methods and a 1 MiB frame cap. Refresh tokens never cross the bridge. Missing
runtime/library or protocol/API/audience mismatch requires matching installation
and configuration; it never falls back to API-key authentication. Consumer code
should use the public API above rather than depending on this private protocol.

Run `uv run --locked --offline pytest tests/test_auth_api.py tests/test_auth_lifecycle.py -q`
from a prepared locked environment. The lifecycle suite executes both Python
blocks above through the public API with synthetic issuer, keyring, callback
and HTTP boundaries. It checks concurrent consumers, refresh/tenant races,
callback and refresh cancellation, repeated cancellation while persistence is
blocked, native lock retention and diagnostic secrecy. Default-deny fixtures
prevent real keyring, browser and remote-network use and reset caches between
cases. Offline results do not verify account login, subscription entitlement,
upstream compatibility or live inference.
