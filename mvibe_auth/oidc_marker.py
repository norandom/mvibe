"""The CLI OIDC flag and its API-key marker, kept free of heavy imports.

Existing clients require a nonempty API key before they can make a request.
In OIDC mode they receive this non-secret marker, and the HTTP boundary
replaces it with the current ID token and tenant headers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mvibe_auth._auth_config import AuthConfig

OIDC_API_KEY = "vibe-cli-oidc"
# Cache keys derived from an OIDC identity, never from the marker itself.
OIDC_CACHE_KEY_PREFIX = "cli-oidc:v1:"
_OIDC_AUTHORIZATION = f"Bearer {OIDC_API_KEY}"


def oidc_enabled(config: AuthConfig | None = None) -> bool:
    """Use a consumer's snapshot; no-argument calls are legacy boundaries."""
    if config is None:
        from mvibe_auth._auth_config import AuthConfig

        config = AuthConfig.from_env()
    return config.enabled


def is_oidc_marker(api_key: str | None) -> bool:
    return api_key == OIDC_API_KEY


def is_oidc_authorization(header: str | None) -> bool:
    return header == _OIDC_AUTHORIZATION
