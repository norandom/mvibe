from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import secrets
import time
import math
from typing import Literal, cast

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
import httpx2

from mvibe_auth._http import MistralPlatformHTTPClient
from mvibe_auth._credentials_port import PlatformCredentialTarget
from mvibe_auth._oidc_callback import OidcCallback
from mvibe_auth._oidc_client import OidcClient
from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import (
    AuthError, AuthFailure, LoginCompleted, LoginOutcome, LoginReusedCurrent,
)
from mvibe_auth._coordination import ResolutionBudget
from mvibe_auth._oidc_config import OidcConfig, OidcError
from mvibe_auth._oidc_credentials import (
    ABSENT,
    OidcCredentialStore,
    OidcStorageError,
    OidcTokens,
)
from mvibe_auth._oidc_platform_credentials import OidcPlatformCredentialProvider
from mvibe_auth.http import build_ssl_context


@dataclass(slots=True)
class OidcAttempt:
    attempt_id: str
    authorization_url: str | None = None
    status: Literal["waiting", "exchanging", "completed", "failed"] = "waiting"
    detail: str | None = None
    error_type: Literal["oidc_error", "storage_error", "unexpected_error"] | None = None
    task: asyncio.Task[None] | None = None
    started_at: float = field(default_factory=time.monotonic)
    started_timestamp: float = field(default_factory=time.time)
    _done_notified: bool = False
    outcome: LoginOutcome | None = None


