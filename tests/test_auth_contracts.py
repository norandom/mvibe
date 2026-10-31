from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone

import pytest

from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import (
    AuthError, AuthFailure, AuthSnapshot, LoginCompleted, LoginHandle, LoginReusedCurrent,
)
from mvibe_auth._oidc_config import OidcConfig
from mvibe_auth._credentials_port import PlatformCredentialSnapshot


def test_defaults_and_disabled_snapshot():
    config = AuthConfig.from_env()
    assert config.enabled is False
    assert config.issuer == 'https://auth.mistral.ai'
    assert config.client_id == '829da7e7-628f-4795-829a-8cca609c76d4'
    assert config.audience == 'https://api.mistral.ai'
    assert config.redirect_uri == 'http://127.0.0.1:8766/callback'
    assert config.scope == 'openid offline_access email profile'
    assert config.organization_id == config.workspace_id == ''
    with pytest.raises(AuthError) as error:
        config.to_oidc_config()
    assert error.value.failure.code == 'rejected'
    with pytest.raises(FrozenInstanceError):
        config.enabled = True


def test_overrides_capture_and_explicit_mapping(monkeypatch):
    overrides = dict(ENABLED='true', ISSUER='https://issuer.example', CLIENT_ID='client',
                     AUDIENCE='https://inference.example', REDIRECT_URI='http://[::1]:8767/callback',
                     SCOPE='openid profile', ORGANIZATION_ID='org', WORKSPACE_ID='workspace')
    for key, value in overrides.items():
        monkeypatch.setenv('VIBE_CLI_OIDC_' + key, value)
    config = AuthConfig.from_env()
    for key in ('issuer', 'client_id', 'audience', 'redirect_uri', 'scope', 'organization_id', 'workspace_id'):
        assert getattr(config, key) == overrides[key.upper()]
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', 'false')
    monkeypatch.setenv('VIBE_CLI_OIDC_WORKSPACE_ID', 'changed')
    assert config.enabled and config.workspace_id == 'workspace'
    assert not AuthConfig.from_env().enabled
    assert AuthConfig.from_oidc_config(config.to_oidc_config(), enabled=True,
        organization_id='org', workspace_id='workspace') == config


@pytest.mark.parametrize('issuer,client,audience', [
    ('https://auth.mistral.ai', '829da7e7-628f-4795-829a-8cca609c76d4', 'https://api.mistral.ai'),
    ('https://auth.globalaegis.net', '3484b11e-2029-4a52-b289-dc826fc03c00', 'https://api.globalaegis.net'),
])
def test_known_registrations(monkeypatch, issuer, client, audience):
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', '1')
    monkeypatch.setenv('VIBE_CLI_OIDC_ISSUER', issuer)
    config = AuthConfig.from_env()
    assert (config.client_id, config.audience) == (client, audience)
    assert config.to_oidc_config().web_origins


@pytest.mark.parametrize('field,value', [
    ('issuer', 'http://issuer.example'), ('issuer', 'https://[secret-invalid'),
    ('issuer', 'https://host:secret-invalid'), ('issuer', 'https://user:secret-invalid@host'),
    ('issuer', 'https://host/?secret-invalid'), ('issuer', 'https://host/#secret-invalid'),
    ('issuer', 'https://bad host'), ('issuer', 'https://host\\secret-invalid'),
    ('issuer', 'https://host\nsecret-invalid'), ('issuer', ''),
    ('audience', ''), ('audience', '  '), ('audience', 'http://remote.example'),
    ('redirect_uri', 'http://localhost:8766/callback'), ('redirect_uri', 'http://127.0.0.1/callback'),
    ('redirect_uri', 'https://127.0.0.1:8766/callback'), ('redirect_uri', 'http://127.0.0.1:0/callback'),
    ('scope', 'profile'), ('client_id', '  '), ('organization_id', '  '), ('workspace_id', '  '),
])
def test_configuration_rejects_invalid_values_safely(field, value):
    with pytest.raises(AuthError) as error:
        AuthConfig(**{field: value})
    assert error.value.failure.code == 'invalid'
    assert 'secret-invalid' not in str(error.value)
    assert 'secret-invalid' not in repr(error.value)
    assert 'secret-invalid' not in repr(error.value.failure)


