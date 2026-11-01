import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx2
import pytest

from mvibe_auth import AuthError, AuthSnapshot
from mvibe_auth.existing_vibe import ExistingVibeSession
from mvibe_auth import existing_vibe
from mvibe_auth.account_plan import AccountPlanResolver


@pytest.fixture(autouse=True)
def guard_native_command(monkeypatch):
    # Common session tests patch the reader. OS branch tests explicitly replace
    # this guard; an omitted mock must never access the user's macOS Keychain.
    def denied(*_args, **_kwargs):
        pytest.fail('unexpected native credential command')
    monkeypatch.setattr(existing_vibe, 'subprocess', SimpleNamespace(run=denied))


@pytest.mark.anyio
@pytest.mark.parametrize('platform', ['win32', 'darwin'])
async def test_native_source_is_fresh_isolated_and_opaque(monkeypatch, platform):
    calls = []
    values = iter(['opaque-one', 'opaque-two', None])
    def read(service, entry):
        calls.append((service, entry))
        return next(values)
    monkeypatch.setenv('MISTRAL_API_KEY', 'environment-must-not-be-used')
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'false')
    monkeypatch.setenv('VIBE_CLI_OIDC_AUDIENCE', 'https://evil.invalid')
    monkeypatch.setattr(existing_vibe, 'sys', SimpleNamespace(platform=platform))
    if platform == 'darwin':
        def command(argv, **kwargs):
            assert argv == ['/usr/bin/security', 'find-generic-password', '-s',
                            'ai.mistral.vibe', '-a', 'MISTRAL_API_KEY', '-w']
            assert kwargs == dict(capture_output=True, text=True, timeout=60, check=False)
            value = read('ai.mistral.vibe', 'MISTRAL_API_KEY')
            return SimpleNamespace(returncode=44 if value is None else 0,
                                   stdout='' if value is None else value + '\n')
        monkeypatch.setattr(existing_vibe.subprocess, 'run', command)
    else:
        monkeypatch.setattr('keyring.get_password', read)
    session = ExistingVibeSession()
    first = await session.resolve('https://api.mistral.ai/v1/models')
    second = await session.resolve('https://api.mistral.ai/v1/models')
    assert calls == [('ai.mistral.vibe', 'MISTRAL_API_KEY')] * 2
    assert first.headers['Authorization'] == 'Bearer opaque-one'
    assert first.kind == 'vibe_credential'
    assert first.revision != second.revision and 'opaque' not in first.revision
    assert 'opaque' not in repr(first)
    assert 0 < (first.expires_at - datetime.now(timezone.utc)).total_seconds() <= 60
    assert not any(name.lower().startswith('x-external') for name in first.headers)
    assert 'Mistral-Vibe/' in first.headers['User-Agent']
    assert await session.status() == {'signed_in': False, 'config_match': False}
    await session.close()
    with pytest.raises(AuthError, match='cancelled'):
        await session.status()


@pytest.mark.anyio
@pytest.mark.parametrize('url', ['https://evil.invalid/v1/models', 'http://api.mistral.ai/v1/models',
    'https://api.mistral.ai:444/v1/models', 'https://user@api.mistral.ai/v1/models',
    'https://api.mistral.ai/v1/models#fragment', 'https://console.mistral.ai/api/vibe/whoami', 'https://api.mistral.ai/admin',
    'https://api.mistral.ai/v1/models/../admin', 'https://api.mistral.ai'])
async def test_native_destinations_rejected_before_storage(url, monkeypatch):
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', lambda *_: pytest.fail('storage read'))
    session = ExistingVibeSession()
    with pytest.raises(AuthError):
        await session.resolve(url)
    await session.close()


@pytest.mark.anyio
async def test_errors_are_secret_safe_and_missing_never_logs_in(monkeypatch):
    session = ExistingVibeSession()
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', lambda *_: None)
    with pytest.raises(AuthError) as error:
        await session.resolve(session.config.audience + '/v1/models')
    assert error.value.failure.code == 'missing'
    with pytest.raises(AuthError):
        await session.start_login()
    def bad(*_):
        raise RuntimeError('opaque-super-secret')
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', bad)
    with pytest.raises(AuthError) as error:
        await session.status()
    assert error.value.failure.code == 'transient'
    assert 'opaque-super-secret' not in str(error.value)
    await session.close()


@pytest.mark.anyio
async def test_native_account_endpoint_and_kind_cache_isolation(monkeypatch):
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', lambda *_: 'opaque')
    session = ExistingVibeSession()
    native = await session.resolve(session.config.audience + '/v1/models')
    sent = []
    def upstream(request):
        sent.append(request)
        return httpx2.Response(200, json={'plan_type': 'CHAT', 'plan_name': 'INDIVIDUAL'})
    resolver = AccountPlanResolver(session.config, transport=httpx2.MockTransport(upstream))
    assert await resolver.resolve(native) == 'Pro'
    assert await resolver.resolve(native) == 'Pro'
    oidc = AuthSnapshot(native.headers, native.expires_at, native.revision, native.target)
    assert await resolver.resolve(oidc) == 'Pro'
    assert [request.url.path for request in sent] == ['/api/vibe/whoami', '/api/vibe/account-context']
    assert not any(name.startswith('x-external') for name in sent[0].headers)
    await resolver.close()
    await session.close()


