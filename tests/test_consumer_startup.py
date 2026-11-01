"""Public proxy startup evidence; no sockets, browser, keyring or live identity."""
import json
import os
from pathlib import Path

import httpx
import pytest

import proxy
from test_proxy import harness


@pytest.mark.anyio
async def test_public_injected_startup_records_separate_local_evidence(monkeypatch):
    launch = {}
    monkeypatch.setattr(proxy.uvicorn, 'run', lambda app, **kwargs: launch.update(app=app, **kwargs))
    proxy.main([])
    assert (launch['host'], launch['port']) == ('127.0.0.1', 8765)
    app, session, sent, _ = harness()
    payload = b'{"messages":[{"role":"assistant","tool_calls":[{"id":"synthetic-call","type":"function","function":{"name":"synthetic","arguments":"{\\"x\\":1}"}}]}]}'
    async with app.router.lifespan_context(app):
        assert app.state.auth is session
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://local') as client:
            health = await client.get('/health')
            assert health.status_code == 200 and health.json() == {'status': 'ok'}
            assert not sent, 'local liveness must not probe upstream'
            assert (await client.post('/v1/chat/completions', content=payload)).status_code == 200
            import json
            outgoing = json.loads(sent[0].content)
            assert outgoing['messages'] == json.loads(payload)['messages']
            assert outgoing['metadata']['call_source'] == 'vibe_code'
            assert outgoing['metadata']['call_type'] == 'secondary_call'
            assert (await client.post('/v1/responses')).status_code == 404
            assert len(sent) == 1
    assert session.events[-1] == 'close'
    # Report routing is test-only and survives the credential-override fixture reset.
    report = Path(os.environ.get('MVIBE_STARTUP_REPORT', os.environ.get('VIBE_STARTUP_REPORT', 'reports/local-startup.json')))
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        'kind': 'offline-local-startup', 'status': 'passed', 'live': 'unverified',
        'requirements': ['6.1', '6.3', '10.5', '12.10'],
        'checks': dict(default_loopback=True, injected_lifespan=True, local_health=True,
                       tool_payload=True, rejected_route=True, closed=True),
        'scope': 'Injected ASGI lifespan; actual OS listening is not claimed',
    }, indent=2), encoding='utf8')
