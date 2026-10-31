"""Reusable Vibe OIDC session. Presentation of login URLs belongs to the caller."""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import secrets
from typing import Any, TypedDict

from mvibe_auth._auth_config import AuthConfig as AuthConfig
from mvibe_auth._auth_contracts import (
    AuthError as AuthError, AuthFailure as AuthFailure,
    AuthFailureCode as AuthFailureCode, AuthSnapshot as AuthSnapshot,
    LoginCompleted as LoginCompleted, LoginHandle as LoginHandle,
    LoginOutcome as LoginOutcome, LoginReusedCurrent as LoginReusedCurrent,
)
from mvibe_auth._oidc_config import OidcError
from mvibe_auth._oidc_credentials import OidcTokens
from mvibe_auth._oidc_runtime import OidcRuntime
from mvibe_auth._oidc_service import OidcAttempt, OidcService

_REVISION_KEY = secrets.token_bytes(32)


class AuthStatus(TypedDict):
    signed_in: bool
    config_match: bool


def _failure(error: Exception) -> AuthFailure:
    if isinstance(error, AuthError):
        return error.failure
    if isinstance(error, OidcError):
        detail = str(error)
        if detail == 'OIDC re-login required: no stored credentials':
            return AuthFailure('missing')
        if detail == 'OIDC re-login required: credential configuration changed' or detail.startswith('OIDC destination'):
            return AuthFailure('invalid')
        if detail.startswith('OIDC re-login required:') or detail == 'OIDC refresh changed the authenticated identity':
            return AuthFailure('rejected')
    return AuthFailure('transient', retryable=True)


class AuthSession:
    """Own captured configuration and authentication activity.

    start_login returns a URL after its callback is listening. Stored matching
    identity yields an empty URL and reused_current outcome. Consumers present
    nonempty URLs themselves. status only reads storage; resolve refreshes as
    needed but never starts browser login. close drains owned work and callbacks.
    """

    def __init__(self, config: AuthConfig | None = None) -> None:
        self._config = config if config is not None else AuthConfig.from_env()
        self._service = OidcService.from_config(self.config)
        self._runtime = OidcRuntime(self.config, service=self._service)
        self._operations: set[asyncio.Task[Any]] = set()
        self._attempts: dict[str, OidcAttempt | LoginOutcome] = {}
        self._login_lock = asyncio.Lock()
        self._resolution_lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def config(self) -> AuthConfig:
        """Immutable configuration captured when this session was created."""
        return self._config

    async def _run[T](self, operation: Coroutine[Any, Any, T]) -> T:
        if self._closed:
            operation.close()
            raise AuthError(AuthFailure('cancelled'))
        task = asyncio.create_task(operation)
        self._operations.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise AuthError(_failure(error)) from None
        finally:
            self._operations.discard(task)

    async def status(self) -> AuthStatus:
        """Read presence/configuration match without refresh or discovery."""
        async def read() -> AuthStatus:
            self.config.require_enabled()
            tokens = await self._service.store.reload()
            return {'signed_in': tokens is not None,
                    'config_match': tokens is not None and self._matches_config(tokens)}
        return await self._run(read())

    def _matches_config(self, tokens: OidcTokens) -> bool:
        return (tokens.issuer, tokens.client_id, tokens.audience) == (
            self.config.issuer, self.config.client_id, self.config.audience)

    async def start_login(self, force: bool = False) -> LoginHandle:
        async def start() -> LoginHandle:
            async with self._login_lock:
                self.config.require_enabled()
                if not force:
                    tokens = await self._service.store.reload()
                    if tokens is not None and self._matches_config(tokens):
                        attempt_id = secrets.token_urlsafe(24)
                        self._attempts[attempt_id] = LoginReusedCurrent()
                        return LoginHandle(attempt_id, '', datetime.now(timezone.utc))
                attempt = await self._service.start()
                self._attempts[attempt.attempt_id] = attempt
                try:
                    await attempt.ready.wait()
                except asyncio.CancelledError:
                    await self._service.cancel(attempt.attempt_id)
                    raise
                if not attempt.authorization_url:
                    outcome = await self._wait(attempt.attempt_id)
                    raise AuthError(outcome if isinstance(outcome, AuthFailure)
                                    else AuthFailure('transient', retryable=True))
                return LoginHandle(attempt.attempt_id, attempt.authorization_url,
                                   datetime.fromtimestamp(attempt.started_timestamp + 300, timezone.utc))
        return await self._run(start())

    async def _wait(self, attempt_id: str) -> LoginOutcome:
        entry = self._attempts.get(attempt_id)
        if entry is None:
            raise AuthError(AuthFailure('invalid'))
        if not isinstance(entry, OidcAttempt):
            return entry
        if entry.task is not None:
            await asyncio.shield(asyncio.gather(entry.task, return_exceptions=True))
        outcome = entry.outcome or AuthFailure('transient', retryable=True)
        self._attempts[attempt_id] = outcome
        return outcome

    async def wait_login(self, attempt_id: str) -> LoginOutcome:
        return await self._run(self._wait(attempt_id))

    async def cancel_login(self, attempt_id: str) -> None:
        async def cancel() -> None:
            entry = self._attempts.get(attempt_id)
            if entry is None:
                raise AuthError(AuthFailure('invalid'))
            if isinstance(entry, OidcAttempt):
                await self._service.cancel(attempt_id)
        await self._run(cancel())

    async def resolve(self, target_url: str) -> AuthSnapshot:
        async def resolve() -> AuthSnapshot:
            async with self._resolution_lock:
                headers, tokens = await self._runtime.material(target_url)
                revision = hmac.new(_REVISION_KEY,
                    tokens.model_dump_json(exclude={'tenant'}).encode(), sha256).hexdigest()
                assert tokens.id_token_expires_at is not None
                return AuthSnapshot(headers, datetime.fromtimestamp(
                    min(tokens.expires_at, tokens.id_token_expires_at), timezone.utc),
                    revision, target_url)
        return await self._run(resolve())

    async def _drain(self) -> None:
        tasks = tuple(self._operations)
        for task in tasks:
            task.cancel()
        # Persistence owns its terminal outcome and may finish before cancel.
        await self._service.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        """Idempotently cancel and drain owned work, even on cancellation."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._drain())
        await self._service.store._wait_for_worker(self._close_task)


__all__ = ['AuthConfig', 'AuthError', 'AuthFailure', 'AuthFailureCode', 'AuthSnapshot',
           'AuthSession', 'AuthStatus', 'LoginCompleted', 'LoginHandle', 'LoginOutcome',
           'LoginReusedCurrent']
