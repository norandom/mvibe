"""Read-only native Vibe authentication; native Vibe owns credential renewal.

Storage origin establishes neither credential issuance nor billing entitlement.
No environment key, legacy service, migration, browser or OIDC flow is used.
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import secrets
import subprocess
import sys
from typing import Any

import httpx2
import keyring

from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import AuthError, AuthFailure, AuthSnapshot, LoginHandle
from mvibe_auth.http import get_adapter_user_agent

_REVISION_KEY = secrets.token_bytes(32)
NATIVE_SIGN_IN_MESSAGE = 'Sign in through normal Vibe, then retry existing Vibe authentication.'


def _read_native_credential() -> str | None:
    if sys.platform == 'darwin':
        result = subprocess.run(['/usr/bin/security', 'find-generic-password',
            '-s', 'ai.mistral.vibe', '-a', 'MISTRAL_API_KEY', '-w'],
            capture_output=True, text=True, timeout=60, check=False)
        if result.returncode == 44:
            return None
        if result.returncode:
            raise RuntimeError('Native credential storage is unavailable.')
        return result.stdout.removesuffix('\n')
    return keyring.get_password('ai.mistral.vibe', 'MISTRAL_API_KEY')


async def _drain(task: asyncio.Task[Any]) -> Any:
    # A native worker cannot be cancelled. Keep ownership until it returns,
    # including when the caller or a second close request is cancelled.
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


class ExistingVibeSession:
    """Fresh native keyring reads and bounded request snapshots, without writes."""

    def __init__(self) -> None:
        # Explicit source selection does not import experimental OIDC settings.
        self._config = AuthConfig(enabled=True)
        self._closed = False
        self._operations: set[asyncio.Task[Any]] = set()
        self._close_task: asyncio.Task[None] | None = None
        self._read_lock = asyncio.Lock()

    @property
    def config(self) -> AuthConfig:
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
        except AuthError:
            raise
        except Exception:
            raise AuthError(AuthFailure('transient', retryable=True)) from None
        finally:
            self._operations.discard(task)

    async def _read(self) -> str | None:
        async with self._read_lock:
            worker = asyncio.create_task(asyncio.to_thread(_read_native_credential))
            try:
                credential = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # Discard any worker result; cancellation must never return auth.
                try:
                    await _drain(worker)
                except Exception:
                    pass
                raise
            if credential is not None and (not isinstance(credential, str) or
                    len(credential) > 16384 or any(not 33 <= ord(c) <= 126 for c in credential)):
                raise AuthError(AuthFailure('invalid'))
            return credential or None

    async def status(self) -> dict[str, bool]:
        async def read() -> dict[str, bool]:
            present = await self._read() is not None
            return {'signed_in': present, 'config_match': present}
        return await self._run(read())

    async def resolve(self, target_url: str) -> AuthSnapshot:
        async def resolve() -> AuthSnapshot:
            try:
                target = httpx2.URL(target_url)
            except (ValueError, TypeError, httpx2.InvalidURL):
                raise AuthError(AuthFailure('invalid')) from None
            if (target.scheme != 'https' or target.host != 'api.mistral.ai'
                    or target.port not in (None, 443) or target.userinfo or target.fragment
                    or target.path not in ('/v1/models', '/v1/chat/completions', '/v1/embeddings')):
                raise AuthError(AuthFailure('invalid'))
            credential = await self._read()
            if credential is None:
                raise AuthError(AuthFailure('missing'))
            revision = hmac.new(_REVISION_KEY, credential.encode(), sha256).hexdigest()
            return AuthSnapshot({'Authorization': f'Bearer {credential}',
                'User-Agent': get_adapter_user_agent()},
                datetime.now(timezone.utc) + timedelta(seconds=60), revision,
                target_url, kind='vibe_credential')
        return await self._run(resolve())

    async def start_login(self, force: bool = False) -> LoginHandle:
        # Protocol v1 remains stable, but this source never initiates login.
        raise AuthError(AuthFailure('missing'))

    async def wait_login(self, attempt_id: str) -> Any:
        raise AuthError(AuthFailure('invalid'))

    async def cancel_login(self, attempt_id: str) -> None:
        raise AuthError(AuthFailure('invalid'))

    async def _close(self) -> None:
        tasks = tuple(self._operations)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        await _drain(self._close_task)
