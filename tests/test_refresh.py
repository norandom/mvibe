"""Synthetic service cycling through real credential store and token validation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from authlib.integrations.base_client.errors import OAuthError
import pytest

from mvibe_auth._auth_config import AuthConfig
from mvibe_auth._auth_contracts import AuthError
from mvibe_auth._coordination import MutationCoordinator
from mvibe_auth._oidc_config import OidcError
from mvibe_auth._oidc_service import OidcService
from tests.test_credentials import tokens, slot

NOW = 1700000000


@pytest.fixture
def setup(slot, monkeypatch, tmp_path):
    monkeypatch.setattr('mvibe_auth._oidc_service.time.time', lambda: NOW)
    config = AuthConfig(issuer='https://synthetic.invalid', client_id='synthetic-client',
                        audience='https://inference.invalid', enabled=True)
    service = OidcService.from_config(config)
    service.store.coordinator = MutationCoordinator(tmp_path / 'slot.lock')
    calls = []
    effects = []
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def refresh_token(self, endpoint, **kwargs):
            calls.append(kwargs['refresh_token'])
            if effects:
                result = effects.pop(0)()
                if isinstance(result, Exception): raise result
                return result
            return {'access_token': 'rotated-access', 'token_type': 'Bearer', 'expires_in': 900}
    service.client.session = Session
    service.client.discover = AsyncMock(return_value=SimpleNamespace(token_endpoint='https://synthetic.invalid/token'))
    slot.external(tokens())
    return SimpleNamespace(service=service, slot=slot, calls=calls, effects=effects)


def put(setup, **changes):
    value = tokens().model_validate(tokens().model_dump() | changes)
    setup.slot.external(value)
    return value


@pytest.mark.anyio
@pytest.mark.parametrize('expiry,refresh', [(29, True), (30, True), (30.01, False)])
@pytest.mark.parametrize('field', ['expires_at', 'id_token_expires_at'])
async def test_exact_freshness_threshold(setup, field, expiry, refresh):
    put(setup, **{field: NOW + expiry})
    # Controlled validated issuer result; threshold is service policy, signature tests live in client suite.
    setup.service.client.validated_tokens = AsyncMock(return_value=tokens())
    await setup.service.access_tokens(require_id_token=True)
    assert len(setup.calls) == int(refresh)


@pytest.mark.anyio
async def test_authoritative_reload_even_fresh_cache(setup):
    cached = await setup.service.store.load()
    newer = put(setup, subject='external-person')
    assert newer != cached
    assert await setup.service.access_tokens() == newer
    assert setup.calls == []


@pytest.mark.anyio
async def test_refresh_omissions_keep_fresh_id_and_refresh(setup):
    original = put(setup, expires_at=NOW + 30)
    updated = await setup.service.access_tokens(require_id_token=True)
    assert updated.id_token == original.id_token
    assert updated.refresh_token == original.refresh_token
    assert updated.access_token.get_secret_value() == 'rotated-access'
    assert updated.expires_at == NOW + 900
    assert await setup.service.store.reload() == updated


@pytest.mark.anyio
@pytest.mark.parametrize('expiry', [None, NOW - 1, NOW + 30, float('nan'), float('inf')])
async def test_omitted_id_without_fresh_prior_is_refused(setup, expiry):
    put(setup, id_token_expires_at=expiry)
    with pytest.raises(OidcError, match='re-login'):
        await setup.service.access_tokens(require_id_token=True)
    assert setup.calls == ['synthetic-refresh']
    assert setup.slot.writes == 0


@pytest.mark.anyio
async def test_access_only_does_not_refresh_for_stale_id(setup):
    original = put(setup, id_token_expires_at=None)
    assert await setup.service.access_tokens() == original
    assert setup.calls == []


@pytest.mark.anyio
async def test_invalid_grant_unchanged_rejected_once(setup):
    put(setup, expires_at=NOW)
    setup.effects.append(lambda: OAuthError(error='invalid_grant', description='synthetic-secret'))
    with pytest.raises(OidcError, match='re-login') as error:
        await setup.service.access_tokens()
    assert setup.calls == ['synthetic-refresh']
    assert 'synthetic-secret' not in str(error.value)
    assert setup.slot.writes == 0


@pytest.mark.anyio
async def test_invalid_grant_tenant_only_change_is_unchanged(setup):
    from tests.test_credentials import tenant
    put(setup, expires_at=NOW)
    def rejected():
        put(setup, expires_at=NOW, tenant=tenant())
        return OAuthError(error='invalid_grant')
    setup.effects.append(rejected)
    with pytest.raises(OidcError, match='re-login'):
        await setup.service.access_tokens()
    assert len(setup.calls) == 1


@pytest.mark.anyio
async def test_invalid_grant_changed_token_retries_rotation(setup):
    put(setup, expires_at=NOW)
    def rejected():
        put(setup, expires_at=NOW, refresh_token='external-refresh')
        return OAuthError(error='invalid_grant')
    setup.effects.append(rejected)
    result = await setup.service.access_tokens(require_id_token=True)
    assert result.refresh_token.get_secret_value() == 'external-refresh'
    assert setup.calls == ['synthetic-refresh', 'external-refresh']


@pytest.mark.anyio
async def test_invalid_grant_changed_fresh_set_reused(setup):
    put(setup, expires_at=NOW)
    def rejected():
        put(setup, access_token='external-access')
        return OAuthError(error='invalid_grant')
    setup.effects.append(rejected)
    assert (await setup.service.access_tokens()).access_token.get_secret_value() == 'external-access'
    assert len(setup.calls) == 1


@pytest.mark.anyio
async def test_changed_material_never_retries_same_rejected_refresh(setup):
    put(setup, expires_at=NOW)
    def rejected():
        put(setup, expires_at=NOW, access_token='external-access')
        return OAuthError(error='invalid_grant')
    setup.effects.append(rejected)
    with pytest.raises(OidcError, match='re-login'):
        await setup.service.access_tokens()
    assert setup.calls == ['synthetic-refresh']


@pytest.mark.anyio
async def test_commit_conflict_reuses_authoritative_new_identity(setup):
    put(setup, expires_at=NOW)
    def competing():
        put(setup, subject='external-person')
        return {'access_token': 'discarded-access', 'token_type': 'Bearer', 'expires_in': 900}
    setup.effects.append(competing)
    result = await setup.service.access_tokens()
    assert result.subject == 'external-person'
    assert setup.slot.writes == 0


@pytest.mark.anyio
@pytest.mark.parametrize('first_invalid_grant', [False, True])
async def test_mixed_conflicts_share_two_pass_budget(setup, first_invalid_grant):
    put(setup, expires_at=NOW)
    def competing():
        put(setup, expires_at=NOW, refresh_token=f'external-refresh-{len(setup.calls)}')
        if first_invalid_grant and len(setup.calls) == 1:
            return OAuthError(error='invalid_grant', description='synthetic-secret')
        return {'access_token': 'discarded-access', 'token_type': 'Bearer', 'expires_in': 900}
    setup.effects.extend([competing, competing])
    with pytest.raises(AuthError) as error:
        await setup.service.access_tokens()
    assert error.value.failure.code == 'conflict'
    assert error.value.failure.retryable
    assert len(setup.calls) == 2
    assert setup.slot.writes == 0
    assert 'synthetic-' not in str(error.value)


@pytest.mark.anyio
async def test_no_refresh_credentials_gives_relogin(setup):
    put(setup, expires_at=NOW, refresh_token=None)
    with pytest.raises(OidcError, match='re-login'):
        await setup.service.access_tokens()
    assert setup.calls == []


@pytest.mark.anyio
async def test_changed_subject_validation_rejected_before_commit(setup):
    put(setup, expires_at=NOW)
    setup.service.client.validated_tokens = AsyncMock(return_value=tokens(subject='unexpected-person'))
    with pytest.raises(OidcError, match='identity'):
        await setup.service.access_tokens()
    assert setup.slot.writes == 0


@pytest.mark.anyio
@pytest.mark.parametrize('expiry', [None, NOW, NOW + 30])
async def test_refresh_replacement_id_must_be_fresh(setup, expiry):
    put(setup, expires_at=NOW)
    setup.service.client.validated_tokens = AsyncMock(return_value=tokens(id_token_expires_at=expiry))
    with pytest.raises(OidcError, match='re-login'):
        await setup.service.access_tokens(require_id_token=True)
    assert setup.slot.writes == 0


@pytest.mark.anyio
async def test_second_invalid_grant_exhausts_budget(setup):
    put(setup, expires_at=NOW)
    def rejected():
        put(setup, expires_at=NOW, refresh_token=f'external-refresh-{len(setup.calls)}')
        return OAuthError(error='invalid_grant')
    setup.effects.extend([rejected, rejected])
    with pytest.raises(AuthError) as error:
        await setup.service.access_tokens()
    assert error.value.failure.code == 'conflict'
    assert error.value.failure.retryable
    assert len(setup.calls) == 2


@pytest.mark.anyio
async def test_previously_consumed_budget_not_reset(setup):
    from mvibe_auth._coordination import ResolutionBudget
    budget = ResolutionBudget()
    budget.conflict()  # Represents tenant discovery restarting the same operation.
    put(setup, expires_at=NOW)
    def competing():
        put(setup, subject='external-person')
        return {'access_token': 'discarded-access', 'token_type': 'Bearer', 'expires_in': 900}
    setup.effects.append(competing)
    with pytest.raises(AuthError) as error:
        await setup.service.access_tokens(budget=budget)
    assert error.value.failure.code == 'conflict'
    assert len(setup.calls) == 1


def test_inference_runtime_uses_one_id_aware_resolution(monkeypatch):
    from mvibe_auth._oidc_runtime import _resolve_id_token
    monkeypatch.setattr('mvibe_auth._oidc_service.time.time', lambda: NOW)
    service = SimpleNamespace(access_tokens=AsyncMock(return_value=tokens()))
    assert _resolve_id_token(service) == 'synthetic-id'
    service.access_tokens.assert_awaited_once_with(require_id_token=True)


def test_inference_runtime_preserves_typed_conflict(monkeypatch):
    from mvibe_auth._auth_contracts import AuthFailure
    from mvibe_auth._oidc_runtime import _resolve_id_token
    conflict = AuthError(AuthFailure('conflict', retryable=True))
    service = SimpleNamespace(access_tokens=AsyncMock(side_effect=conflict))
    with pytest.raises(AuthError) as error:
        _resolve_id_token(service)
    assert error.value is conflict