def test_unknown_issuer_requires_registration_overrides(monkeypatch):
    monkeypatch.setenv('VIBE_CLI_OIDC_ISSUER', 'https://issuer.example')
    with pytest.raises(AuthError):
        AuthConfig.from_env()
    monkeypatch.setenv('VIBE_CLI_OIDC_CLIENT_ID', 'client')
    with pytest.raises(AuthError):
        AuthConfig.from_env()


def test_direct_oidc_mapping_is_validated():
    with pytest.raises(AuthError):
        AuthConfig.from_oidc_config(OidcConfig('https://issuer.example', 'client', '',
            'http://127.0.0.1:8766/callback'), enabled=True)


def test_snapshots_are_immutable_id_token_request_credentials():
    headers = {'Authorization': 'Bearer synthetic-id-token'}
    snapshot = AuthSnapshot(headers=headers, expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
                            revision='opaque-revision', target='https://api.mistral.ai/v1/models')
    headers['Authorization'] = 'changed'
    assert snapshot.headers['Authorization'] == 'Bearer synthetic-id-token'
    assert snapshot.kind == 'id_token'
    assert 'synthetic-id-token' not in repr(snapshot)
    assert 'synthetic-id-token' not in str(snapshot)
    with pytest.raises(TypeError):
        snapshot.headers['Authorization'] = 'changed'
    with pytest.raises(FrozenInstanceError):
        snapshot.revision = 'changed'
    userinfo = PlatformCredentialSnapshot(kind='oauth_access_token', headers={'Authorization': 'Bearer access'},
        cache_namespace='userinfo', revision='other')
    assert userinfo.kind != snapshot.kind
    assert not any('refresh' in f.name or f.name == 'access_token' for f in fields(snapshot))


def test_login_outcomes_and_failures_are_safe_and_distinct():
    assert LoginCompleted().outcome == 'completed'
    assert LoginReusedCurrent().outcome == 'reused_current'
    assert LoginCompleted() != LoginReusedCurrent()
    handle = LoginHandle(attempt_id='attempt', authorization_url='https://issuer.example/?code=secret-code',
                         expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert 'secret-code' not in repr(handle)
    for code in ('missing', 'invalid', 'rejected', 'transient', 'conflict', 'cancelled'):
        failure = AuthFailure(code=code, retryable=code in ('transient', 'conflict'))
        assert failure.message and str(AuthError(failure)) == failure.message
        with pytest.raises(FrozenInstanceError):
            failure.code = 'missing'
    with pytest.raises(ValueError, match='Invalid authentication failure code'):
        AuthFailure(code='secret-invalid')


def test_public_contract_exports():
    from mvibe_auth.api import AuthConfig as PublicConfig, AuthSnapshot as PublicSnapshot
    assert PublicConfig is AuthConfig
    assert PublicSnapshot is AuthSnapshot

@pytest.mark.parametrize('value,enabled', [('1', True), ('true', True), ('TRUE', True), ('0', False), ('false', False), ('', False)])
def test_feature_enabled_is_captured(monkeypatch, value, enabled):
    monkeypatch.setenv('VIBE_CLI_OIDC_ENABLED', value)
    assert AuthConfig.from_env().enabled is enabled


@pytest.mark.parametrize('field,value', [('enabled', 'false'), ('workspace_id', None), ('issuer', None), ('audience', 7)])
def test_configuration_types_are_validated(field, value):
    with pytest.raises(AuthError) as error:
        AuthConfig(**{field: value})
    assert error.value.failure.code == 'invalid'


def test_numeric_loopback_policy_is_preserved():
    config = AuthConfig(issuer='http://127.0.0.1:8765', audience='http://[::1]:8000', enabled=True)
    assert config.to_oidc_config().issuer == config.issuer


def test_malformed_url_has_internal_cause_and_safe_public_failure(monkeypatch):
    monkeypatch.setenv('VIBE_CLI_OIDC_ISSUER', 'https://[secret-invalid')
    with pytest.raises(AuthError) as error:
        AuthConfig.from_env()
    assert error.value.__cause__ is not None
    assert 'secret-invalid' not in error.value.failure.message


def test_snapshot_cannot_contain_mutable_header_values():
    with pytest.raises(AuthError):
        AuthSnapshot(headers={'Authorization': ['synthetic-id-token']},
                     expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc), revision='rev', target='https://api.mistral.ai')
