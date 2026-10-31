"""Portable persistence contracts with a synthetic OS keyring and real encryption."""
import keyring
import pytest
from pydantic import SecretStr
from mvibe_auth import _oidc_credentials as credentials
from tests.test_credentials import tokens

@pytest.fixture
def ring(monkeypatch):
    entries = {}
    monkeypatch.setattr(keyring, 'get_password', lambda service, entry: entries.get(entry))
    monkeypatch.setattr(keyring, 'set_password', lambda service, entry, value: entries.__setitem__(entry, value))
    monkeypatch.setattr(keyring, 'delete_password', lambda *args: pytest.fail('legacy slot must not be deleted'))
    return entries

@pytest.mark.anyio
async def test_large_bundle_uses_only_short_key_and_encrypted_file(ring):
    value = tokens(access_token=SecretStr('large-secret-' * 5000))
    store = credentials.OidcCredentialStore()
    assert await store.save(value, expected=credentials.ABSENT)
    assert list(map(len, ring.values())) == [44]
    path = credentials.credential_path()
    assert b'large-secret' not in path.read_bytes()
    credentials.clear_oidc_token_cache()
    assert await credentials.OidcCredentialStore().reload() == value

@pytest.mark.anyio
async def test_legacy_migration_and_tombstone_do_not_touch_vibe(ring):
    original = tokens()
    ring[credentials._KEYRING_ENTRY] = original.model_dump_json()
    store = credentials.OidcCredentialStore()
    assert await store.reload() == original
    assert await store.clear(expected=original)
    assert ring[credentials._KEYRING_ENTRY] == original.model_dump_json()
    assert credentials.credential_path().exists()
    assert await store.reload() is None

@pytest.mark.anyio
@pytest.mark.parametrize('damage', ['corrupt', 'missing-key', 'wrong-key'])
async def test_existing_file_fails_closed_without_legacy_fallback(ring, damage):
    from cryptography.fernet import Fernet
    store = credentials.OidcCredentialStore()
    await store.save(tokens(), expected=credentials.ABSENT)
    ring[credentials._KEYRING_ENTRY] = tokens(subject='legacy').model_dump_json()
    if damage == 'corrupt': credentials.credential_path().write_bytes(b'corrupt secret')
    elif damage == 'missing-key': ring.pop(credentials._ENCRYPTION_KEY_ENTRY)
    else: ring[credentials._ENCRYPTION_KEY_ENTRY] = Fernet.generate_key().decode('ascii')
    for operation in [store.reload, lambda: store.save(tokens()), store.clear]:
        with pytest.raises(credentials.OidcStorageError): await operation()

@pytest.mark.anyio
@pytest.mark.parametrize('failure_at', ['fsync', 'replace'])
async def test_failed_atomic_commit_preserves_previous_ciphertext_and_cleans_temp(ring, monkeypatch, failure_at):
    store = credentials.OidcCredentialStore()
    original = tokens()
    assert await store.save(original, expected=credentials.ABSENT)
    path = credentials.credential_path()
    ciphertext = path.read_bytes()
    key = dict(ring)
    def fail(*args): raise OSError('synthetic-access private filesystem details')
    with monkeypatch.context() as patch:
        patch.setattr(credentials.os, failure_at, fail)
        with pytest.raises(credentials.OidcStorageError) as error:
            await store.save(tokens(subject='replacement'), expected=original)
        assert 'synthetic-access' not in str(error.value)
        assert error.value.__cause__ is None
    assert path.read_bytes() == ciphertext
    assert not list(path.parent.glob(".credentials-*.tmp"))
    assert ring == key
    assert await store.reload() == original

@pytest.mark.anyio
async def test_first_key_creation_is_serialized_between_competing_stores(ring, monkeypatch):
    import asyncio
    from threading import Event
    entered, release = Event(), Event()
    original_write = keyring.set_password
    writes = []
    def blocked_write(service, entry, value):
        writes.append(value)
        entered.set()
        assert release.wait(5)
        original_write(service, entry, value)
    monkeypatch.setattr(keyring, 'set_password', blocked_write)
    one, two = credentials.OidcCredentialStore(), credentials.OidcCredentialStore()
    first = asyncio.create_task(one.save(tokens(), expected=credentials.ABSENT))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        second = asyncio.create_task(two.save(tokens(subject='competing'), expected=credentials.ABSENT))
        await asyncio.sleep(.05)
        assert not second.done()
        assert len(writes) == 1
    finally:
        release.set()
        assert await first
        if second is not None: assert not await second
    assert len(writes) == 1
    assert await two.reload() == tokens()

