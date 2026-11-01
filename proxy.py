# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "fastapi", "uvicorn", "authlib==1.9.1", "httpx2==2.16.0",
#   "httpx", "joserfc==1.7.7", "pydantic==2.13.4", "keyring==25.7.0",
#   "cryptography==50.0.2", "certifi", "truststore", "anyio", "filelock", "pydantic-settings", "python-dotenv",
#   "structlog==25.5.0",
# ]
# ///
"""Run with: uv run --script proxy.py"""
from __future__ import annotations

from mvibe_auth.request_metadata import attach_request_metadata
from mvibe_auth.account_plan import AccountPlanResolver

import argparse
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
import os
import webbrowser

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from urllib.parse import urlencode
from mvibe_proxy.streaming import ProxyStreamOwner
from mvibe_proxy.diagnostics import ProxyDiagnostics, RequestDiagnostic, configure_stdout, disable_stdout
from mvibe_auth.api import AuthConfig, AuthError, AuthFailure, AuthSession
from mvibe_auth.http import VibeAsyncHTTPClient
import uvicorn

_API_DESCRIPTION = (
    'Local Vibe OIDC proxy supporting models, chat/completions and embeddings. '
    'Raw request payloads are forwarded without local OpenAI body validation. '
    'The agent executes tools and sends correlated tool results in subsequent messages; '
    'the proxy is neither a tool executor nor an MCP server. The direct OpenCode plugin '
    'bypasses this proxy. Responses and other OpenAI APIs are unsupported. '
    'Caller API keys are not required or used: the local proxy owns Vibe OIDC authentication. '
    'Responses include a generated X-Request-ID for local diagnostic correlation.'
)
_OBJECT = {'type': 'object', 'additionalProperties': True}
_ERROR = {'type': 'object', 'properties': {'error': {'type': 'object', 'properties': {
    'message': {'type': 'string'}}, 'additionalProperties': True}}, 'additionalProperties': True}
_TOOL_CALL = {'type': 'object', 'properties': {
    'id': {'type': 'string'}, 'type': {'type': 'string', 'example': 'function'},
    'function': {'type': 'object', 'properties': {'name': {'type': 'string'},
        'arguments': {'type': 'string', 'description': 'JSON encoded tool arguments for the agent.'}},
        'additionalProperties': True}}, 'additionalProperties': True}
_MESSAGE = {'type': 'object', 'properties': {'role': {'type': 'string'},
    'content': {}, 'tool_call_id': {'type': 'string'},
    'tool_calls': {'type': 'array', 'items': _TOOL_CALL}}, 'additionalProperties': True}


def _operation(kind: str) -> dict:
    """Documentation only: no model binding or runtime payload narrowing."""
    result = {'responses': {
        '200': {'description': 'Upstream response forwarded without replay.', 'content': {
            'application/json': {'schema': _OBJECT}}},
        '401': {'description': 'Local OIDC failure: Vibe authentication failed; run proxy.py --login. Upstream 401 is forwarded.',
                'content': {'application/json': {'schema': _ERROR}}},
        '502': {'description': 'Local acquisition failure: Upstream connection failed. Upstream 502 is forwarded.',
                'content': {'application/json': {'schema': _ERROR}}},
        '404': {'description': 'Unsupported endpoint (local error.message), or upstream response.',
                'content': {'application/json': {'schema': _ERROR}}},
        'default': {'description': 'Other upstream statuses and bodies are forwarded. After headers, stream errors terminate the stream without replacement JSON.'},
    }}
    if kind == 'models':
        result['responses']['200']['content']['application/json']['schema'] = {
            **_OBJECT, 'properties': {'object': {'type': 'string'}, 'data': {'type': 'array', 'items': _OBJECT}}}
        return result
    properties = {'model': {'type': 'string'}}
    if kind == 'chat':
        properties.update({'messages': {'type': 'array', 'items': _MESSAGE},
            'tools': {'type': 'array', 'items': {'type': 'object', 'properties': {
                'type': {'type': 'string', 'example': 'function'},
                'function': {'type': 'object', 'properties': {'name': {'type': 'string'},
                    'description': {'type': 'string'}, 'parameters': _OBJECT}, 'additionalProperties': True}}, 'additionalProperties': True}},
            'tool_choice': {}, 'stream': {'type': 'boolean'}})
        result['responses']['200']['content']['application/json']['schema'] = {
            **_OBJECT, 'properties': {'choices': {'type': 'array', 'items': {
                **_OBJECT, 'properties': {'message': _MESSAGE, 'delta': _MESSAGE,
                    'finish_reason': {'type': ['string', 'null']}}}}}}
        result['responses']['200']['content']['text/event-stream'] = {'schema': {'type': 'string'},
            'example': 'data: {"choices":[{"delta":{"tool_calls":[]}}]}\n\ndata: [DONE]\n\n'}
        result['responses']['200']['description'] += ' With stream=true, SSE data frames carry incremental content/tool_calls and may end with [DONE]; chunks are forwarded as received.'
    else:
        properties.update({'input': {}, 'encoding_format': {'type': 'string'}, 'dimensions': {'type': 'integer'}})
        result['responses']['200']['content']['application/json']['schema'] = {
            **_OBJECT, 'properties': {'data': {'type': 'array', 'items': {
                **_OBJECT, 'properties': {'embedding': {'type': 'array', 'items': {'type': 'number'}}, 'index': {'type': 'integer'}}}}}}
    result['requestBody'] = {'description': 'Extensible upstream payload; this schema documents common fields only. No local body validation.',
        'content': {'application/json': {'schema': {**_OBJECT, 'properties': properties}}}}
    return result