@pytest.mark.anyio
async def test_cancel_and_close_drain_native_worker(monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    def read(*_):
        started.set()
        release.wait(3)
        return 'discarded-opaque'
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', read)
    session = ExistingVibeSession()
    request = asyncio.create_task(session.resolve(session.config.audience + '/v1/models'))
    await asyncio.to_thread(started.wait, 2)
    request.cancel()
    closing = asyncio.create_task(session.close())
    await asyncio.sleep(0.01)
    assert not request.done() and not closing.done()
    closing.cancel()
    release.set()
    await closing
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not session._operations
    await session.close()


@pytest.mark.anyio
async def test_login_existing_is_local_and_marker_safe(monkeypatch, capsys):
    from mvibe_auth import login_cli
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', lambda *_: 'opaque')
    monkeypatch.setattr('webbrowser.open', lambda *_: pytest.fail('browser opened'))
    await login_cli.login_existing()


def test_login_existing_command_marker_and_missing(monkeypatch, capsys):
    from mvibe_auth import __main__, login_cli
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', lambda *_: 'opaque')
    monkeypatch.setattr('sys.argv', ['mvibe_auth', 'login-vibe'])
    assert __main__.main() == 0
    assert capsys.readouterr() == (login_cli.CONNECTION_MARKER, '')
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', lambda *_: None)
    assert __main__.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert 'normal Vibe' in output.err


def test_macos_native_storage_is_single_read_only_command(monkeypatch):
    from types import SimpleNamespace
    from mvibe_auth import existing_vibe
    calls = []
    monkeypatch.setattr(existing_vibe, 'sys', SimpleNamespace(platform='darwin'))
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='opaque\n')
    monkeypatch.setattr(existing_vibe.subprocess, 'run', run)
    monkeypatch.setattr('keyring.get_password', lambda *_: pytest.fail('unexpected keyring read'))
    assert existing_vibe._read_native_credential() == 'opaque'
    assert calls == [(['/usr/bin/security', 'find-generic-password', '-s', 'ai.mistral.vibe',
        '-a', 'MISTRAL_API_KEY', '-w'], dict(capture_output=True, text=True, timeout=60, check=False))]
    monkeypatch.setattr(existing_vibe.subprocess, 'run', lambda *_args, **_kwargs: SimpleNamespace(returncode=44))
    assert existing_vibe._read_native_credential() is None
    monkeypatch.setattr(existing_vibe.subprocess, 'run', lambda *_args, **_kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match='storage is unavailable'):
        existing_vibe._read_native_credential()


@pytest.mark.anyio
async def test_macos_timeout_is_sanitized(monkeypatch):
    import subprocess
    from mvibe_auth import existing_vibe
    monkeypatch.setattr(existing_vibe, 'sys', SimpleNamespace(platform='darwin'))
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired('opaque-secret', 60, output='opaque-secret')
    monkeypatch.setattr(existing_vibe.subprocess, 'run', timeout)
    session = ExistingVibeSession()
    with pytest.raises(AuthError) as error:
        await session.status()
    assert error.value.failure.code == 'transient'
    assert 'opaque' not in str(error.value)
    await session.close()


@pytest.mark.anyio
async def test_bridge_native_factory_retains_v1_and_safe_missing(monkeypatch):
    import json
    from mvibe_auth.bridge import serve
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', lambda *_: 'opaque')
    inbound, outbound = asyncio.Queue(), asyncio.Queue()
    async def read(_limit):
        return await inbound.get()
    async def write(frame):
        await outbound.put(json.loads(frame))
    task = asyncio.create_task(serve(read, write, session_factory=ExistingVibeSession))
    async def request(method, **params):
        await inbound.put(json.dumps(dict(version=1, id=method, method=method, params=params)).encode() + b'\n')
        return await asyncio.wait_for(outbound.get(), 3)
    try:
        response = await request('resolve', target_url='https://api.mistral.ai/v1/models')
        assert response['ok'] is True
        assert response['result']['headers']['Authorization'] == 'Bearer opaque'
        assert 'kind' not in response['result']
        assert 'opaque' not in response['result']['revision']
        monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', lambda *_: None)
        response = await request('resolve', target_url='https://api.mistral.ai/v1/models')
        assert response['error']['code'] == 'missing'
        assert 'normal Vibe' in response['error']['message']
        await request('shutdown')
    finally:
        await inbound.put(b'')
        await task


@pytest.mark.anyio
async def test_proxy_explicit_native_source_never_starts_browser(monkeypatch):
    import proxy
    monkeypatch.setattr('mvibe_auth.existing_vibe._read_native_credential', lambda *_: 'opaque')
    monkeypatch.setattr('webbrowser.open', lambda *_: pytest.fail('browser opened'))
    session = ExistingVibeSession()
    await proxy.login(session, force=True)
    await session.close()


def test_bridge_vibe_command_selects_factory_only(monkeypatch):
    from mvibe_auth import __main__, bridge
    called = []
    async def stdio(**kwargs):
        called.append(kwargs)
    monkeypatch.setattr(bridge, 'stdio', stdio)
    monkeypatch.setattr('sys.argv', ['mvibe_auth', 'bridge-vibe'])
    assert __main__.main() == 0
    assert called == [dict(session_factory=ExistingVibeSession)]