@pytest.mark.anyio
async def test_clear_when_absent_records_tombstone_before_later_legacy_login(ring):
    store = credentials.OidcCredentialStore()
    assert await store.clear(expected=credentials.ABSENT)
    ring[credentials._KEYRING_ENTRY] = tokens(subject='legacy-new-login').model_dump_json()
    assert await store.reload() is None

@pytest.mark.anyio
async def test_first_save_migrates_legacy_and_future_vibe_changes_are_not_imported(ring):
    original = tokens()
    ring[credentials._KEYRING_ENTRY] = original.model_dump_json()
    store = credentials.OidcCredentialStore()
    changed = tokens(subject='library-login')
    assert await store.save(changed, expected=original)
    assert ring[credentials._KEYRING_ENTRY] == original.model_dump_json()
    ring[credentials._KEYRING_ENTRY] = tokens(subject='vibe-login').model_dump_json()
    assert await store.reload() == changed

@pytest.mark.parametrize('platform,env,expected', [
    ('win32', 'LOCALAPPDATA', 'mvibe-auth/credentials.v1.enc'),
    ('linux', 'XDG_DATA_HOME', 'mvibe-auth/credentials.v1.enc'),
    ('darwin', None, 'Library/Application Support/mvibe-auth/credentials.v1.enc'),
])
def test_data_path_is_per_user_and_independent_of_consumer_profile(monkeypatch, tmp_path, platform, env, expected):
    from pathlib import Path
    monkeypatch.setattr(credentials.sys, 'platform', platform)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    if env: monkeypatch.setenv(env, str(tmp_path))
    expected_path = tmp_path / expected
    assert credentials.credential_path() == expected_path
    monkeypatch.setenv('OPENCODE_CONFIG_DIR', str(tmp_path / 'opencode-isolation'))
    monkeypatch.chdir(tmp_path)
    assert credentials.credential_path() == expected_path

@pytest.mark.anyio
async def test_os_keyring_failure_has_no_file_or_plaintext_fallback(ring, monkeypatch):
    from keyring.errors import PasswordSetError
    def fail(*args): raise PasswordSetError('synthetic-access')
    monkeypatch.setattr(keyring, 'set_password', fail)
    with pytest.raises(credentials.OidcStorageError) as error:
        await credentials.OidcCredentialStore().save(tokens(), expected=credentials.ABSENT)
    assert 'synthetic-access' not in str(error.value)
    assert not credentials.credential_path().exists()
    assert not ring


def test_two_process_first_commit_initializes_only_one_key(tmp_path, owned_workers):
    import subprocess
    import sys
    import time
    from pathlib import Path
    from cryptography.fernet import Fernet
    children = []
    def spawn(name):
        child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('portable_store_worker.py')), str(tmp_path), name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(child)
        return child
    def cleanup():
        (tmp_path / 'release-key').touch()
        for child in children:
            if child.poll() is None: child.kill()
            child.communicate(timeout=5)
    owned_workers.own(cleanup)
    def wait(name):
        deadline = time.monotonic() + 5
        while not (tmp_path / name).exists():
            assert time.monotonic() < deadline, name
            time.sleep(.01)
    first = spawn('first')
    wait('key-entered')
    second = spawn('second')
    wait('second-started')
    time.sleep(.1)
    assert (tmp_path / 'key-writes').read_text().splitlines() == ['first']
    (tmp_path / 'release-key').touch()
    for child in [first, second]:
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, (out, err)
    assert (tmp_path / 'first-result').read_text() == 'True'
    assert (tmp_path / 'second-result').read_text() == 'False'
    assert (tmp_path / 'key-writes').read_text().splitlines() == ['first']
    decoded = Fernet((tmp_path / 'key.txt').read_bytes()).decrypt((tmp_path / 'credentials.enc').read_bytes())
    assert credentials.OidcTokens.model_validate_json(decoded).subject == 'first'