def _default_auth_factory() -> AuthSession:
    # Choosing the OIDC-only proxy opts in, while explicit disablement wins.
    config = AuthConfig.from_env()
    if 'VIBE_CLI_OIDC_ENABLED' not in os.environ:
        config = replace(config, enabled=True)
    return AuthSession(config)


async def login(session: AuthSession, *, force: bool = False) -> None:
    """The caller presents URLs; the shared library owns the login lifecycle."""
    from mvibe_auth.existing_vibe import ExistingVibeSession
    if isinstance(session, ExistingVibeSession):
        await session.resolve(session.config.audience.rstrip('/') + '/v1/models')
        return
    handle = await session.start_login(force=force)
    if handle.authorization_url:
        print(f"Sign in: {handle.authorization_url}", flush=True)
        webbrowser.open(handle.authorization_url)
    outcome = await session.wait_login(handle.attempt_id)
    if isinstance(outcome, AuthFailure):
        raise AuthError(outcome)


def create_app(
    auth_factory: Callable[[], AuthSession] = _default_auth_factory,
    client_factory: Callable[..., VibeAsyncHTTPClient] = VibeAsyncHTTPClient,
    *, force_login: bool = False,
    account_plan_factory: Callable[[AuthConfig], AccountPlanResolver] = AccountPlanResolver,
) -> FastAPI:
    """Construct an inert app; lifespan owns the public session and HTTP client."""
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        session = auth_factory()
        account_plan = account_plan_factory(session.config)
        try:
            state = await session.status()
            if force_login or not (state['signed_in'] and state['config_match']):
                await login(session, force=force_login)
            client = client_factory(auth_config=session.config, timeout=600,
                                    follow_redirects=False)
            await client.__aenter__()
            try:
                app.state.auth = session
                app.state.client = client
                app.state.stream_owner = ProxyStreamOwner(client)
                app.state.account_plan = account_plan
                yield
            finally:
                await app.state.stream_owner.shutdown()
        finally:
            try:
                await account_plan.close()
            finally:
                await session.close()

    app = FastAPI(lifespan=lifespan, title='Vibe local proxy', description=_API_DESCRIPTION)
    app.add_middleware(ProxyDiagnostics)
    app.state.force_login = force_login

    @app.get('/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok'}

    @app.api_route('/v1/{path:path}', methods=['GET', 'POST'], response_model=None, include_in_schema=False)
    async def proxy(path: str, request: Request) -> Response:
        diagnostic = request.scope.get('mvibe.diagnostic') or RequestDiagnostic()
        if (request.method, path) not in {
            ('GET', 'models'), ('POST', 'chat/completions'), ('POST', 'embeddings'),
        }:
            diagnostic.failure('unsupported')
            return JSONResponse({'error': {'message': 'Unsupported endpoint'}}, status_code=404)
        async def acquire():
            session = request.app.state.auth
            url = session.config.audience.rstrip('/') + '/v1/' + path
            snapshot = await session.resolve(url)
            headers = dict(snapshot.headers)
            headers['Content-Type'] = 'application/json'
            # HTTPX QueryParams groups keys; encode ordered Starlette pairs
            # directly into the URL to preserve interleaved repeated values.
            query = urlencode(request.query_params.multi_items())
            target = url + ('?' + query if query else '')
            client = request.app.state.client
            body = await request.body()
            if path == 'chat/completions':
                plan = await request.app.state.account_plan.resolve(snapshot)
                body = attach_request_metadata(body, user_plan=plan)
            return await client.send(client.build_request(
                request.method, target, headers=headers,
                content=body), stream=True, follow_redirects=False)

        return request.app.state.stream_owner.response(acquire, diagnostic)

    # Registered before the catch-all to expose useful operations while retaining
    # its legacy fallback behavior for wrong methods and unknown endpoints.
    fallback = app.router.routes.pop()

    @app.get('/v1/models', response_model=None, openapi_extra=_operation('models'))
    async def models(request: Request) -> Response:
        return await proxy('models', request)

    @app.post('/v1/chat/completions', response_model=None, openapi_extra=_operation('chat'))
    async def chat_completions(request: Request) -> Response:
        return await proxy('chat/completions', request)

    @app.post('/v1/embeddings', response_model=None, openapi_extra=_operation('embeddings'))
    async def embeddings(request: Request) -> Response:
        return await proxy('embeddings', request)

    app.router.routes.append(fallback)

    return app


app = create_app()


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('Port must be an integer from 1 to 65535') from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('Port must be an integer from 1 to 65535')
    return port


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Local OpenAI-compatible proxy using Vibe OIDC')
    parser.add_argument('--auth-source', choices=['oidc', 'existing-vibe'], default='oidc', help='Select experimental OIDC or existing native Vibe credentials')
    parser.add_argument('--port', type=_port, default=8765)
    parser.add_argument('--login', action='store_true', help='Sign in again before serving')
    parser.add_argument('--log', action='store_true', help='Emit safe structured request diagnostics to stdout (default: off)')
    args = parser.parse_args(argv)
    if args.log:
        configure_stdout()
    else:
        disable_stdout()
    if args.auth_source == 'existing-vibe':
        from mvibe_auth.existing_vibe import ExistingVibeSession
        selected_app = create_app(auth_factory=ExistingVibeSession, force_login=args.login)
    else:
        selected_app = create_app(force_login=args.login)
    uvicorn.run(selected_app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
