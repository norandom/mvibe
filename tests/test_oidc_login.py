"""Offline callback and signed-token contracts using synthetic I/O and clocks."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from mvibe_auth._oidc_callback import OidcCallback
from mvibe_auth._oidc_config import OidcError


URI = "http://127.0.0.1:8766/callback"
STATE = "synthetic-state"


class Listener:
    def __init__(self):
        self.closed = False
        self.joined = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        assert self.closed
        self.joined = True


@pytest.fixture
def listener(monkeypatch):
    server = Listener()

    async def start(handler, host, port, *, limit):
        assert (host, port, limit) == ("127.0.0.1", 8766, 8192)
        server.handler = handler
        return server

    monkeypatch.setattr(asyncio, "start_server", start)
    return server


def request(query, *, method="GET", path="/callback", version="HTTP/1.1"):
    return f"{method} {path}?{query} {version}\r\nHost: 127.0.0.1\r\n\r\n".encode()


@pytest.mark.anyio
@pytest.mark.parametrize("query", [
    "state=synthetic-state&state=&code=one",
    "state=&state=synthetic-state&code=one",
    "state=synthetic-state&state=other&code=one",
    "state=synthetic-state&code=one&code=",
    "state=synthetic-state&code=&code=one",
    "state=synthetic-state&code=one&code=two",
    "state=&code=one", "code=one", "state=wrong&code=one",
    "state=synthetic-state&code=", "state=synthetic-state",
    "state=synthetic-state&error=", "state=synthetic-state&error=denied&error=",
    "state=synthetic-state&error=denied&error=other",
    "state=synthetic-state&error=denied&code=one",
    "state=synthetic-state&state=&error=access_denied",
])
async def test_invalid_callback_cannot_resolve(query, listener, controlled_callback):
    callback = OidcCallback(URI, STATE)
    async with callback:
        reader, writer = controlled_callback(request(query))
        await listener.handler(reader, writer)
        # Malformed/unrelated requests do not terminate the legitimate browser wait.
        assert not callback.result.done()
        assert writer.closed
        assert writer.data.startswith(b"HTTP/1.1 400 Bad Request")
        assert not callback._connections
    assert listener.closed and listener.joined
    assert callback.result.cancelled()


@pytest.mark.anyio
@pytest.mark.parametrize("kwargs", [
    {"method": "POST"}, {"path": "/wrong"},
    {"path": "http://127.0.0.1:8766/callback"}, {"path": "//127.0.0.1/callback"},
    {"version": "garbage"}, {"path": "/callback#fragment"},
])
async def test_request_target_and_method_rejected(kwargs, listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        reader, writer = controlled_callback(request("state=synthetic-state&code=one", **kwargs))
        await listener.handler(reader, writer)
        assert not callback.result.done()
        assert writer.closed
    assert listener.closed and listener.joined


@pytest.mark.anyio
async def test_valid_callback_resolves_once(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        for code in ("first", "second"):
            reader, writer = controlled_callback(request(f"state={STATE}&code={code}"))
            await listener.handler(reader, writer)
            assert writer.closed
            assert b"Cache-Control: no-store" in writer.data
            assert code.encode() not in writer.data
        assert await callback.result == f"{URI}?state={STATE}&code=first"
        assert writer.data.startswith(b"HTTP/1.1 400 Bad Request")
    assert listener.closed and listener.joined
    assert not callback._connections


@pytest.mark.anyio
async def test_concurrent_callbacks_preserve_first_result(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        pairs = [controlled_callback(request(f"state={STATE}&code={code}")) for code in ("one", "two")]
        await asyncio.gather(*(listener.handler(reader, writer) for reader, writer in pairs))
        assert await callback.result in (f"{URI}?state={STATE}&code=one", f"{URI}?state={STATE}&code=two")
        assert sum(writer.data.startswith(b"HTTP/1.1 200 OK") for _, writer in pairs) == 1
        assert all(writer.closed for _, writer in pairs)
        assert not callback._connections


@pytest.mark.anyio
async def test_rejected_request_does_not_prevent_valid_callback(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        for query in ("state=wrong&code=one", f"state={STATE}&code=valid"):
            reader, writer = controlled_callback(request(query))
            await listener.handler(reader, writer)
            assert writer.closed
        assert await callback.result == f"{URI}?state={STATE}&code=valid"


@pytest.mark.anyio
async def test_matching_denial_is_safe_failure(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        reader, writer = controlled_callback(request(f"state={STATE}&error=access_denied&error_description=private"))
        await listener.handler(reader, writer)
        with pytest.raises(OidcError, match="^OIDC authorization was declined$"):
            await callback.result
        assert b"200 OK" in writer.data and b"private" not in writer.data
        assert writer.closed
    assert listener.closed and listener.joined


class BlockedReader:
    def __init__(self):
        self.started = asyncio.Event()
        self.stopped = False

    async def readuntil(self, separator):
        assert separator == b"\r\n\r\n"
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.stopped = True


@pytest.mark.anyio
async def test_connection_limit_and_shutdown_drains_tasks(listener, controlled_callback):
    callback = OidcCallback(URI, STATE)
    await callback.__aenter__()
    active = []
    for _ in range(8):
        reader = BlockedReader()
        _, writer = controlled_callback(b"")
        task = asyncio.create_task(listener.handler(reader, writer))
        await reader.started.wait()
        active.append((reader, writer, task))
    reader, refused = controlled_callback(request(f"state={STATE}&code=one"))
    await listener.handler(reader, refused)
    assert refused.closed and not callback.result.done()
    await callback.__aexit__()
    assert listener.closed and listener.joined and callback.result.cancelled()
    assert not callback._connections
    assert all(reader.stopped and writer.closed and task.done() for reader, writer, task in active)
    # A connection callback already queued when the server closed is rejected too.
    reader, late = controlled_callback(request(f"state={STATE}&code=late"))
    await listener.handler(reader, late)
    assert late.closed and not late.data
    await callback.__aexit__()  # repeated cleanup is harmless


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [b"broken\r\n\r\n", b"\xff\r\n\r\n", b"GET /", b"x" * 9000 + b"\r\n\r\n"])
async def test_malformed_and_oversized_requests(payload, listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        # Model the production stream limit without binding a socket.
        reader = asyncio.StreamReader(limit=8192)
        reader.feed_data(payload)
        reader.feed_eof()
        _, writer = controlled_callback(b"")
        await listener.handler(reader, writer)
        assert writer.closed and not callback.result.done()
        assert not callback._connections
    assert listener.closed and listener.joined


@pytest.mark.anyio
async def test_read_and_drain_deadlines_are_bounded(monkeypatch, listener, controlled_callback):
    real_wait_for = asyncio.wait_for
    deadlines = []

    async def expire(awaitable, timeout):
        deadlines.append(timeout)
        if len(deadlines) == 1:
            # The controlled pending read is cancelled immediately, no clock wait.
            return await real_wait_for(awaitable, 0)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", expire)
    async with OidcCallback(URI, STATE) as callback:
        reader = BlockedReader()
        _, writer = controlled_callback(b"")
        await listener.handler(reader, writer)
        assert writer.closed and not callback.result.done()
    assert deadlines[0] == 5 and listener.closed and listener.joined


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["drain", "wait_closed"])
async def test_write_deadlines_and_errors_release_activity(stage, monkeypatch, listener, controlled_callback):
    real_wait_for = asyncio.wait_for
    deadlines = []

    async def wait_for(awaitable, timeout):
        deadlines.append(timeout)
        return await real_wait_for(awaitable, 0 if awaitable.cr_code.co_name == "stall" else timeout)

    async def stall():
        await asyncio.Future()

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    async with OidcCallback(URI, STATE) as callback:
        reader, writer = controlled_callback(request("state=wrong&code=one"))
        setattr(writer, stage, stall)
        await listener.handler(reader, writer)
        assert writer.closed and not callback._connections and not callback.result.done()
    assert deadlines and set(deadlines) == {5}
    assert listener.closed and listener.joined


@pytest.mark.anyio
async def test_transport_error_during_close_is_contained(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        reader, writer = controlled_callback(request("state=wrong&code=one"))

        async def broken_close():
            raise OSError("synthetic closed transport")

        writer.wait_closed = broken_close
        await listener.handler(reader, writer)
        assert writer.closed and not callback._connections and not callback.result.done()


@pytest.mark.anyio
async def test_complete_oversized_header_cannot_resolve(listener, controlled_callback):
    async with OidcCallback(URI, STATE) as callback:
        data = request(f"state={STATE}&code=one")[:-2] + b"X-Padding: " + b"x" * 8192 + b"\r\n\r\n"
        reader, writer = controlled_callback(data)
        await listener.handler(reader, writer)
        assert writer.closed and not callback.result.done()


@pytest.mark.anyio
async def test_fragment_and_empty_state_cannot_resolve(listener, controlled_callback):
    for expected_state, data in (
        (STATE, b"GET /callback?state=synthetic-state&code=one#fragment HTTP/1.1\r\n\r\n"),
        ("", request("state=&code=one")),
    ):
        async with OidcCallback(URI, expected_state) as callback:
            reader, writer = controlled_callback(data)
            await listener.handler(reader, writer)
            assert writer.closed and not callback.result.done()


@pytest.mark.anyio
async def test_shutdown_waits_for_writer_close(listener, controlled_callback):
    async with OidcCallback(URI, STATE):
        reader, writer = controlled_callback(request(f"state={STATE}&code=one"))
        joined = []

        async def wait_closed():
            assert writer.closed
            joined.append(True)

        writer.wait_closed = wait_closed
        await listener.handler(reader, writer)
        assert joined == [True]


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_login_deadline_and_cancellation_release_callback(monkeypatch, listener, cancel):
    from mvibe_auth._auth_config import AuthConfig
    from mvibe_auth._oidc_service import OidcAttempt, OidcService

    config = AuthConfig(enabled=True)
    reached_wait = asyncio.Event()

    class Session:
        closed = False

        def create_authorization_url(self, endpoint, **kwargs):
            reached_wait.set()
            return "https://issuer.invalid/authorize", kwargs["state"]

        async def aclose(self):
            self.closed = True

    session = Session()

    async def discover(session):
        return SimpleNamespace(authorization_endpoint="https://issuer.invalid/authorize")

    client = SimpleNamespace(config=config.to_oidc_config(), session=lambda: session, discover=discover)
    from unittest.mock import AsyncMock
    service = OidcService(client, SimpleNamespace(reload=AsyncMock(return_value=None)), config=config)
    callback_instances = []

    class CapturedCallback(OidcCallback):
        def __init__(self, *args):
            super().__init__(*args)
            callback_instances.append(self)

    monkeypatch.setattr("mvibe_auth._oidc_service.OidcCallback", CapturedCallback)
    real_timeout = asyncio.timeout
    deadlines = []
    timeout_contexts = []

    def controlled_timeout(seconds):
        deadlines.append(seconds)
        context = real_timeout(None)
        timeout_contexts.append(context)
        return context

    monkeypatch.setattr(asyncio, "timeout", controlled_timeout)
    attempt = OidcAttempt("synthetic-attempt")
    task = asyncio.create_task(service._complete(attempt))
    await reached_wait.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        timeout_contexts[0].reschedule(asyncio.get_running_loop().time())
        await task
        assert attempt.status == "failed" and attempt.detail
    assert deadlines == [300]
    assert listener.closed and listener.joined and session.closed
    assert callback_instances[0].result.cancelled()
    assert not callback_instances[0]._connections

# OIDC client contracts (2.4, 3.1–3.3, 4.5–4.6, 10.6).
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx2
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import ECKey, RSAKey
from pydantic import ValidationError

from mvibe_auth._oidc_client import OidcClient, OidcMetadata
from mvibe_auth._oidc_config import OidcConfig


ISSUER = "https://issuer.invalid"
CLIENT = "synthetic-client"
NONCE = "synthetic-nonce"


@pytest.fixture(scope="module")
def signing_keys():
    # Generated in memory; no issuer credentials or disk key files.
    return {
        "RSA": RSAKey.generate_key(2048, parameters={"kid": "synthetic-rsa"}),
        "EC256": ECKey.generate_key("P-256", parameters={"kid": "synthetic-ec256"}),
        "EC384": ECKey.generate_key("P-384", parameters={"kid": "synthetic-ec384"}),
        "EC512": ECKey.generate_key("P-521", parameters={"kid": "synthetic-ec512"}),
    }


@pytest.fixture
def issuer(monkeypatch, clock, signing_keys):
    import mvibe_auth._oidc_client as module
    import joserfc._rfc7519.claims as claims_module

    monkeypatch.setattr(module, "time", SimpleNamespace(time=clock.time))
    monkeypatch.setattr(claims_module, "_generate_now", lambda: int(clock.now))
    config = OidcConfig(ISSUER, CLIENT, "https://api.invalid", URI, "openid offline_access")
    metadata = OidcMetadata(
        issuer=ISSUER, authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token", jwks_uri=f"{ISSUER}/jwks",
        userinfo_endpoint=f"{ISSUER}/userinfo",
        id_token_signing_alg_values_supported=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
    )

    def signed(overrides=None, *, omit=(), alg="RS256", key=None):
        claims = {"iss": ISSUER, "sub": "synthetic-subject", "aud": CLIENT,
                  "exp": int(clock.now) + 300, "iat": int(clock.now), "nonce": NONCE}
        claims.update(overrides or {})
        for field in omit:
            claims.pop(field, None)
        key = key or signing_keys["RSA" if alg.startswith("RS") else f"EC{alg[2:]}"]
        return jwt.encode({"alg": alg, "kid": key.kid}, claims, key, algorithms=[alg])

    def raw(**overrides):
        response = {"access_token": "synthetic-access", "token_type": "Bearer", "expires_in": 600,
                    "refresh_token": "synthetic-refresh", "id_token": signed()}
        response.update(overrides)
        return response

    def session(controlled_http):
        # Preserve the real Authlib client and OAuth processing, replacing only I/O.
        return module.AsyncOAuth2Client(
            client_id=CLIENT, token_endpoint_auth_method="none", redirect_uri=URI,
            scope=config.scope, code_challenge_method="S256", follow_redirects=False,
            trust_env=False, transport=controlled_http.transport,
        )

    def jwks(controlled_http, key=None):
        controlled_http.queue("GET", metadata.jwks_uri,
                              json={"keys": [(key or signing_keys["RSA"]).as_dict(private=False)]})

    return SimpleNamespace(client=OidcClient(config), metadata=metadata, signed=signed,
                           raw=raw, session=session, jwks=jwks, clock=clock)


@pytest.mark.anyio
async def test_discovery_and_code_exchange_bind_pkce_state_nonce(issuer, controlled_http):
    controlled_http.queue("GET", f"{ISSUER}/.well-known/openid-configuration",
                          json=issuer.metadata.model_dump())
    async with issuer.session(controlled_http) as session:
        metadata = await issuer.client.discover(session)
        verifier = "synthetic-verifier-" + "a" * 48
        url, state = session.create_authorization_url(metadata.authorization_endpoint,
                    state=STATE, nonce=NONCE, code_verifier=verifier, audience="https://api.invalid")
        query = parse_qs(urlsplit(url).query)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert state == STATE
        assert query["code_challenge"] == [challenge] and query["code_challenge_method"] == ["S256"]
        assert query["nonce"] == [NONCE] and query["state"] == [STATE]
        assert query["client_id"] == [CLIENT] and query["redirect_uri"] == [URI]
        controlled_http.queue("POST", metadata.token_endpoint, json=issuer.raw())
        raw = await session.fetch_token(metadata.token_endpoint,
                authorization_response=f"{URI}?state={STATE}&code=synthetic-code",
                state=STATE, code_verifier=verifier)
        form = parse_qs(controlled_http.requests[-1].content.decode())
        assert form["code_verifier"] == [verifier] and form["code"] == ["synthetic-code"]
        assert form["grant_type"] == ["authorization_code"] and form["client_id"] == [CLIENT]
        issuer.jwks(controlled_http)
        tokens = await issuer.client.validated_tokens(session, metadata, raw, nonce=NONCE)
        assert tokens.subject == "synthetic-subject" and tokens.nonce == NONCE
        assert tokens.expires_at == issuer.clock.now + 600
        assert tokens.id_token_expires_at == issuer.clock.now + 300
        assert all("authorization" not in request.headers for request in controlled_http.requests)
    assert not controlled_http.responses


@pytest.mark.anyio
async def test_exchange_rejects_state_before_http(issuer, controlled_http):
    from authlib.oauth2.rfc6749.errors import MismatchingStateException
    async with issuer.session(controlled_http) as session:
        with pytest.raises(MismatchingStateException):
            await session.fetch_token(issuer.metadata.token_endpoint,
                authorization_response=f"{URI}?state=wrong&code=synthetic-code", state=STATE,
                code_verifier="synthetic-verifier-" + "a" * 48)
    assert not controlled_http.requests


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [
    ("issuer", "https://other.invalid"),
    ("authorization_endpoint", "http://remote.invalid/authorize"),
    ("token_endpoint", "https://user:password@issuer.invalid/token"),
    ("jwks_uri", "file:///keys"),
    ("userinfo_endpoint", "https://other.invalid/userinfo"),
    ("token_endpoint", "https://issuer.invalid/token#fragment"),
])
async def test_discovery_rejects_issuer_and_unsafe_endpoints(issuer, controlled_http, field, value):
    metadata = issuer.metadata.model_dump()
    metadata[field] = value
    controlled_http.queue("GET", f"{ISSUER}/.well-known/openid-configuration", json=metadata)
    async with issuer.session(controlled_http) as session:
        with pytest.raises(OidcError):
            await issuer.client.discover(session)
    assert len(controlled_http.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("alg", ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"])
async def test_supported_signed_algorithms(issuer, controlled_http, signing_keys, alg):
    key = signing_keys["RSA" if alg.startswith("RS") else f"EC{alg[2:]}"]
    issuer.jwks(controlled_http, key)
    async with issuer.session(controlled_http) as session:
        tokens = await issuer.client.validated_tokens(session, issuer.metadata,
                    issuer.raw(id_token=issuer.signed(alg=alg)), nonce=NONCE)
    assert tokens.subject == "synthetic-subject"


@pytest.mark.anyio
@pytest.mark.parametrize("overrides,omit", [
    ({"iss": "https://wrong.invalid"}, ()), ({"aud": "wrong-client"}, ()),
    ({"nonce": "wrong-nonce"}, ()), ({}, ("nonce",)),
    ({"sub": ""}, ()), ({"sub": 123}, ()), ({}, ("sub",)),
    ({}, ("exp",)), ({}, ("iat",)), ({}, ("iss",)), ({}, ("aud",)),
    ({"exp": 1_699_999_969}, ()), ({"iat": 1_700_000_031}, ()),
    ({"nbf": 1_700_000_031}, ()), ({"exp": "tomorrow"}, ()),
    ({"aud": [CLIENT, "other"]}, ()), ({"azp": "wrong-client"}, ()),
    ({"at_hash": "invalid-hash"}, ()),
])
async def test_invalid_signed_claims_rejected(issuer, controlled_http, overrides, omit):
    issuer.jwks(controlled_http)
    async with issuer.session(controlled_http) as session:
        with pytest.raises((JoseError, OidcError)):
            await issuer.client.validated_tokens(session, issuer.metadata,
                    issuer.raw(id_token=issuer.signed(overrides, omit=omit)), nonce=NONCE)


@pytest.mark.anyio
async def test_invalid_signature_rejected(issuer, controlled_http):
    wrong = RSAKey.generate_key(2048, parameters={"kid": "synthetic-rsa"})
    issuer.jwks(controlled_http)
    async with issuer.session(controlled_http) as session:
        with pytest.raises(JoseError):
            await issuer.client.validated_tokens(session, issuer.metadata,
                    issuer.raw(id_token=issuer.signed(key=wrong)), nonce=NONCE)


@pytest.mark.anyio
@pytest.mark.parametrize("algorithms", [["HS256"], ["none"], [], ["RS384"]])
async def test_unapproved_algorithm_rejected(issuer, controlled_http, algorithms):
    issuer.jwks(controlled_http)
    metadata = issuer.metadata.model_copy(update={"id_token_signing_alg_values_supported": algorithms})
    async with issuer.session(controlled_http) as session:
        with pytest.raises((OidcError, JoseError)):
            await issuer.client.validated_tokens(session, metadata, issuer.raw(), nonce=NONCE)


@pytest.mark.anyio
@pytest.mark.parametrize("overrides", [
    {"access_token": ""}, {"access_token": None}, {"access_token": 123},
    {"token_type": "Basic"}, {"token_type": None}, {"token_type": 123},
    {"expires_in": 0}, {"expires_in": -1}, {"expires_in": float("inf")},
    {"expires_in": float("nan")}, {"expires_in": None}, {"id_token": None},
])
async def test_invalid_token_response_rejected_before_jwks(issuer, controlled_http, overrides):
    async with issuer.session(controlled_http) as session:
        with pytest.raises((OidcError, ValidationError)):
            await issuer.client.validated_tokens(session, issuer.metadata, issuer.raw(**overrides), nonce=NONCE)
    assert not controlled_http.requests


@pytest.mark.anyio
async def test_refresh_rotation_and_omitted_replacements(issuer, controlled_http):
    async with issuer.session(controlled_http) as session:
        issuer.jwks(controlled_http)
        previous = await issuer.client.validated_tokens(session, issuer.metadata, issuer.raw(), nonce=NONCE)
        controlled_http.queue("POST", issuer.metadata.token_endpoint, json=issuer.raw(
            access_token="synthetic-access-rotated", refresh_token="synthetic-refresh-rotated",
            id_token=issuer.signed(omit=("nonce",))))
        raw = await session.refresh_token(issuer.metadata.token_endpoint,
                                         refresh_token=previous.refresh_token.get_secret_value())
        form = parse_qs(controlled_http.requests[-1].content.decode())
        assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["synthetic-refresh"]
        issuer.jwks(controlled_http)
        rotated = await issuer.client.validated_tokens(session, issuer.metadata, raw, previous=previous)
        assert rotated.refresh_token.get_secret_value() == "synthetic-refresh-rotated"
        assert rotated.subject == previous.subject and rotated.nonce == NONCE
        omitted = {"access_token": "synthetic-access-next", "token_type": "bearer", "expires_in": 90}
        retained = await issuer.client.validated_tokens(session, issuer.metadata, omitted, previous=rotated)
        assert retained.refresh_token == rotated.refresh_token
        assert retained.id_token == rotated.id_token
        assert retained.id_token_expires_at == rotated.id_token_expires_at
        assert retained.access_token.get_secret_value() == "synthetic-access-next"
    assert not controlled_http.responses


@pytest.mark.anyio
@pytest.mark.parametrize("changed", [{"sub": "changed-subject"}, {"nonce": "wrong-nonce"}])
async def test_refresh_rejects_identity_or_nonce_change(issuer, controlled_http, changed):
    async with issuer.session(controlled_http) as session:
        issuer.jwks(controlled_http)
        previous = await issuer.client.validated_tokens(session, issuer.metadata, issuer.raw(), nonce=NONCE)
        issuer.jwks(controlled_http)
        with pytest.raises((OidcError, JoseError)):
            await issuer.client.validated_tokens(session, issuer.metadata,
                issuer.raw(id_token=issuer.signed(changed)), previous=previous)
        assert previous.subject == "synthetic-subject" and previous.nonce == NONCE


@pytest.mark.anyio
async def test_original_nonce_required_when_refresh_includes_nonce(issuer, controlled_http):
    async with issuer.session(controlled_http) as session:
        issuer.jwks(controlled_http)
        previous = await issuer.client.validated_tokens(session, issuer.metadata, issuer.raw(), nonce=NONCE)
        issuer.jwks(controlled_http)
        with pytest.raises(OidcError, match="original nonce is unavailable"):
            await issuer.client.validated_tokens(session, issuer.metadata, issuer.raw(),
                                                previous=previous.model_copy(update={"nonce": None}))


@pytest.mark.anyio
@pytest.mark.parametrize("offset", [-30, -29, 0, 30])
async def test_id_token_expiry_uses_documented_library_leeway(issuer, controlled_http, offset):
    issuer.jwks(controlled_http)
    async with issuer.session(controlled_http) as session:
        tokens = await issuer.client.validated_tokens(session, issuer.metadata,
                issuer.raw(id_token=issuer.signed({"exp": int(issuer.clock.now) + offset})), nonce=NONCE)
    assert tokens.id_token_expires_at == issuer.clock.now + offset

@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [
    ("exp", float("nan")), ("exp", float("inf")),
    ("iat", float("nan")), ("iat", float("-inf")),
    ("nbf", float("nan")), ("nbf", float("-inf")),
])
async def test_nonfinite_id_token_lifetime_rejected(issuer, controlled_http, field, value):
    issuer.jwks(controlled_http)
    async with issuer.session(controlled_http) as session:
        with pytest.raises((OidcError, JoseError)):
            await issuer.client.validated_tokens(session, issuer.metadata,
                issuer.raw(id_token=issuer.signed({field: value})), nonce=NONCE)

@pytest.mark.anyio
async def test_symmetric_token_rejected_even_if_advertised(issuer, controlled_http):
    from joserfc.jwk import OctKey
    key = OctKey.generate_key(256, parameters={"kid": "synthetic-hmac"})
    encoded = jwt.encode({"alg": "HS256", "kid": key.kid}, {
        "iss": ISSUER, "aud": CLIENT, "sub": "synthetic-subject", "nonce": NONCE,
        "iat": int(issuer.clock.now), "exp": int(issuer.clock.now) + 300,
    }, key, algorithms=["HS256"])
    issuer.jwks(controlled_http)
    metadata = issuer.metadata.model_copy(update={"id_token_signing_alg_values_supported": ["HS256", "RS256"]})
    async with issuer.session(controlled_http) as session:
        with pytest.raises(JoseError):
            await issuer.client.validated_tokens(session, metadata, issuer.raw(id_token=encoded), nonce=NONCE)


@pytest.mark.anyio
async def test_fractional_numeric_dates_and_multi_audience_authorized_party(issuer, controlled_http):
    issuer.jwks(controlled_http)
    async with issuer.session(controlled_http) as session:
        tokens = await issuer.client.validated_tokens(session, issuer.metadata, issuer.raw(
            id_token=issuer.signed({"exp": issuer.clock.now + 300.5, "iat": issuer.clock.now - 0.5,
                                    "nbf": issuer.clock.now - 0.5, "aud": [CLIENT, "other"], "azp": CLIENT})),
            nonce=NONCE)
    assert tokens.id_token_expires_at == issuer.clock.now + 300.5
