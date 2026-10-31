from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from mvibe_auth.oidc_marker import oidc_enabled


@dataclass(frozen=True, slots=True)
class _CliRegistration:
    client_id: str
    audience: str
    # First-party web origins that also accept the CLI ID token.
    web_origins: tuple[str, ...]


# Dedicated CLI registrations: sre-infra PR #10140, VIBE-5365.
_CLI_REGISTRATIONS = {
    "https://auth.globalaegis.net": _CliRegistration(
        client_id="3484b11e-2029-4a52-b289-dc826fc03c00",
        audience="https://api.globalaegis.net",
        web_origins=("https://chat.globalaegis.net", "https://console.globalaegis.net"),
    ),
    "https://auth.mistral.ai": _CliRegistration(
        client_id="829da7e7-628f-4795-829a-8cca609c76d4",
        audience="https://api.mistral.ai",
        web_origins=("https://chat.mistral.ai", "https://console.mistral.ai"),
    ),
}
_DEFAULT_ISSUER = "https://auth.mistral.ai"


class OidcError(Exception):
    """A safe-to-display error from the isolated CLI experiment."""


def require_oidc_enabled() -> None:
    if not oidc_enabled():
        raise OidcError("CLI OIDC experiment is disabled")


def validate_oidc_url(value: str, *, loopback: bool = False) -> str:
    try:
        # urlsplit otherwise silently removes some controls and accepts spaces
        # and backslashes in hostnames. Reject before parsing at this boundary.
        if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
            raise ValueError("Malformed URL")
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, TypeError) as cause:
        raise OidcError("Invalid OIDC URL") from cause
    local = parsed.hostname in {"127.0.0.1", "::1"}
    invalid_origin = (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    )
    invalid_scheme = parsed.scheme != "https" and not (
        local and parsed.scheme == "http"
    )
    if invalid_origin or invalid_scheme or parsed.query or parsed.fragment:
        raise OidcError("OIDC URLs must use HTTPS (HTTP is allowed only on loopback)")
    if loopback and (not local or parsed.scheme != "http" or not port):
        raise OidcError(
            "OIDC redirect must use HTTP on a loopback IP with a fixed port"
        )
    return value


@dataclass(frozen=True, slots=True)
class OidcConfig:
    issuer: str
    client_id: str
    audience: str
    redirect_uri: str
    scope: str = "openid offline_access email profile"

    @property
    def web_origins(self) -> tuple[str, ...]:
        registration = _CLI_REGISTRATIONS.get(self.issuer)
        return registration.web_origins if registration is not None else ()

    @classmethod
    def from_env(cls) -> OidcConfig:
        """Legacy boundary; environment parsing belongs to AuthConfig alone."""
        from mvibe_auth._auth_config import AuthConfig
        from mvibe_auth._auth_contracts import AuthError

        try:
            return AuthConfig.from_env().to_oidc_config()
        except AuthError as cause:
            raise OidcError(str(cause)) from cause
