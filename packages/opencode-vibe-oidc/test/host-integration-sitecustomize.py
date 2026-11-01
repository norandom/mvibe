"""Test-only child bootstrap, installed in a temporary isolated PYTHONPATH."""
import os
import json
import socket
import sys
import webbrowser
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import keyring


def deny(*args, **kwargs):
    raise RuntimeError('offline fixture blocked external effect')


original_connect = socket.socket.connect
socketpair_code = getattr(socket.socketpair, '__code__', None)


def socketpair_connect(self, address):
    # Windows asyncio's stdlib socketpair alone may connect its local pair.
    if (socketpair_code is not None and sys._getframe(1).f_code is socketpair_code
            and address[0] in ('127.0.0.1', '::1')):
        return original_connect(self, address)
    return deny()


socket.socket.connect = socketpair_connect
socket.socket.connect_ex = deny
socket.create_connection = deny
socket.getaddrinfo = deny
for name in ('open', 'open_new', 'open_new_tab', 'get'):
    setattr(webbrowser, name, deny)
for name in ('get_keyring', 'get_password', 'get_credential', 'set_password', 'delete_password'):
    setattr(keyring, name, deny)

from mvibe_auth import LoginHandle, LoginCompleted, AuthSnapshot
import mvibe_auth.bridge as bridge
import mvibe_auth.login_cli as cli


class Session:
    config = SimpleNamespace(audience='https://inference.example.test')

    def __init__(self, config=None):
        pass

    async def status(self):
        return dict(signed_in=True, config_match=True)

    async def start_login(self, force=False):
        return LoginHandle('synthetic', '', datetime.now(timezone.utc))

    async def wait_login(self, attempt):
        return LoginCompleted()

    async def resolve(self, target):
        with open(os.environ['VIBE_TEST_ROTATION']) as source:
            revision = source.read()
        if os.environ.get('VIBE_TEST_RESOLUTIONS'):
            # Test-only synthetic boundary observation, independent of JS bundling.
            with open(os.environ['VIBE_TEST_RESOLUTIONS'], 'a') as evidence:
                evidence.write(json.dumps({'target': target, 'revision': revision}) + '\n')
        return AuthSnapshot({'Authorization': 'Bearer synthetic-' + revision,
                             'User-Agent': 'mistral-client-python/Mistral-Vibe/3.0.4'},
                            datetime.now(timezone.utc) + timedelta(hours=1), revision, target)

    async def cancel_login(self, attempt):
        pass

    async def close(self):
        pass


bridge.create_session = Session
cli.AuthSession = Session
