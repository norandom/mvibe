"""Credential mutation contracts with synthetic keyring and encrypted local storage."""
from __future__ import annotations

import asyncio
from threading import Event

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
import pytest

from mvibe_auth import _oidc_credentials as credentials
from mvibe_auth._oidc_credentials import (
    ABSENT, OidcCredentialStore, OidcStorageError, OidcTenant, OidcTokens,
)


def tokens(**changes):
    return OidcTokens(
        issuer="https://synthetic.invalid", client_id="synthetic-client",
        audience="https://inference.invalid", subject="synthetic-person",
        access_token="synthetic-access", refresh_token="synthetic-refresh",
        id_token="synthetic-id", expires_at=1700000900,
        nonce="synthetic-nonce", id_token_expires_at=1700000900,
    ).model_copy(update=changes)


def tenant(selection=""):
    return OidcTenant(selection=selection, organization_id="synthetic-org", workspace_id="synthetic-workspace")


@pytest.fixture
def slot(monkeypatch):
    from cryptography.fernet import Fernet
    original_write = OidcCredentialStore._write_encrypted

    class Slot:
        legacy = None
        key = None
        writes = 0
        deletes = 0
        read_error = None
        write_error = None
        delete_error = None

        @property
        def raw(self):
            path = credentials.credential_path()
            if path.exists():
                raw = Fernet(self.key.encode()).decrypt(path.read_bytes()).decode()
                return None if raw == '{"version":1,"signed_out":true}' else raw
            return self.legacy

        @raw.setter
        def raw(self, raw):
            path = credentials.credential_path()
            if path.exists():
                path.write_bytes(Fernet(self.key.encode()).encrypt((raw or '{"version":1,"signed_out":true}').encode()))
            else:
                self.legacy = raw

        def read(self, service, entry):
            assert service == "ai.mistral.vibe.oidc"
            if self.read_error:
                raise self.read_error
            return self.legacy if entry == credentials._KEYRING_ENTRY else self.key

        def key_write(self, service, entry, raw):
            assert entry == credentials._ENCRYPTION_KEY_ENTRY
            self.key = raw

        def write(self, raw):
            if self.write_error:
                raise self.write_error
            if self.delete_error and raw == b'{"version":1,"signed_out":true}':
                raise self.delete_error
            original_write(OidcCredentialStore(), raw)
            self.writes += 1

        def external(self, value):
            raw = value.model_dump_json() if value is not None else None
            path = credentials.credential_path()
            if path.exists():
                # Simulate a second participating library process, not Vibe.
                encrypted = Fernet(self.key.encode()).encrypt((raw or '{"version":1,"signed_out":true}').encode())
                path.write_bytes(encrypted)
            else:
                self.legacy = raw

    value = Slot()
    monkeypatch.setattr(keyring, "get_password", value.read)
    monkeypatch.setattr(keyring, "set_password", value.key_write)
    monkeypatch.setattr(keyring, "delete_password", lambda *args: pytest.fail("legacy slot mutated"))

    def write(self, raw):
        try:
            value.write(raw)
        except KeyringError:
            raise OidcStorageError("Could not save OIDC credentials to storage") from None
    monkeypatch.setattr(OidcCredentialStore, "_write_encrypted", write)
    return value


@pytest.mark.anyio
async def test_expected_absent_login_commits_only_missing_slot(slot):
    store = OidcCredentialStore()
    assert await store.reload() is None
    assert await store.save_if_current(tokens(), expected=ABSENT)
    newer = tokens(subject="newer-person")
    slot.external(newer)
    assert not await store.save_if_current(tokens(), expected=ABSENT)
    assert await store.reload() == newer
    assert slot.writes == 1