class OidcService:
    """One active login and serialized refresh-on-access per app server."""

    def __init__(self, client: OidcClient, store: OidcCredentialStore,
                 *, config: AuthConfig | None = None) -> None:
        if config is None:
            captured = AuthConfig.from_env()
            config = AuthConfig.from_oidc_config(
                client.config, enabled=captured.enabled,
                organization_id=captured.organization_id, workspace_id=captured.workspace_id,
            )
        expected = OidcConfig(config.issuer, config.client_id, config.audience,
                              config.redirect_uri, config.scope)
        if client.config != expected:
            raise OidcError("OIDC client configuration does not match its owner")
        self.config = config
        self.client = client
        self.store = store
        self._lock = asyncio.Lock()
        self._attempt: OidcAttempt | None = None
        self.on_attempt_started: Callable[[OidcAttempt], None] | None = None
        self.on_attempt_done: (
            Callable[[OidcAttempt, Literal["success", "error", "cancelled"]], None]
            | None
        ) = None

    @classmethod
    def from_env(cls) -> OidcService:
        return cls.from_config(AuthConfig.from_env())

    @classmethod
    def from_config(cls, config: AuthConfig) -> OidcService:
        oidc = OidcConfig(config.issuer, config.client_id, config.audience,
                          config.redirect_uri, config.scope)
        return cls(OidcClient(oidc), OidcCredentialStore(), config=config)

    async def start(self) -> OidcAttempt:
        self.config.require_enabled()
        async with self._lock:
            await self.cancel()
            attempt = OidcAttempt(secrets.token_urlsafe(24))
            self._attempt = attempt
            if self.on_attempt_started is not None:
                try:
                    self.on_attempt_started(attempt)
                except Exception:
                    pass
            attempt.task = asyncio.create_task(self._complete(attempt))
            return attempt

    async def _complete(self, attempt: OidcAttempt) -> None:
        session: AsyncOAuth2Client | None = None
        callback: OidcCallback | None = None
        try:
            async with asyncio.timeout(300):
                self.config.require_enabled()
                expected = await self.store.reload()
                expected = expected if expected is not None else ABSENT
                budget = ResolutionBudget()
                session = self.client.session()
                metadata = await self.client.discover(session)
                self.config.require_enabled()
                verifier, nonce, state = (
                    secrets.token_urlsafe(64),
                    secrets.token_urlsafe(32),
                    secrets.token_urlsafe(32),
                )
                callback = OidcCallback(self.client.config.redirect_uri, state)
                await callback.__aenter__()
                extra = (
                    {"audience": self.client.config.audience}
                    if self.client.config.audience
                    else {}
                )
                attempt.authorization_url, _ = session.create_authorization_url(
                    metadata.authorization_endpoint,
                    state=state,
                    code_verifier=verifier,
                    nonce=nonce,
                    **extra,
                )
                response = await callback.result
                self.config.require_enabled()
                attempt.status = "exchanging"
                raw = await session.fetch_token(
                    metadata.token_endpoint,
                    authorization_response=response,
                    state=callback.state,
                    code_verifier=verifier,
                )
                tokens = await self.client.validated_tokens(
                    session, metadata, raw, nonce=nonce
                )
                async with self._lock:
                    self.config.require_enabled()
                    saved = await self.store.save(
                        tokens, expected=expected, complete_on_cancel=True
                    )
                    if saved:
                        attempt.outcome = LoginCompleted()
                    else:
                        budget.conflict()
                        await self.access_tokens(require_id_token=True, budget=budget)
                        attempt.outcome = LoginReusedCurrent()
                attempt.status = "completed"
        except asyncio.CancelledError:
            attempt.status = "failed"
            attempt.outcome = AuthFailure("cancelled")
            attempt.detail = attempt.outcome.message
            raise
        except AuthError as exc:
            attempt.status = "failed"
            attempt.outcome = exc.failure
            attempt.detail = exc.failure.message
            attempt.error_type = "oidc_error"
        except OidcError:
            attempt.status = "failed"
            attempt.outcome = AuthFailure("rejected")
            attempt.detail = attempt.outcome.message
            attempt.error_type = "oidc_error"
        except Exception as exc:
            # Library/HTTP/storage errors can contain codes or token payloads.
            attempt.status = "failed"
            attempt.outcome = AuthFailure("transient", retryable=True)
            attempt.error_type = (
                "storage_error"
                if isinstance(exc, OidcStorageError)
                else "unexpected_error"
            )
            attempt.detail = "OIDC sign-in failed; check issuer, client registration and KeyRing availability"
        finally:
            try:
                if callback is not None:
                    await callback.__aexit__()
            finally:
                try:
                    if session is not None:
                        await cast(httpx2.AsyncClient, session).aclose()
                finally:
                    self._notify_attempt_done(attempt)

    def _notify_attempt_done(self, attempt: OidcAttempt) -> None:
        if self.on_attempt_done is None or attempt._done_notified:
            return
        attempt._done_notified = True
        status = (
            "cancelled"
            if isinstance(attempt.outcome, AuthFailure) and attempt.outcome.code == "cancelled"
            else "success"
            if attempt.status == "completed"
            else "error"
            if attempt.status == "failed"
            else "cancelled"
        )
        try:
            self.on_attempt_done(attempt, status)
        except Exception:
            # Analytics must not change authentication or resource cleanup.
            pass

    def poll(self, attempt_id: str) -> OidcAttempt:
        self.config.require_enabled()
        if self._attempt is None or self._attempt.attempt_id != attempt_id:
            raise OidcError("Unknown OIDC sign-in attempt")
        return self._attempt

    async def wait(self, attempt_id: str) -> LoginOutcome:
        """Return the single terminal result without cancelling the owned login."""
        attempt = self.poll(attempt_id)
        if attempt.task is not None:
            await asyncio.shield(asyncio.gather(attempt.task, return_exceptions=True))
        if attempt.outcome is None:
            raise AuthError(AuthFailure("transient", retryable=True))
        return attempt.outcome

    async def cancel(self, attempt_id: str | None = None) -> None:
        attempt = self._attempt
        if attempt is None or (
            attempt_id is not None and attempt.attempt_id != attempt_id
        ):
            return
        if attempt.task is not None:
            attempt.task.cancel()
            await asyncio.gather(attempt.task, return_exceptions=True)
        if attempt.outcome is None:
            attempt.status = "failed"
            attempt.outcome = AuthFailure("cancelled")
            attempt.detail = attempt.outcome.message
        self._notify_attempt_done(attempt)

    async def clear(self) -> None:
        self.config.require_enabled()
        async with self._lock:
            await self.cancel()
            await self.store.clear()

    async def access_tokens(
        self, *, force_refresh: bool = False, require_id_token: bool = False,
        budget: ResolutionBudget | None = None,
    ) -> OidcTokens:
        """Resolve from storage; inference opts into the ID-token freshness rule.

        Pass the same budget through tenant resolution so all observed rotation
        conflicts share the original pass plus a single retry.
        """
        self.config.require_enabled()
        budget = budget if budget is not None else ResolutionBudget()
        rejected_refresh = None
        async with self.store.coordinator.hold_async():
            while True:
                self.config.require_enabled()
                tokens = await self.store.reload()
                config = self.client.config
                if tokens is None:
                    raise OidcError("OIDC re-login required: no stored credentials")
                if (tokens.issuer, tokens.client_id, tokens.audience) != (
                    config.issuer, config.client_id, config.audience,
                ):
                    raise OidcError("OIDC re-login required: credential configuration changed")
                if not force_refresh and self._fresh(tokens.expires_at) and (
                    not require_id_token or self._fresh_id(tokens)
                ):
                    return tokens
                if tokens.refresh_token is None or not tokens.refresh_token.get_secret_value():
                    raise OidcError("OIDC re-login required: no refresh token")
                if tokens.refresh_token == rejected_refresh:
                    raise OidcError("OIDC re-login required: refresh token rejected")
                session = self.client.session()
                async with cast(httpx2.AsyncClient, session):
                    metadata = await self.client.discover(session)
                    try:
                        raw = await session.refresh_token(
                            metadata.token_endpoint,
                            refresh_token=tokens.refresh_token.get_secret_value(),
                            scope=None,
                        )
                    except OAuthError as exc:
                        if exc.error != "invalid_grant":
                            raise OidcError("OIDC refresh failed; retry the probe later") from None
                        current = await self.store.reload()
                        if OidcCredentialStore._matches(current, tokens):
                            raise OidcError("OIDC re-login required: refresh token rejected") from None
                        rejected_refresh = tokens.refresh_token
                        budget.conflict()
                        # A newer usable set should be reused even after an
                        # explicit refresh of the rejected original set.
                        force_refresh = False
                        continue
                    updated = await self.client.validated_tokens(
                        session, metadata, raw, previous=tokens
                    )
                if not updated.same_identity(tokens):
                    raise OidcError("OIDC refresh changed the authenticated identity")
                if require_id_token and not self._fresh_id(updated):
                    raise OidcError("OIDC re-login required: ID token was not renewed")
                if not self._fresh(updated.expires_at):
                    raise OidcError("OIDC re-login required: access token was not renewed")
                self.config.require_enabled()
                if await self.store.save_if_current(updated, expected=tokens):
                    return updated
                budget.conflict()
                force_refresh = False

    @staticmethod
    def _fresh(expires_at: float | None) -> bool:
        return expires_at is not None and math.isfinite(expires_at) and expires_at > time.time() + 30

    @classmethod
    def _fresh_id(cls, tokens: OidcTokens) -> bool:
        return bool(tokens.id_token.get_secret_value()) and cls._fresh(tokens.id_token_expires_at)

    async def probe(self) -> dict[str, str]:
        self.config.require_enabled()
        session = self.client.session()
        async with cast(httpx2.AsyncClient, session):
            metadata = await self.client.discover(session)
        if metadata.userinfo_endpoint is None:
            raise OidcError("OIDC issuer does not advertise a userinfo endpoint")
        # OpenID Connect Core 1.0 section 5.3: UserInfo uses the access token.
        target = PlatformCredentialTarget(
            base_url=metadata.userinfo_endpoint, audience=self.client.config.audience
        )
        credentials = OidcPlatformCredentialProvider(
            target, self.access_tokens, config=self.client.config, auth_config=self.config
        )
        async with MistralPlatformHTTPClient(
            auth_config=self.config,
            base_url=target.base_url,
            audience=target.audience,
            credentials=credentials,
            timeout=20,
            transport=httpx2.AsyncHTTPTransport(
                verify=build_ssl_context(), trust_env=False
            ),
        ) as client:
            response = await client.get(metadata.userinfo_endpoint)
        if response.status_code == httpx2.codes.UNAUTHORIZED:
            raise OidcError("OIDC access token rejected; re-login required")
        response.raise_for_status()
        identity = response.json()
        if identity.get("sub") != credentials.subject:
            raise OidcError("OIDC UserInfo subject mismatch")
        return {
            "issuer": self.client.config.issuer,
            "subject": credentials.subject or "",
        }
