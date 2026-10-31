from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import tempfile
from enum import Enum
from threading import RLock
from typing import Literal, cast

import keyring
from keyring.errors import KeyringError
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_serializer
from mvibe_auth._coordination import MutationCoordinator

_KEYRING_SERVICE = "ai.mistral.vibe.oidc"
_KEYRING_ENTRY = "cli-oidc:v1:tokens"
_ENCRYPTION_KEY_ENTRY = "mvibe-auth:v1:fernet-key"
_DISABLE_KEYRING_ENV_VAR = "VIBE_TEST_DISABLE_KEYRING"
# One credential slot is shared by service instances and worker event loops.
_slot_lock = RLock()
_UNLOADED = object()
# Process-wide copy of the slot, so hot paths do not read the keyring each time.
_cached: OidcTokens | None | object = _UNLOADED


def credential_path() -> Path:
    """Per-user library data, independent of project, CLI and OpenCode profiles."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "mvibe-auth" / "credentials.v1.enc"


class OidcTenant(BaseModel):
    """The organization and workspace the signed-in identity acts in.

    Not secret. ``selection`` is the workspace override it was discovered for
    (``""`` for the default), so a changed override discovers again.
    """

    model_config = ConfigDict(extra="forbid")

    selection: str
    organization_id: str
    workspace_id: str


class OidcTokens(BaseModel):
    """Versioned OIDC credentials; JSON serialization exposes secrets for storage."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    issuer: str
    client_id: str
    audience: str
    subject: str
    access_token: SecretStr
    refresh_token: SecretStr | None
    id_token: SecretStr
    expires_at: float
    nonce: str | None = None
    id_token_expires_at: float | None = None
    tenant: OidcTenant | None = None

    @field_serializer("access_token", "refresh_token", "id_token", when_used="json")
    def _serialize_secret(self, value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value is not None else None

    def same_identity(self, other: OidcTokens) -> bool:
        return (self.issuer, self.client_id, self.audience, self.subject) == (
            other.issuer,
            other.client_id,
            other.audience,
            other.subject,
        )


class OidcStorageError(Exception):
    """OIDC persistence failed without exposing backend details or stored secrets."""


class _SnapshotMarker(Enum):
    ABSENT = "absent"
    CAPTURE_CURRENT = "capture_current"


# Only a successful authoritative read returning None can produce this snapshot.
ABSENT = _SnapshotMarker.ABSENT


class OidcCredentialStore:
    """Conditionally mutate the shared encrypted store under native coordination.

    Legacy Vibe credentials are imported only while the new store is absent.
    Installed Vibe does not participate in subsequent library writes.
    """

    def __init__(self, *, coordinator: MutationCoordinator | None = None) -> None:
        self.coordinator = coordinator if coordinator is not None else MutationCoordinator()

    async def load(self) -> OidcTokens | None:
        return await self._wait_for_worker(asyncio.create_task(self.coordinator.worker(self._load)))

    async def reload(self) -> OidcTokens | None:
        """Read authoritative storage before a refresh another process may have done."""
        return await self._wait_for_worker(asyncio.create_task(self.coordinator.worker(self._reload)))

    async def save(
        self, tokens: OidcTokens, *, complete_on_cancel: bool = False,
        expected: OidcTokens | _SnapshotMarker = _SnapshotMarker.CAPTURE_CURRENT,
    ) -> bool:
        """Save against a snapshot; legacy callers capture one at invocation.

        Long-running operations must supply their own pre-operation snapshot.
        False means storage changed; callers must not report a persisted login.
        """
        tokens = tokens.model_copy(deep=True)
        async with self.coordinator.hold_async():
            expected = await self._capture_expected(expected)
            return await self._wait_for_worker(
                asyncio.create_task(self.coordinator.worker(self._save_if_current, tokens, expected)),
                complete_on_cancel=complete_on_cancel,
            )

    async def save_if_current(
        self, tokens: OidcTokens, *, expected: OidcTokens | _SnapshotMarker
    ) -> bool:
        return await self.save(tokens, expected=expected)

    async def clear(
        self, *, expected: OidcTokens | _SnapshotMarker = _SnapshotMarker.CAPTURE_CURRENT
    ) -> bool:
        async with self.coordinator.hold_async():
            expected = await self._capture_expected(expected)
            return await self._wait_for_worker(
                asyncio.create_task(self.coordinator.worker(self._clear_if_current, expected))
            )

    async def clear_if_current(self, *, expected: OidcTokens | _SnapshotMarker) -> bool:
        return await self.clear(expected=expected)

    async def _capture_expected(
        self, expected: OidcTokens | _SnapshotMarker
    ) -> OidcTokens | _SnapshotMarker:
        if expected is _SnapshotMarker.CAPTURE_CURRENT:
            current = await self.reload()
            return current.model_copy(deep=True) if current is not None else ABSENT
        if isinstance(expected, OidcTokens):
            return expected.model_copy(deep=True)
        if expected is ABSENT:
            return expected
        raise TypeError("Expected an OIDC credential snapshot or ABSENT")

    def save_tenant(self, tokens: OidcTokens, tenant: OidcTenant) -> bool:
        """Record ``tenant`` on the stored session, if it is still ``tokens``'s
        token revision: sign-out, sign-in and refresh are not overwritten.
        """
        with self.coordinator.hold(), _slot_lock:
            current = self._mutation_current()
            if current is None or not self._matches(current, tokens):
                return False
            self._save(current.model_copy(update={"tenant": tenant}))
            return True

    @staticmethod
    def _matches(current: OidcTokens | None, expected: OidcTokens | _SnapshotMarker) -> bool:
        if expected is ABSENT:
            return current is None
        return (
            current is not None
            and isinstance(expected, OidcTokens)
            and current.model_copy(update={"tenant": None})
            == expected.model_copy(update={"tenant": None})
        )

    def _save_if_current(self, tokens: OidcTokens, expected: OidcTokens | _SnapshotMarker) -> bool:
        with self.coordinator.hold(), _slot_lock:
            current = self._mutation_current()
            # The tenant is not token material: a tenant recorded during the
            # refresh is kept rather than failing it.
            if not self._matches(current, expected):
                return False
            if current is not None and current.same_identity(tokens):
                tokens = tokens.model_copy(update={"tenant": current.tenant})
            self._save(tokens)
            return True

    def _clear_if_current(self, expected: OidcTokens | _SnapshotMarker) -> bool:
        with self.coordinator.hold(), _slot_lock:
            current = self._mutation_current()
            if not self._matches(current, expected):
                return False
            self._clear()
            return True

    def _mutation_current(self) -> OidcTokens | None:
        if os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1":
            raise OidcStorageError("OIDC credential storage is disabled")
        return self._reload()

    async def _wait_for_worker[T](
        self, task: asyncio.Task[T], *, complete_on_cancel: bool = False
    ) -> T:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Keep the caller's lock held until the worker can no longer mutate storage.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if complete_on_cancel and not task.cancelled():
                # Persistence is the commit point: report the finished write outcome.
                return task.result()
            if not task.cancelled():
                task.exception()
            raise

    def _load(self) -> OidcTokens | None:
        if os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1":
            return None
        with _slot_lock:
            if _cached is not _UNLOADED:
                return cast(OidcTokens | None, _cached)
            tokens = self._read()
            _set_cached(tokens)
            return tokens

    def _reload(self) -> OidcTokens | None:
        with _slot_lock:
            _set_cached(_UNLOADED)
            return self._load()

    def _read(self) -> OidcTokens | None:
        try:
            try:
                encrypted = credential_path().read_bytes()
            except FileNotFoundError:
                raw = keyring.get_password(_KEYRING_SERVICE, _KEYRING_ENTRY)
                return OidcTokens.model_validate_json(raw) if raw is not None else None
            key = keyring.get_password(_KEYRING_SERVICE, _ENCRYPTION_KEY_ENTRY)
            if key is None:
                raise ValueError("missing encryption key")
            raw = Fernet(key.encode("ascii")).decrypt(encrypted)
            if raw == b'{"version":1,"signed_out":true}':
                return None
            return OidcTokens.model_validate_json(raw)
        except (OSError, KeyringError, ImportError, ValueError, InvalidToken):
            # Never fall back to legacy credentials for an existing unreadable file.
            raise OidcStorageError("Could not read OIDC credentials from storage") from None

    def _write_encrypted(self, raw: bytes) -> None:
        # Called only under coordinator -> slot lock. Key creation and first file
        # commit therefore share the same cross-process serialization as updates.
        temporary = None
        try:
            path = credential_path()
            key = keyring.get_password(_KEYRING_SERVICE, _ENCRYPTION_KEY_ENTRY)
            if key is None:
                # An existing file must never be overwritten using a replacement key.
                if path.exists():
                    raise ValueError("missing encryption key")
                key = Fernet.generate_key().decode("ascii")
                keyring.set_password(_KEYRING_SERVICE, _ENCRYPTION_KEY_ENTRY, key)
            encrypted = Fernet(key.encode("ascii")).encrypt(raw)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, temporary = tempfile.mkstemp(prefix=".credentials-", suffix=".tmp", dir=path.parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        except (OSError, KeyringError, ImportError, ValueError):
            raise OidcStorageError("Could not save OIDC credentials to storage") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _save(self, tokens: OidcTokens) -> None:
        if os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1":
            raise OidcStorageError("OIDC credential storage is disabled")
        _set_cached(_UNLOADED)
        self._write_encrypted(tokens.model_dump_json().encode("utf-8"))
        _set_cached(tokens)

    def _clear(self) -> None:
        if os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1":
            raise OidcStorageError("OIDC credential storage is disabled")
        _set_cached(_UNLOADED)
        # Keep an authenticated tombstone: deleting the file would resurrect Vibe.
        self._write_encrypted(b'{"version":1,"signed_out":true}')
        _set_cached(None)



def _set_cached(value: OidcTokens | None | object) -> None:
    global _cached
    _cached = value


def clear_oidc_token_cache() -> None:
    """Forget the in-memory copy; the next read goes to authoritative storage."""
    with _slot_lock:
        _set_cached(_UNLOADED)


__all__ = [
    "ABSENT",
    "OidcCredentialStore",
    "OidcStorageError",
    "OidcTenant",
    "OidcTokens",
    "clear_oidc_token_cache",
]