@pytest.mark.anyio
@pytest.mark.parametrize("field,value", [
    ("issuer", "https://changed.invalid"), ("client_id", "changed-client"),
    ("audience", "https://changed-audience.invalid"), ("subject", "changed-person"),
    ("access_token", "changed-access"), ("refresh_token", "changed-refresh"),
    ("id_token", "changed-id"), ("expires_at", 1700001000),
    ("nonce", "changed-nonce"), ("id_token_expires_at", 1700001000),
])
async def test_changed_revision_blocks_refresh_tenant_and_clear(slot, field, value):
    store = OidcCredentialStore()
    original = tokens()
    slot.external(original)
    assert await store.load() == original  # Seed a stale process cache.
    newer = OidcTokens.model_validate(original.model_dump() | {field: value})
    slot.external(newer)
    assert not await store.save_if_current(tokens(), expected=original)
    assert not store.save_tenant(original, tenant())
    assert not await store.clear_if_current(expected=original)
    assert await store.reload() == newer
    assert (slot.writes, slot.deletes) == (0, 0)


@pytest.mark.anyio
async def test_tenant_only_change_survives_refresh_commit(slot):
    original = tokens(tenant=tenant("old"))
    current = original.model_copy(update={"tenant": tenant("concurrent")})
    slot.external(current)
    store = OidcCredentialStore()
    replacement = OidcTokens.model_validate(
        original.model_dump() | {"access_token": "replacement-access", "refresh_token": "replacement-refresh",
                                 "id_token": "replacement-id", "expires_at": 1700002000, "tenant": tenant("candidate")}
    )
    assert await store.save_if_current(replacement, expected=original)
    assert await store.reload() == replacement.model_copy(update={"tenant": current.tenant})
    assert store.save_tenant(replacement, tenant("latest"))
    assert (await store.reload()).tenant == tenant("latest")


@pytest.mark.anyio
async def test_changed_identity_does_not_inherit_prior_tenant(slot):
    original = tokens(tenant=tenant())
    slot.external(original)
    replacement = tokens(subject="new-person")
    store = OidcCredentialStore()
    assert await store.save_if_current(replacement, expected=original)
    assert (await store.reload()).tenant is None


@pytest.mark.anyio
async def test_removed_slot_does_not_resurrect_stale_refresh_or_tenant(slot):
    store = OidcCredentialStore()
    original = tokens()
    assert not await store.save_if_current(original, expected=original)
    assert not store.save_tenant(original, tenant())
    assert not await store.clear_if_current(expected=original)
    assert slot.raw is None and slot.writes == 0


@pytest.mark.anyio
async def test_conditional_clear_and_compatibility_wrappers(slot):
    store = OidcCredentialStore()
    assert await store.clear_if_current(expected=ABSENT)
    assert await store.save(tokens(), expected=ABSENT)
    assert not await store.clear(expected=ABSENT)
    assert await store.clear(expected=tokens())
    assert await store.save(tokens())
    assert await store.clear()
    assert slot.raw is None


@pytest.mark.anyio
async def test_none_is_not_an_implicit_absent_snapshot(slot):
    store = OidcCredentialStore()
    with pytest.raises(TypeError, match="snapshot or ABSENT"):
        await store.save_if_current(tokens(), expected=None)
    assert slot.writes == 0


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["save", "clear"])
async def test_legacy_wrappers_detect_update_after_captured_snapshot(slot, monkeypatch, method):
    store = OidcCredentialStore()
    original, newer = tokens(), tokens(subject="newer-person")
    slot.external(original)
    reload_original = store.reload

    async def reload_then_external_update():
        snapshot = await reload_original()
        slot.external(newer)
        return snapshot

    monkeypatch.setattr(store, "reload", reload_then_external_update)
    result = await store.save(tokens()) if method == "save" else await store.clear()
    assert not result
    assert await reload_original() == newer
    assert (slot.writes, slot.deletes) == (0, 0)


