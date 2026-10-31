"""Immutable session configuration below the service/facade dependency boundary."""
from __future__ import annotations

from dataclasses import dataclass
import os

from mvibe_auth._auth_contracts import AuthError, AuthFailure
from mvibe_auth._oidc_config import (
    OidcConfig, OidcError, _CLI_REGISTRATIONS, _DEFAULT_ISSUER, validate_oidc_url,
)

@dataclass(frozen=True, slots=True)
class AuthConfig:
    """One session's captured settings, including disablement and tenant selection.

    ``from_env`` is the authentication environment reader. Legacy boundaries
    delegate here; reusable consumers retain this snapshot and pass explicitly
    mapped ``OidcConfig`` settings to existing clients.
    Configuration is validated even when authentication is disabled.
    """

    issuer: str = _DEFAULT_ISSUER
    client_id: str = _CLI_REGISTRATIONS[_DEFAULT_ISSUER].client_id
    audience: str = _CLI_REGISTRATIONS[_DEFAULT_ISSUER].audience
    redirect_uri: str = 'http://127.0.0.1:8766/callback'
    scope: str = 'openid offline_access email profile'
    enabled: bool = False
    organization_id: str = ''
    workspace_id: str = ''

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.enabled, bool) or not all(isinstance(value, str) for value in (
                self.issuer, self.client_id, self.audience, self.redirect_uri,
                self.scope, self.organization_id, self.workspace_id,
            )):
                raise OidcError('Invalid configuration type')
            validate_oidc_url(self.issuer)
            validate_oidc_url(self.redirect_uri, loopback=True)
            if not self.audience.strip() or not self.client_id.strip():
                raise OidcError('Missing inference registration')
            validate_oidc_url(self.audience)
            if 'openid' not in self.scope.split():
                raise OidcError('Missing openid scope')
            for selection in (self.organization_id, self.workspace_id):
                if selection and not selection.strip():
                    raise OidcError('Invalid tenant selection')
        except (OidcError, ValueError, TypeError, AttributeError) as cause:
            raise AuthError(AuthFailure('invalid')) from cause

    def require_enabled(self) -> None:
        if not self.enabled:
            raise AuthError(AuthFailure('rejected'))

    def to_oidc_config(self) -> OidcConfig:
        """Map enabled, validated settings to the existing client/service primitive."""
        self.require_enabled()
        return OidcConfig(self.issuer, self.client_id, self.audience, self.redirect_uri, self.scope)

    @classmethod
    def from_oidc_config(cls, config: OidcConfig, *, enabled: bool = False,
                         organization_id: str = '', workspace_id: str = '') -> AuthConfig:
        """Capture legacy settings without environment reads; validate for inference."""
        return cls(issuer=config.issuer, client_id=config.client_id, audience=config.audience,
                   redirect_uri=config.redirect_uri, scope=config.scope, enabled=enabled,
                   organization_id=organization_id, workspace_id=workspace_id)

    @classmethod
    def from_env(cls) -> AuthConfig:
        env = os.environ.copy()
        prefix = 'VIBE_CLI_OIDC_'
        issuer = env.get(prefix + 'ISSUER', _DEFAULT_ISSUER)
        registration = _CLI_REGISTRATIONS.get(issuer)
        return cls(
            issuer=issuer,
            client_id=env.get(prefix + 'CLIENT_ID', registration.client_id if registration else ''),
            audience=env.get(prefix + 'AUDIENCE', registration.audience if registration else ''),
            redirect_uri=env.get(prefix + 'REDIRECT_URI', 'http://127.0.0.1:8766/callback'),
            scope=env.get(prefix + 'SCOPE', 'openid offline_access email profile'),
            enabled=env.get(prefix + 'ENABLED', '').lower() in {'1', 'true'},
            organization_id=env.get(prefix + 'ORGANIZATION_ID', ''),
            workspace_id=env.get(prefix + 'WORKSPACE_ID', ''),
        )
