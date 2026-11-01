"""Optional account-derived attribution for one adapter/auth-session lifetime.

The credential-specific endpoints and plan labels follow Vibe 3.0.4
``vibe/setup/auth/whoami.py`` (see LICENSE.vibe). No account response can change
credential destinations. Nothing from the response or request is logged.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
import json
import time
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field

from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import AuthSnapshot
from mvibe_auth._oidc_config import _CLI_REGISTRATIONS
from mvibe_auth.http import VibeAsyncHTTPClient, build_ssl_context

ACCOUNT_TIMEOUT_SECONDS = 3.0
MAX_ACCOUNT_BYTES = 65536
_CACHE_TTL_SECONDS = 300.0
_FAILURE_TTL_SECONDS = 30.0
_MAX_ENTRIES = 32
_TENANT_HEADERS = ('x-external-organization-id', 'x-external-workspace-id')
type _Key = tuple[str, str, str, str]


class _AccountContext(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True, hide_input_in_errors=True)
    plan_type: str = Field(max_length=64)
    plan_name: str = Field(max_length=256)


def _native_user_plan(payload: object) -> str | None:
    account = _AccountContext.model_validate(payload)
    kind, name = account.plan_type.strip().lower(), account.plan_name.strip().upper()
    if kind == 'chat':
        return {'FREE': 'Free', 'INDIVIDUAL': 'Pro', 'EDU': 'Student', 'TEAM': 'Team'}.get(name)
    if kind == 'mistral_code':
        return {'F': 'Free Codestral', 'E': 'Code Enterprise'}.get(name)
    if kind == 'api' and name:
        return 'Free API' if 'FREE' in name else 'PAYG API'
    return None


def _reject_constant(value: str) -> None:
    raise ValueError('Invalid JSON constant.')


class AccountPlanResolver:
    """Bounded, memory-only cache; concurrent reads share one cancellable lookup.

    Configuration and the resolved authentication snapshot come from the same owner.
    Transport injection is Python-only and never selectable by wire input.
    Unknown registrations have no account destination and perform no HTTP.
    """

    def __init__(self, config: AuthConfig, *, transport: httpx2.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        registration = _CLI_REGISTRATIONS.get(getattr(config, 'issuer', None))
        self._url: str | None = None
        if (registration is not None and getattr(config, 'enabled', False)
                and getattr(config, 'client_id', None) == registration.client_id):
            # Select only the console origin already authorized by registration.
            # Never derive a destination from audience, caller or response URLs.
            self._url = next((origin + '/api/vibe/account-context'
                for origin in registration.web_origins
                if urlsplit(origin).hostname and urlsplit(origin).hostname.startswith('console.')), None)
        self._transport = transport
        self._client: VibeAsyncHTTPClient | None = None
        self._clock = clock
        self._cache: OrderedDict[_Key, tuple[float, str | None]] = OrderedDict()
        self._inflight: dict[_Key, asyncio.Task[str | None]] = {}
        self._waiters: dict[_Key, int] = {}
        self._closed = False

    async def resolve(self, snapshot: AuthSnapshot) -> str | None:
        if self._closed or self._url is None:
            return None
        headers = {name.lower(): value for name, value in snapshot.headers.items()}
        authorization = headers.get('authorization', '')
        lifetime = (snapshot.expires_at - datetime.now(timezone.utc)).total_seconds()
        if snapshot.kind not in ('id_token', 'vibe_credential') or not authorization.startswith('Bearer ') or lifetime <= 0:
            return None
        key = (snapshot.kind, snapshot.revision, *(headers.get(name, '') for name in _TENANT_HEADERS))
        cached = self._cache.get(key)
        if cached is not None and cached[0] > self._clock():
            self._cache.move_to_end(key)
            return cached[1]
        task = self._inflight.get(key)
        if task is None:
            if len(self._inflight) >= _MAX_ENTRIES:
                return None
            names = ('authorization', 'user-agent') + (_TENANT_HEADERS if snapshot.kind == 'id_token' else ())
            outbound = {name: headers[name] for name in names
                        if name in headers}
            task = asyncio.create_task(self._lookup(key, outbound, lifetime, snapshot.kind))
            self._inflight[key] = task
        self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            # A cancelled request must not cancel a lookup still used by others.
            return await asyncio.shield(task)
        finally:
            self._waiters[key] -= 1
            if self._waiters[key] == 0:
                self._waiters.pop(key)
                self._inflight.pop(key, None)
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def _lookup(self, key: _Key, headers: dict[str, str], lifetime: float, kind: str) -> str | None:
        plan = None
        try:
            async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS):
                if self._client is None:
                    self._client = VibeAsyncHTTPClient(auth_config=self._config, transport=self._transport,
                        verify=build_ssl_context(), timeout=ACCOUNT_TIMEOUT_SECONDS, follow_redirects=False)
                url = self._url.replace('/account-context', '/whoami') if kind == 'vibe_credential' else self._url
                async with self._client.stream('GET', url, headers=headers,
                                               follow_redirects=False) as response:
                    if response.status_code == 200:
                        body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=MAX_ACCOUNT_BYTES + 1):
                            body.extend(chunk)
                            if len(body) > MAX_ACCOUNT_BYTES:
                                raise ValueError('Account response too large.')
                        plan = _native_user_plan(json.loads(body, parse_constant=_reject_constant))
        except (httpx2.HTTPError, ValueError, TypeError, RecursionError, TimeoutError):
            # Optional metadata is unavailable. Cancellation deliberately propagates.
            pass
        if not self._closed:
            ttl = _CACHE_TTL_SECONDS if plan is not None else _FAILURE_TTL_SECONDS
            self._cache[key] = (self._clock() + min(ttl, lifetime), plan)
            self._cache.move_to_end(key)
            while len(self._cache) > _MAX_ENTRIES:
                self._cache.popitem(last=False)
        return plan

    async def close(self) -> None:
        self._closed = True
        self._cache.clear()
        tasks = tuple(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
