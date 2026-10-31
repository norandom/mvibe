"""Local test executable: patches dependencies before running the real module.

No production environment variable or wire method enables these replacements.
"""
import asyncio
import os
from pathlib import Path
import runpy
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

# Installs the same pre-import default-deny network/browser/keyring guards as
# pytest. Only the specific synthetic keyring functions below are reopened.
sys.path.insert(0, str(Path(__file__).parent))
from tests import conftest  # noqa: F401
import keyring

from mvibe_auth import api, bridge
from mvibe_auth._coordination import MutationCoordinator
from mvibe_auth._oidc_service import OidcService
from tests.test_credentials import tokens

root, mode = Path(sys.argv[1]), sys.argv[2]
(root / 'runtime.pid').write_text(str(os.getpid()))


def mark(name):
    (root / name).touch()


def wait(name):
    deadline = time.monotonic() + 20
    while not (root / name).exists():
        if time.monotonic() > deadline:
            raise RuntimeError('synthetic barrier expired')
        time.sleep(.01)


def read(service, entry):
    path = root / 'slot.json'
    return path.read_text() if path.exists() else None


def write(service, entry, raw):
    mark('write-entered')
    if mode.endswith('before'):
        wait('release-write')
    temporary = root / 'slot.tmp'
    temporary.write_text(raw)
    os.replace(temporary, root / 'slot.json')
    mark('write-committed')
    if mode.endswith('after'):
        wait('release-write')


keyring.get_password = read
keyring.set_password = write
config = api.AuthConfig(issuer='https://synthetic.invalid', client_id='synthetic-client',
    audience='https://inference.invalid', organization_id='synthetic-org', enabled=True)
service = OidcService.from_config(config)
service.store.coordinator = MutationCoordinator(root / 'coord.lock', timeout=5)
service.client.discover = AsyncMock(return_value=SimpleNamespace(
    authorization_endpoint='https://synthetic.invalid/authorize',
    token_endpoint='https://synthetic.invalid/token'))
service.client.validated_tokens = AsyncMock(return_value=tokens(
    expires_at=4102444800, id_token_expires_at=4102444800))


class Callback:
    def __init__(self, uri, state):
        self.state = state
        self.result = asyncio.get_running_loop().create_future()
        self.feed = None

    async def __aenter__(self):
        async def feed():
            while not (root / 'release-callback').exists():
                await asyncio.sleep(.01)
            self.result.set_result('https://synthetic.invalid/callback?code=synthetic')
        self.feed = asyncio.create_task(feed())
        return self

    async def __aexit__(self):
        self.feed.cancel()
        await asyncio.gather(self.feed, return_exceptions=True)
        if not self.result.done():
            self.result.cancel()
        mark('callback-closed')


class Issuer:
    def create_authorization_url(self, endpoint, **kwargs):
        mark('callback-ready')
        return 'https://synthetic.invalid/authorize', kwargs['state']

    async def fetch_token(self, *args, **kwargs):
        return {'synthetic': 'response'}

    async def aclose(self):
        mark('issuer-closed')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        mark('issuer-closed')

    async def refresh_token(self, *args, **kwargs):
        mark('refresh-entered')
        if mode == 'refresh-wait':
            await asyncio.Event().wait()
        return {'synthetic': 'response'}


import mvibe_auth._oidc_service as service_module
service_module.OidcCallback = Callback
service.client.session = Issuer
OidcService.from_config = classmethod(lambda cls, config: service)
bridge.create_session = lambda: api.AuthSession(config)
sys.argv = ['mvibe_auth', 'bridge']
runpy.run_module('mvibe_auth', run_name='__main__')
