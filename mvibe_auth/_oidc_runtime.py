from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import math
import threading
import time

import httpx2
from pydantic import BaseModel

from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import AuthError
from mvibe_auth._coordination import CoordinationError
from mvibe_auth._oidc_config import OidcError
from mvibe_auth._oidc_credentials import (
    OidcCredentialStore,
    OidcStorageError,
    OidcTenant,
    OidcTokens,
)
from mvibe_auth._oidc_service import OidcService
from mvibe_auth.concurrency import run_sync
from mvibe_auth.http import VibeAsyncHTTPClient, build_ssl_context
from mvibe_auth.oidc_marker import OIDC_API_KEY, OIDC_CACHE_KEY_PREFIX

_WORKSPACE_PAGE_SIZE = 100
_refresh_lock = threading.Lock()
# Serializes workspace discovery, so concurrent requests discover once.
_tenant_lock = threading.Lock()


class _Workspace(BaseModel):
    id: str
    organization_id: str


class _Workspaces(BaseModel):
    workspaces: list[_Workspace]


class OidcRuntime:
    """Configuration owner for existing runtime helpers, below the public facade.

    Consumers pass ``headers`` as their transport resolver and the same ``config``
    as its auth_config. No credential policy is duplicated here.
    """

    def __init__(self, config: AuthConfig, *, service: OidcService | None = None) -> None:
        self.config = config
        self.service = service if service is not None else OidcService.from_config(config)
        if self.service.config != config:
            raise OidcError("OIDC runtime configuration does not match its service")

    @classmethod
    def from_env(cls) -> OidcRuntime:
        return cls(AuthConfig.from_env())

    async def headers(self, url: str) -> dict[str, str]:
        return await _oidc_headers(url, self.config, self.service)

    def stored_credential(self) -> str | None:
        return stored_oidc_credential(self.config)

    def stored_cache_key(self) -> str | None:
        return stored_oidc_cache_key(self.config)


def stored_oidc_credential(config: AuthConfig | None = None) -> str | None:
    """The marker when a matching session is stored, else None (never raises).

    Unreadable storage or invalid configuration reads as signed out, so callers
    of ``resolve_api_key`` keep their "a key or None" contract and setup can
    still offer browser sign-in.
    """
    try:
        config = config if config is not None else AuthConfig.from_env()
        config.require_enabled()
        tokens = OidcCredentialStore()._load()
    except (AuthError, OidcError, OidcStorageError):
        return None
    if tokens is None or (tokens.issuer, tokens.client_id, tokens.audience) != (
        config.issuer,
        config.client_id,
        config.audience,
    ):
        return None
    return OIDC_API_KEY


def stored_oidc_cache_key(config: AuthConfig | None = None) -> str | None:
    """Anonymous identity/tenant key, without token refresh or network discovery.

    An unresolved tenant is not a cache identity: bypass persistent caches until
    explicit overrides or the tenant stored with the session identify it. The
    stored tenant survives token refresh and restarts, so the key does too.
    """
    try:
        config = config if config is not None else AuthConfig.from_env()
        config.require_enabled()
        tokens = OidcCredentialStore()._load()
    except (AuthError, OidcError, OidcStorageError):
        return None
    if (
        tokens is None
        or not tokens.subject
        or (tokens.issuer, tokens.client_id, tokens.audience)
        != (config.issuer, config.client_id, config.audience)
    ):
        return None
    organization = config.organization_id
    workspace = config.workspace_id
    if not organization:
        tenant = _stored_tenant(tokens, workspace)
        if tenant is None:
            return None
        organization, workspace = tenant.organization_id, tenant.workspace_id
    if not organization:
        return None
    material = json.dumps(
        [
            tokens.issuer,
            tokens.client_id,
            tokens.audience,
            tokens.subject,
            organization,
            workspace,
        ],
        separators=(",", ":"),
    )
    return f"{OIDC_CACHE_KEY_PREFIX}{sha256(material.encode()).hexdigest()}"


def _stored_tenant(tokens: OidcTokens, selection: str) -> OidcTenant | None:
    """The tenant stored with ``tokens``, if discovered for this workspace
    override (``""`` for the default).
    """
    tenant = tokens.tenant
    if tenant is None or tenant.selection != selection:
        return None
    return tenant


async def oidc_headers(url: str, config: AuthConfig | None = None) -> dict[str, str]:
    """Compatibility boundary; reusable consumers retain an OidcRuntime instead."""
    owner = OidcRuntime(config if config is not None else AuthConfig.from_env())
    return await owner.headers(url)


