"""Offline subprocess exercise of portable storage's first native-lock commit."""
import asyncio
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conftest  # installs pre-import default-deny external-effect guards
import keyring
from mvibe_auth import _oidc_credentials as credentials
from mvibe_auth._coordination import MutationCoordinator
from tests.test_credentials import tokens

root, name = Path(sys.argv[1]), sys.argv[2]
credentials.credential_path = lambda: root / 'credentials.enc'

def read(service, entry):
    if entry == credentials._KEYRING_ENTRY: return None
    path = root / 'key.txt'
    return path.read_text() if path.exists() else None

def write(service, entry, value):
    assert entry == credentials._ENCRYPTION_KEY_ENTRY
    with (root / 'key-writes').open('a') as stream: stream.write(name + '\n')
    (root / 'key-entered').touch()
    deadline = time.monotonic() + 10
    while not (root / 'release-key').exists():
        if time.monotonic() > deadline: raise RuntimeError('synthetic barrier timed out')
        time.sleep(.01)
    (root / 'key.txt').write_text(value)

keyring.get_password = read
keyring.set_password = write
store = credentials.OidcCredentialStore(coordinator=MutationCoordinator(root / 'shared.lock', timeout=10))
(root / (name + '-started')).touch()
result = asyncio.run(store.save(tokens(subject=name), expected=credentials.ABSENT))
(root / (name + '-result')).write_text(str(result))
