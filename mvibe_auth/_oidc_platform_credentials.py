from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import sha256
import math
import time
from typing import Literal

from mvibe_auth._credentials_port import (
    PlatformCredentialIdentity,
    PlatformCredentialRequired,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._oidc_config import OidcConfig, OidcError
from mvibe_auth._oidc_credentials import OidcTokens


class OidcPlatformCredentialProvider:
    """The access token from an OIDC bundle, for one explicitly bound endpoint."""

    def __init__(
        self,
        target: PlatformCredentialTarget,
        resolve_tokens: Callable[[], Awaitable[OidcTokens | None]],
        *,
        config: OidcConfig,
        auth_config: AuthConfig | None = None,
    ) -> None:
        self.auth_config = auth_config if auth_config is not None else AuthConfig.from_oidc_config(
            config, enabled=AuthConfig.from_env().enabled,
        )
        if (config.issuer, config.client_id, config.audience, config.redirect_uri, config.scope) != (
            self.auth_config.issuer, self.auth_config.client_id, self.auth_config.audience,
            self.auth_config.redirect_uri, self.auth_config.scope,
        ):
            raise OidcError("OIDC provider configuration does not match its owner")
        self.target = target
        self._resolve_tokens = resolve_tokens
        self._config = config
        self._rejected: PlatformCredentialIdentity | None = None
        self.subject: str | None = None

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        self.auth_config.require_enabled()
        if target != self.target:
            return PlatformCredentialRequired(reason="invalid")
        try:
            tokens = await self._resolve_tokens()
        except OidcError:
            raise
        except Exception:
            return PlatformCredentialRequired(reason="transient")
        if tokens is None:
            return PlatformCredentialRequired(reason="missing")
        return self._snapshot(tokens)

    def _snapshot(self, tokens: OidcTokens) -> PlatformCredentialResult:
        if (tokens.issuer, tokens.client_id, tokens.audience) != (
            self._config.issuer,
            self._config.client_id,
            self._config.audience,
        ) or tokens.audience != (self.target.audience or ""):
            return PlatformCredentialRequired(reason="invalid")
        token, expires_at = tokens.access_token, tokens.expires_at
        if not token.get_secret_value():
            return PlatformCredentialRequired(reason="invalid")
        if (
            expires_at is None
            or not math.isfinite(expires_at)
            or expires_at <= time.time()
        ):
            return PlatformCredentialRequired(reason="expired")
        headers = {"Authorization": f"Bearer {token.get_secret_value()}"}
        namespace = sha256(
            f"{tokens.issuer}\0{tokens.client_id}\0{tokens.audience}".encode()
        ).hexdigest()
        snapshot = PlatformCredentialSnapshot(
            kind="oauth_access_token",
            headers=headers,
            cache_namespace=f"cli-oidc:{namespace}",
            revision=sha256(token.get_secret_value().encode()).hexdigest(),
            expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
            source="the experimental CLI OIDC session",
        )
        if snapshot.identity == self._rejected:
            return PlatformCredentialRequired(
                reason="rejected", observed_revision=snapshot.revision
            )
        self.subject = tokens.subject
        return snapshot

    async def reject(
        self,
        target: PlatformCredentialTarget,
        *,
        observed: PlatformCredentialIdentity,
        reason: Literal["http_unauthorized"],
    ) -> PlatformCredentialResult:
        del reason
        if target != self.target or observed.kind != "oauth_access_token":
            return PlatformCredentialRequired(reason="invalid")
        self._rejected = observed
        return PlatformCredentialRequired(
            reason="rejected", observed_revision=observed.revision
        )
