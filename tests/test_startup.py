"""Startup, caller-owned browser presentation, and loopback CLI checks."""
import importlib

import pytest

import proxy
from mvibe_auth.api import AuthConfig, AuthError, AuthFailure, LoginReusedCurrent
from test_proxy import Session, harness


def test_import_and_app_construction_are_inert(monkeypatch):
    monkeypatch.delenv('VIBE_CLI_OIDC_ENABLED', raising=False)
    importlib.reload(proxy)
    assert 'VIBE_CLI_OIDC_ENABLED' not in __import__('os').environ
    def forbidden():
        raise AssertionError('factory called before lifespan')
    proxy.create_app(forbidden, forbidden)


@pytest.mark.anyio
@pytest.mark.parametrize(('signed_in', 'matches', 'forced', 'login'), [
    (False, False, False, True), (True, False, False, True),
    (True, True, False, False), (True, True, True, True)])
async def test_startup_identity_policy(monkeypatch, capsys, signed_in, matches, forced, login):
    session = Session(signed_in=signed_in, config_match=matches)
    opened = []
    monkeypatch.setattr(proxy.webbrowser, 'open', opened.append)
    app, _, _, clients = harness(session, force=forced)
    async with app.router.lifespan_context(app):
        assert bool(clients)
        assert (('login', forced) in session.events) == login
        assert bool(opened) == login
        assert ('Sign in: https://issuer.example/authorize' in capsys.readouterr().out) == login
        if login:
            assert session.events.index(('wait', 'attempt')) < len(session.events)
    assert session.events[-1] == 'close'


@pytest.mark.anyio
async def test_failed_login_does_not_start_serving(monkeypatch):
    session = Session(signed_in=False, config_match=False)
    session.failure = AuthFailure('rejected')
    monkeypatch.setattr(proxy.webbrowser, 'open', lambda _: False)
    app, _, _, clients = harness(session)
    with pytest.raises(AuthError):
        async with app.router.lifespan_context(app):
            pytest.fail('failed startup yielded serving app')
    assert not clients and session.events[-1] == 'close'


@pytest.mark.anyio
async def test_reused_current_login_is_successful_without_claiming_browser_identity(monkeypatch, capsys):
    session = Session(signed_in=False, config_match=False)
    async def wait(attempt_id):
        return LoginReusedCurrent()
    session.wait_login = wait
    monkeypatch.setattr(proxy.webbrowser, 'open', lambda _: False)
    app, _, _, clients = harness(session)
    async with app.router.lifespan_context(app):
        assert clients
    assert 'saved' not in capsys.readouterr().out


@pytest.mark.parametrize('enabled', [None, '0', 'false', '1'])
def test_proxy_default_opt_in_respects_explicit_disablement(monkeypatch, enabled):
    if enabled is not None:
        monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', enabled)
    captured = []
    monkeypatch.setattr(proxy, 'AuthSession', captured.append)
    proxy._default_auth_factory()
    assert captured[0].enabled == (enabled in (None, '1'))
    assert __import__('os').environ.get('VIBE_CLI_OIDC_ENABLED') == enabled


@pytest.mark.anyio
async def test_invalid_configuration_stops_startup_before_client_creation():
    clients = []
    def invalid_auth():
        return AuthConfig(audience='invalid-secret')
    app = proxy.create_app(invalid_auth, lambda **kwargs: clients.append(kwargs))
    with pytest.raises(AuthError) as error:
        async with app.router.lifespan_context(app):
            pytest.fail('invalid config served')
    assert 'invalid-secret' not in str(error.value)
    assert not clients


def test_proxy_uses_public_auth_imports_only():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(proxy.__file__).read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(module.startswith('mvibe_auth._') for module in imports if module)


@pytest.mark.parametrize(('arguments', 'port', 'force'), [([], 8765, False), (['--port', '9123', '--login'], 9123, True)])
def test_cli_loopback_and_port(monkeypatch, arguments, port, force):
    calls = []
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda app, **kwargs: calls.append((app, kwargs)))
    proxy.main(arguments)
    assert calls[0][1] == {'host': '127.0.0.1', 'port': port, 'access_log': False}
    assert calls[0][0].state.force_login is force


@pytest.mark.parametrize('port', ['0', '-1', '65536', 'invalid'])
def test_cli_invalid_port_is_rejected(monkeypatch, port):
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda *a, **k: pytest.fail('invalid port served'))
    with pytest.raises(SystemExit) as error:
        proxy.main(['--port', port])
    assert error.value.code == 2