async def _oidc_headers(url: str, captured: AuthConfig, service: OidcService) -> dict[str, str]:
    config = captured.to_oidc_config()
    destination = httpx2.URL(url)
    audience = httpx2.URL(config.audience)
    origins = {(audience.scheme, audience.host, audience.port)}
    for origin in map(httpx2.URL, config.web_origins):
        origins.add((origin.scheme, origin.host, origin.port))
    scheme = {"wss": "https", "ws": "http"}.get(destination.scheme, destination.scheme)
    if (scheme, destination.host, destination.port) not in origins:
        raise OidcError(
            "OIDC destination does not match the configured issuer/audience"
        )
    token = await asyncio.to_thread(_resolve_id_token, service)
    headers = {"Authorization": f"Bearer {token}"}
    # Albe IdTokenGrant._resolve_membership requires an explicit organization;
    # workspace is optional (albe/services/auth/grants/oidc_id_token.py).
    if not captured.organization_id:
        headers.update(await asyncio.to_thread(_tenant_headers, captured, token))
    for value, header in (
        (captured.organization_id, "x-external-organization-id"),
        (captured.workspace_id, "x-external-workspace-id"),
    ):
        if value:
            headers[header] = value
    return headers


def _tenant_headers(config: AuthConfig, token: str) -> dict[str, str]:
    workspace = config.workspace_id
    store = OidcCredentialStore()
    session = _session_for(store, token)
    if session is not None and (tenant := _stored_tenant(session, workspace)):
        return _tenant_header_values(tenant)
    with _tenant_lock:
        # Another request may have discovered it while this one waited.
        session = _session_for(store, token)
        if session is not None and (tenant := _stored_tenant(session, workspace)):
            return _tenant_header_values(tenant)
        try:
            selected = run_sync(_discover_workspace(config, token, workspace))
        except (OidcError, CoordinationError):
            raise
        except Exception:
            raise OidcError(
                "OIDC workspace discovery failed; retry or set VIBE_CLI_OIDC_ORGANIZATION_ID"
            ) from None
        tenant = OidcTenant(
            selection=workspace,
            organization_id=selected.organization_id,
            workspace_id=selected.id,
        )
        if session is not None:
            try:
                # save_tenant shares the native coordinator with login/refresh;
                # it reloads and checks token revision before rewriting a bundle.
                store.save_tenant(session, tenant)
            except OidcStorageError:
                # Requests still carry the tenant; persistent caches stay
                # bypassed until a later discovery saves it.
                pass
        return _tenant_header_values(tenant)


def _session_for(store: OidcCredentialStore, token: str) -> OidcTokens | None:
    """The stored session ``token`` belongs to, or ``None`` when the slot holds
    another one (a sign-in or sign-out since the token was read).
    """
    try:
        tokens = store._load()
    except OidcStorageError:
        return None
    if tokens is None or tokens.id_token.get_secret_value() != token:
        return None
    return tokens


def _tenant_header_values(tenant: OidcTenant) -> dict[str, str]:
    return {
        "x-external-organization-id": tenant.organization_id,
        "x-external-workspace-id": tenant.workspace_id,
    }


async def _discover_workspace(
    config: AuthConfig, token: str, selected_id: str
) -> _Workspace:
    # Producer: dashboard/users/public/routes.py:list_workspaces populates
    # organization_id from each workspace's organization. The list is ordered.
    async with VibeAsyncHTTPClient(
        auth_config=config,
        verify=build_ssl_context(), follow_redirects=False, timeout=20
    ) as client:
        offset = 0
        while True:
            response = await client.get(
                f"{config.audience.rstrip('/')}/v1/users/me/workspaces",
                params={"limit": _WORKSPACE_PAGE_SIZE, "offset": offset},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            page = _Workspaces.model_validate(response.json())
            for workspace in page.workspaces:
                if not selected_id or workspace.id == selected_id:
                    return workspace
            if len(page.workspaces) < _WORKSPACE_PAGE_SIZE:
                raise OidcError(
                    "No accessible OIDC workspace found; check membership or workspace override"
                )
            offset += _WORKSPACE_PAGE_SIZE


def _resolve_id_token(service: OidcService) -> str:
    # Sync credential callers and HTTP hooks share refresh-token rotation.
    with _refresh_lock:
        try:
            tokens = run_sync(service.access_tokens(require_id_token=True))
            token = tokens.id_token.get_secret_value()
            if not token or not _id_token_is_fresh(tokens.id_token_expires_at):
                raise OidcError("OIDC re-login required: ID token was not renewed")
            return token
        except (OidcError, AuthError):
            raise
        except Exception:
            raise OidcError(
                "OIDC credential resolution failed; explicitly re-login"
            ) from None


def _id_token_is_fresh(expires_at: float | None) -> bool:
    return (
        expires_at is not None
        and math.isfinite(expires_at)
        and expires_at > time.time() + 30
    )