@pytest.mark.anyio
async def test_disabled_storage_never_claims_successful_mutation(slot, monkeypatch):
    monkeypatch.setenv("VIBE_TEST_DISABLE_KEYRING", "1")
    store = OidcCredentialStore()
    for operation in [lambda: store.save_if_current(tokens(), expected=ABSENT),
                      lambda: store.clear_if_current(expected=ABSENT)]:
        with pytest.raises(OidcStorageError, match="disabled"):
            await operation()
    with pytest.raises(OidcStorageError, match="disabled"):
        store.save_tenant(tokens(), tenant())
    assert (slot.writes, slot.deletes) == (0, 0)


@pytest.mark.anyio
@pytest.mark.parametrize("unreadable", ["invalid-json synthetic-access", "{}", KeyringError("synthetic-access synthetic-refresh synthetic-id")])
async def test_unreadable_is_never_absent_or_mutated(slot, unreadable):
    if isinstance(unreadable, Exception):
        slot.read_error = unreadable
    else:
        slot.raw = unreadable
    store = OidcCredentialStore()
    operations = [store.reload, lambda: store.save_if_current(tokens(), expected=ABSENT),
                  lambda: store.clear_if_current(expected=ABSENT), lambda: store.save(tokens()), store.clear]
    for operation in operations:
        with pytest.raises(OidcStorageError) as error:
            await operation()
        assert "synthetic-access" not in str(error.value)
        assert error.value.__cause__ is None
    with pytest.raises(OidcStorageError):
        store.save_tenant(tokens(), tenant())
    assert (slot.writes, slot.deletes) == (0, 0)


@pytest.mark.anyio
async def test_storage_write_and_delete_failures_are_explicit_and_sanitized(slot):
    store = OidcCredentialStore()
    slot.write_error = KeyringError("synthetic-access synthetic-refresh synthetic-id")
    with pytest.raises(OidcStorageError) as error:
        await store.save_if_current(tokens(), expected=ABSENT)
    assert "synthetic-" not in str(error.value)
    slot.external(tokens())
    slot.delete_error = PasswordDeleteError("synthetic-access")
    with pytest.raises(OidcStorageError) as error:
        await store.clear_if_current(expected=tokens())
    assert "synthetic-access" not in str(error.value)
    assert await store.reload() == tokens()


@pytest.mark.anyio
async def test_tenant_write_failure_preserves_usable_request_values(slot):
    value, selected = tokens(), tenant()
    slot.external(value)
    slot.write_error = KeyringError("synthetic-access")
    store = OidcCredentialStore()
    with pytest.raises(OidcStorageError, match="Could not save"):
        store.save_tenant(value, selected)
    assert await store.reload() == value
    assert value.access_token.get_secret_value() == "synthetic-access"
    assert selected.workspace_id == "synthetic-workspace"


@pytest.mark.anyio
async def test_storage_roundtrip_and_displays_keep_existing_schema(slot):
    value = tokens(tenant=tenant())
    store = OidcCredentialStore()
    assert await store.save_if_current(value, expected=ABSENT)
    assert await store.reload() == value
    assert OidcTokens.model_validate_json(slot.raw) == value
    assert '"version":1' in slot.raw
    for secret in ("synthetic-access", "synthetic-refresh", "synthetic-id"):
        assert secret not in repr(value) and secret not in str(value)
        assert secret in slot.raw  # Secrets remain available only for storage serialization.


@pytest.mark.anyio
@pytest.mark.parametrize("complete", [False, True])
async def test_cancel_waits_for_mutating_worker_and_reports_commit_when_requested(slot, monkeypatch, owned_workers, complete):
    store = OidcCredentialStore()
    started, release = Event(), Event()
    owned_workers.own(release.set)
    original_write = slot.write

    def blocked_write(*args):
        started.set()
        assert release.wait(5), "test failed to release persistence worker"
        original_write(*args)

    monkeypatch.setattr(slot, "write", blocked_write)
    task = asyncio.create_task(store.save(tokens(), expected=ABSENT, complete_on_cancel=complete))
    try:
        async with asyncio.timeout(3):
            while not started.is_set():
                await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        if complete:
            assert await task
        else:
            with pytest.raises(asyncio.CancelledError):
                await task
    assert await store.reload() == tokens()
