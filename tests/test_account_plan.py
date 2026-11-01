"""Synthetic account context: no login, keyring, API keys or remote traffic."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import httpx2
import pytest

from mvibe_auth.account_plan import AccountPlanResolver
from mvibe_auth.api import AuthConfig, AuthSnapshot
from mvibe_auth.request_metadata import RequestContext, build_request_metadata


def snapshot(revision='one', organization='org', workspace='workspace'):
    return AuthSnapshot({'Authorization': 'Bearer synthetic-id',
        'x-external-organization-id': organization, 'x-external-workspace-id': workspace,
        'User-Agent': 'mistral-client-python/Mistral-Vibe/3.0.4'},
        datetime.now(timezone.utc) + timedelta(hours=1), revision,
        'https://api.mistral.ai/v1/chat/completions')


def account_payload(kind='CHAT', name='INDIVIDUAL'):
    return dict(plan_type=kind, plan_name=name, has_paid_vibe_entitlement=True,
        api_base='https://api.mistral.ai', vibe_base='https://chat.mistral.ai')


@pytest.mark.anyio
async def test_lookup_precedes_native_plan_and_reuses_current_snapshot():
    sent = []
    async def upstream(request):
        sent.append(request)
        return httpx2.Response(200, json=account_payload())
    resolver = AccountPlanResolver(AuthConfig(enabled=True), transport=httpx2.MockTransport(upstream))
    try:
        plan = await resolver.resolve(snapshot())
        assert plan == 'Pro'
        assert build_request_metadata(RequestContext(), user_plan=plan)['user_plan'] == 'Pro'
        assert await resolver.resolve(snapshot()) == 'Pro'
        assert len(sent) == 1
        assert str(sent[0].url) == 'https://console.mistral.ai/api/vibe/account-context'
        assert sent[0].headers['authorization'] == 'Bearer synthetic-id'
        assert sent[0].headers['x-external-organization-id'] == 'org'
        assert sent[0].headers['x-external-workspace-id'] == 'workspace'
    finally:
        await resolver.close()


@pytest.mark.anyio
@pytest.mark.parametrize(('kind', 'name', 'expected'), [
    ('CHAT', 'FREE', 'Free'), ('chat', ' individual ', 'Pro'), ('CHAT', 'EDU', 'Student'),
    ('CHAT', 'TEAM', 'Team'), ('MISTRAL_CODE', 'F', 'Free Codestral'),
    ('MISTRAL_CODE', 'E', 'Code Enterprise'), ('API', 'FREE', 'Free API'),
    ('API', 'PAID', 'PAYG API'), ('API', 'some_free_tier', 'Free API'),
    ('CHAT', 'FUTURE', None), ('FUTURE', 'INDIVIDUAL', None), ('API', '', None),
    ('NO_PLAN_DATA', 'NO_PLAN_DATA', None), (1, 'INDIVIDUAL', None), ('CHAT', 1, None),
])
async def test_native_allowlisted_plan_mapping(kind, name, expected):
    resolver = AccountPlanResolver(AuthConfig(enabled=True), transport=httpx2.MockTransport(
        lambda request: httpx2.Response(200, json=account_payload(kind, name))))
    try:
        assert await resolver.resolve(snapshot()) == expected
    finally:
        await resolver.close()


@pytest.mark.anyio
@pytest.mark.parametrize('response', [
    httpx2.Response(302, headers={'location': 'https://evil.invalid/account'}),
    httpx2.Response(401), httpx2.Response(403), httpx2.Response(500),
    httpx2.Response(200, content=b'not json'), httpx2.Response(200, json=[]),
    httpx2.Response(200, json={'plan_name': 'INDIVIDUAL'}),
    httpx2.Response(200, json={'plan_type': 'CHAT', 'plan_name': 'x' * 257}),
    httpx2.Response(200, content=b' ' * 65537),
    httpx2.Response(200, content=b'{"plan_type":"CHAT","plan_name":"INDIVIDUAL","x":NaN}'),
])
async def test_unavailable_or_invalid_account_omits_plan_without_retry(response, capsys):
    sent = []
    def upstream(request):
        sent.append(request)
        return response
    resolver = AccountPlanResolver(AuthConfig(enabled=True), transport=httpx2.MockTransport(upstream))
    try:
        assert await resolver.resolve(snapshot()) is None
        assert await resolver.resolve(snapshot()) is None
        assert len(sent) == 1
    finally:
        await resolver.close()
    assert capsys.readouterr() == ('', '')


@pytest.mark.anyio
@pytest.mark.parametrize('config', [SimpleNamespace(audience='https://inference.invalid'),
    AuthConfig(enabled=False), AuthConfig(enabled=True, issuer='https://unknown.invalid', client_id='custom'),
    AuthConfig(enabled=True, client_id='custom')])
async def test_unknown_or_disabled_registration_never_sends(config):
    def deny(request):
        raise AssertionError('must not send')
    resolver = AccountPlanResolver(config, transport=httpx2.MockTransport(deny))
    try:
        assert await resolver.resolve(snapshot()) is None
    finally:
        await resolver.close()


@pytest.mark.anyio
async def test_cache_is_revision_tenant_time_and_session_scoped():
    calls = []
    now = [0.0]
    def upstream(request):
        calls.append(request)
        return httpx2.Response(200, json=account_payload(name='INDIVIDUAL' if len(calls) == 1 else 'TEAM'))
    resolver = AccountPlanResolver(AuthConfig(enabled=True), transport=httpx2.MockTransport(upstream), clock=lambda: now[0])
    try:
        assert await resolver.resolve(snapshot()) == 'Pro'
        assert await resolver.resolve(snapshot(revision='two')) == 'Team'
        assert await resolver.resolve(snapshot(organization='other')) == 'Team'
        assert await resolver.resolve(snapshot(workspace='other')) == 'Team'
        now[0] = 301
        assert await resolver.resolve(snapshot()) == 'Team'
        assert len(calls) == 5
        for number in range(40):
            await resolver.resolve(snapshot(revision=str(number)))
        assert len(resolver._cache) <= 32
    finally:
        await resolver.close()
    assert not resolver._cache
    assert await resolver.resolve(snapshot()) is None


@pytest.mark.anyio
async def test_concurrent_lookup_coalesces_and_one_cancel_preserves_other_waiter():
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def upstream(request):
        calls.append(request)
        started.set()
        await release.wait()
        return httpx2.Response(200, json=account_payload())
    resolver = AccountPlanResolver(AuthConfig(enabled=True), transport=httpx2.MockTransport(upstream))
    first = asyncio.create_task(resolver.resolve(snapshot()))
    await started.wait()
    second = asyncio.create_task(resolver.resolve(snapshot()))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == 'Pro'
    assert len(calls) == 1
    await resolver.close()


@pytest.mark.anyio
async def test_last_waiter_cancel_and_close_release_owned_http():
    started, cancelled = asyncio.Event(), asyncio.Event()
    class Transport(httpx2.AsyncBaseTransport):
        closed = False
        async def handle_async_request(self, request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async def aclose(self):
            self.closed = True
    transport = Transport()
    resolver = AccountPlanResolver(AuthConfig(enabled=True), transport=transport)
    task = asyncio.create_task(resolver.resolve(snapshot()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert not resolver._inflight
    await resolver.close()
    assert transport.closed


@pytest.mark.anyio
async def test_lookup_timeout_is_bounded(monkeypatch):
    import mvibe_auth.account_plan as account
    account.build_ssl_context()
    monkeypatch.setattr(account, 'ACCOUNT_TIMEOUT_SECONDS', 0.01)
    async def upstream(request):
        await asyncio.Event().wait()
    resolver = AccountPlanResolver(AuthConfig(enabled=True), transport=httpx2.MockTransport(upstream))
    try:
        assert await asyncio.wait_for(resolver.resolve(snapshot()), 0.5) is None
    finally:
        await resolver.close()


@pytest.mark.parametrize('plan', [None, 'Team'])
def test_account_plan_owns_caller_metadata(plan):
    from mvibe_auth.request_metadata import attach_request_metadata
    result = json.loads(attach_request_metadata(b'{"metadata":{"user_plan":"Pro","custom":"kept"}}', user_plan=plan))
    assert result['metadata'].get('user_plan') == plan
    assert result['metadata']['custom'] == 'kept'


@pytest.mark.anyio
@pytest.mark.parametrize(('name', 'plan'), [('INDIVIDUAL', 'Pro'), ('TEAM', 'Team'), ('UNKNOWN', None), ('UNAVAILABLE', None)])
async def test_proxy_final_request_uses_account_lookup_before_inference(name, plan):
    import proxy
    events, sent = [], []
    class Session:
        config = AuthConfig(enabled=True)
        async def status(self):
            return {'signed_in': True, 'config_match': True}
        async def resolve(self, target):
            return snapshot()
        async def close(self):
            events.append('closed')
    def account(request):
        events.append('account')
        assert str(request.url) == 'https://console.mistral.ai/api/vibe/account-context'
        return httpx2.Response(503 if name == 'UNAVAILABLE' else 200, json=account_payload(name=name))
    def inference(request):
        events.append('inference')
        sent.append(request)
        return httpx2.Response(200, json={'choices': []})
    def client_factory(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.MockTransport(inference))
    app = proxy.create_app(Session, client_factory,
        account_plan_factory=lambda config: AccountPlanResolver(config, transport=httpx2.MockTransport(account)))
    payload = {'messages': [{'role': 'user', 'content': 'synthetic'}],
        'tools': [{'function': {'parameters': {'type': 'object'}}}],
        'metadata': {'user_plan': 'Pro', 'custom': 'kept'}}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            for _ in range(2):
                assert (await client.post('/v1/chat/completions', json=payload,
                    headers={'Authorization': 'Bearer caller-secret'})).status_code == 200
    assert events == ['account', 'inference', 'inference', 'closed']
    for request in sent:
        body = json.loads(request.content)
        assert body['metadata'].get('user_plan') == plan
        assert body['metadata']['custom'] == 'kept'
        assert body['messages'] == payload['messages']
        assert body['tools'] == payload['tools']
        assert request.headers['authorization'] == 'Bearer synthetic-id'


@pytest.mark.anyio
@pytest.mark.parametrize(('name', 'plan'), [('INDIVIDUAL', 'Pro'), ('TEAM', 'Team'), ('UNKNOWN', None), ('UNAVAILABLE', None)])
async def test_bridge_resolve_derives_plan_and_legacy_resolve_performs_no_lookup(name, plan):
    from mvibe_auth import bridge
    requests, replies, sent = asyncio.Queue(), asyncio.Queue(), []
    class Session:
        config = AuthConfig(enabled=True)
        closed = False
        async def resolve(self, target):
            return snapshot()
        async def close(self):
            self.closed = True
    def account(request):
        sent.append(request)
        return httpx2.Response(503 if name == 'UNAVAILABLE' else 200, json=account_payload(name=name))
    async def read(limit):
        return await requests.get()
    async def write(frame):
        replies.put_nowait(json.loads(frame))
    session = Session()
    task = asyncio.create_task(bridge.serve(read, write, session_factory=lambda: session,
        account_plan_factory=lambda config: AccountPlanResolver(config, transport=httpx2.MockTransport(account))))
    async def resolve(id, context=None):
        params = {'target_url': snapshot().target}
        if context is not None:
            params['request_context'] = context
        requests.put_nowait(json.dumps(dict(version=1, id=id, method='resolve', params=params)).encode() + b'\n')
        reply = await asyncio.wait_for(replies.get(), 5)
        assert reply['ok'], reply
        return reply['result']
    try:
        assert 'metadata' not in await resolve('legacy')
        assert not sent
        for id in ('one', 'two'):
            result = await resolve(id, {'session_id': id})
            assert result['metadata'].get('user_plan') == plan
            assert result['metadata']['session_id'] == id
            assert result['headers']['Authorization'] == 'Bearer synthetic-id'
        assert len(sent) == 1
    finally:
        requests.put_nowait(b'')
        await asyncio.wait_for(task, 5)
    assert session.closed


def test_private_context_cannot_supply_account_plan():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RequestContext(user_plan='Pro')


@pytest.mark.anyio
async def test_close_cancels_pending_response_body_and_closes_stream():
    reading = asyncio.Event()
    class Stream(httpx2.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            reading.set()
            await asyncio.Event().wait()
            yield b''
        async def aclose(self):
            self.closed = True
    stream = Stream()
    resolver = AccountPlanResolver(AuthConfig(enabled=True), transport=httpx2.MockTransport(
        lambda request: httpx2.Response(200, stream=stream)))
    task = asyncio.create_task(resolver.resolve(snapshot()))
    await reading.wait()
    await resolver.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed
    assert not resolver._inflight


@pytest.mark.anyio
async def test_connection_failure_is_optional_and_negative_cache_expires():
    now, sent = [0.0], []
    def upstream(request):
        sent.append(request)
        if len(sent) == 1:
            raise httpx2.ConnectError('synthetic-token-must-not-be-logged')
        return httpx2.Response(200, json=account_payload())
    resolver = AccountPlanResolver(AuthConfig(enabled=True), transport=httpx2.MockTransport(upstream), clock=lambda: now[0])
    try:
        assert await resolver.resolve(snapshot()) is None
        assert await resolver.resolve(snapshot()) is None
        assert len(sent) == 1
        now[0] = 31
        assert await resolver.resolve(snapshot()) == 'Pro'
        assert len(sent) == 2
    finally:
        await resolver.close()
