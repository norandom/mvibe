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

from mvibe_auth.request_metadata import (attach_request_metadata_dict, parse_request_body,
                                          serialize_request_body)
from mvibe_auth.account_plan import AccountPlanResolver

import argparse
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
import ipaddress
import logging
import os
import re
import sys
import webbrowser

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
import json
from urllib.parse import urlencode
from mvibe_proxy import compat
from mvibe_proxy.streaming import ProxyStreamOwner
from mvibe_proxy.diagnostics import ProxyDiagnostics, RequestDiagnostic, configure_stdout, disable_stdout, configure_exporter, clear_exporter, emit_econ_warm
from mvibe_proxy.econ import EconConfig, EconConfigError, EconShunt, McpError, StdioMcpClient
from mvibe_proxy.observability import ObserveConfig, ObserveConfigError, OpenObserveExporter, start_telemetry, inject_traceparent
from mvibe_auth.api import AuthConfig, AuthError, AuthFailure, AuthSession
from mvibe_auth.existing_vibe import ExistingVibeSession
from mvibe_auth.http import VibeAsyncHTTPClient
import uvicorn

_API_DESCRIPTION = (
    'Local Vibe proxy supporting models, chat/completions and embeddings. '
    'Raw request payloads are forwarded without local OpenAI body validation. '
    'The agent executes tools and sends correlated tool results in subsequent messages; '
    'the proxy is neither a tool executor nor an MCP server. The direct OpenCode plugin '
    'bypasses this proxy. Responses and other OpenAI APIs are unsupported. '
    'Caller API keys are not required or used: the proxy reads existing native Vibe credentials by default. '
    'Experimental OIDC authentication requires explicit selection. '
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
        '401': {'description': 'Local authentication failure: sign in through normal Vibe for the default source; for explicit OIDC run proxy.py --auth-source oidc --login. Upstream 401 is forwarded.',
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

def _default_auth_factory() -> ExistingVibeSession:
    return ExistingVibeSession()


def _oidc_auth_factory() -> AuthSession:
    # Explicit OIDC selection opts in, while explicit disablement wins.
    config = AuthConfig.from_env()
    if 'VIBE_CLI_OIDC_ENABLED' not in os.environ:
        config = replace(config, enabled=True)
    return AuthSession(config)


async def login(session: AuthSession | ExistingVibeSession, *, force: bool = False) -> None:
    """Validate native credentials, or present URLs for explicit OIDC login."""
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


def _request_model(body: bytes) -> str | None:
    """Model id only; the body itself is never logged or exported."""
    try:
        payload = json.loads(body)
    except Exception:
        return None
    if isinstance(payload, dict) and isinstance(payload.get('model'), str):
        return payload['model']
    return None


def _has_tool_result(payload: dict) -> bool:
    """Cheap Req 4.6 pre-check: economics only ever applies to bodies carrying
    tool-result messages; no parse, no hashing, no warm-up."""
    messages = payload.get('messages')
    if not isinstance(messages, list):
        return False
    return any(isinstance(message, dict) and message.get('role') == 'tool'
               for message in messages)


_ECON_HOOK_LOG = logging.getLogger('mvibe-proxy.econ')


def create_app(
    auth_factory: Callable[[], AuthSession | ExistingVibeSession] = _default_auth_factory,
    client_factory: Callable[..., VibeAsyncHTTPClient] = VibeAsyncHTTPClient,
    *, force_login: bool = False,
    account_plan_factory: Callable[[AuthConfig], AccountPlanResolver] = AccountPlanResolver,
    observe_config: ObserveConfig | None = None,
    econ_config: EconConfig | None = None,
    econ_client: StdioMcpClient | None = None,
) -> FastAPI:
    """Construct an inert app; lifespan owns the public session and HTTP client.

    With ``econ_config`` the lifespan also owns the economics shunt and its
    supervised ``mmhmem stdio`` child (started at startup, stopped on
    shutdown); ``econ_client`` replaces the spawned child for injected-stream
    tests. Without it nothing economics-related is ever created."""
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        session = auth_factory()
        account_plan = account_plan_factory(session.config)
        exporter = OpenObserveExporter(observe_config) if observe_config is not None else None
        telemetry = start_telemetry() if observe_config is not None else None
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
                app.state.telemetry = telemetry
                if econ_config is not None:
                    # Economics on: spawn and supervise the mmhmem stdio child
                    # at startup (Amendment 1); econ_client (injected streams)
                    # replaces the spawn in tests. A spawn or handshake failure
                    # fails fast with a sanitized, actionable message.
                    mcp_client = (econ_client if econ_client is not None
                                  else StdioMcpClient(econ_config.mmhmem_bin))
                    try:
                        await mcp_client.start()
                    except Exception as error:
                        try:
                            await mcp_client.shutdown()
                        except Exception:
                            pass
                        detail = (str(error) if isinstance(error, McpError)
                                  else type(error).__name__)
                        raise RuntimeError(
                            'economics startup failed: the mmhmem stdio child from '
                            f'MVIBE_ECON_MMHMEM_BIN={econ_config.mmhmem_bin} could not '
                            f'be started ({detail}); check the binary and its stdio '
                            'subcommand, or unset MVIBE_ECON_MMHMEM_BIN to disable '
                            'economics') from error
                    app.state.econ = EconShunt(econ_config, mcp_client,
                                               emit_warm=emit_econ_warm)
                if exporter is not None:
                    await exporter.start()
                    configure_exporter(exporter)
                yield
            finally:
                if app.state.econ is not None:
                    # Bounded: the warm queue drains (<= 2 s), then the child
                    # closes stdin, waits <= 2 s and is terminated.
                    shunt, app.state.econ = app.state.econ, None
                    await shunt.shutdown()
                await app.state.stream_owner.shutdown()
                if exporter is not None:
                    clear_exporter()
                    await exporter.shutdown()
                if telemetry is not None:
                    telemetry.shutdown()
        finally:
            try:
                await account_plan.close()
            finally:
                await session.close()

    app = FastAPI(lifespan=lifespan, title='Vibe local proxy', description=_API_DESCRIPTION)
    app.add_middleware(ProxyDiagnostics)
    app.state.force_login = force_login
    app.state.telemetry = None
    app.state.econ = None

    @app.get('/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok'}

    def make_acquire(request: Request, path: str, body: bytes | None = None, diagnostic=None):
        """Shared per-request auth snapshot + upstream send for any /v1 target."""
        async def acquire():
            session = request.app.state.auth
            url = session.config.audience.rstrip('/') + '/v1/' + path
            telemetry = getattr(request.app.state, 'telemetry', None)
            if telemetry is not None:
                with telemetry.tracer.start_as_current_span('auth.resolve'):
                    snapshot = await session.resolve(url)
            else:
                snapshot = await session.resolve(url)
            headers = dict(snapshot.headers)
            if telemetry is not None:
                inject_traceparent(headers)
            headers['Content-Type'] = 'application/json'
            # HTTPX QueryParams groups keys; encode ordered Starlette pairs
            # directly into the URL to preserve interleaved repeated values.
            query = urlencode(request.query_params.multi_items())
            target = url + ('?' + query if query else '')
            client = request.app.state.client
            payload = body if body is not None else await request.body()
            if path == 'chat/completions':
                parsed = parse_request_body(payload)
                if diagnostic is not None:
                    # The single strict parse feeds both the model field and the
                    # attribution below; the tolerant fallback keeps model
                    # extraction working for bodies that will be relayed raw.
                    model = parsed.get('model') if parsed is not None else _request_model(payload)
                    diagnostic.model = model if isinstance(model, str) else None
                plan = await request.app.state.account_plan.resolve(snapshot)
                attributed = (None if parsed is None
                              else attach_request_metadata_dict(parsed, user_plan=plan))
                if attributed is not None:
                    shunt = getattr(request.app.state, 'econ', None)
                    if shunt is not None and _has_tool_result(attributed):
                        # Fail-open snapshot (Req 4.5): the attributed bytes
                        # exactly as the metadata attachment produced them —
                        # immune to apply's in-place mutation, so any exception
                        # relays this body. The hook never awaits (Req 4.1) and
                        # runs on the already-attributed object (Req 3.6).
                        attributed_bytes = serialize_request_body(attributed)
                        try:
                            shunt.apply(attributed, diagnostic)
                        except Exception as error:
                            _ECON_HOOK_LOG.warning(
                                'econ apply failed (%s); relaying the original body',
                                type(error).__name__)
                            if diagnostic is not None:
                                diagnostic.econ = {'econ_outcome': 'error'}
                            payload = attributed_bytes
                        else:
                            payload = serialize_request_body(attributed)
                    else:
                        payload = serialize_request_body(attributed)
            return await client.send(client.build_request(
                request.method, target, headers=headers,
                content=payload), stream=True, follow_redirects=False)
        return acquire

    @app.api_route('/v1/{path:path}', methods=['GET', 'POST'], response_model=None, include_in_schema=False)
    async def proxy(path: str, request: Request) -> Response:
        diagnostic = request.scope.get('mvibe.diagnostic') or RequestDiagnostic()
        if (request.method, path) not in {
            ('GET', 'models'), ('POST', 'chat/completions'), ('POST', 'embeddings'),
        }:
            diagnostic.failure('unsupported')
            return JSONResponse({'error': {'message': 'Unsupported endpoint'}}, status_code=404)
        return request.app.state.stream_owner.response(
            make_acquire(request, path, diagnostic=diagnostic), diagnostic)

    def adapter_post(dialect: str, *, target: str, translate_request, make_transform, inspect=None):
        """POST route translating an adapter dialect body to the upstream /v1 target."""
        async def handler(request: Request) -> Response:
            diagnostic = request.scope.get('mvibe.diagnostic') or RequestDiagnostic()
            try:
                raw = await request.body()
                adapter_body = json.loads(raw) if raw else {}
                if not isinstance(adapter_body, dict):
                    raise compat.CompatError('body must be a JSON object')
                upstream_body = translate_request(adapter_body)
                model = upstream_body.get('model')
                if isinstance(model, str):
                    diagnostic.model = model
            except compat.CompatError as error:
                diagnostic.failure('invalid')
                status, payload = compat.adapter_error(dialect, 400, str(error))
                return JSONResponse(payload, status_code=status)
            except ValueError:
                diagnostic.failure('invalid')
                status, payload = compat.adapter_error(dialect, 400, 'body must be valid JSON')
                return JSONResponse(payload, status_code=status)
            acquire = make_acquire(request, target, json.dumps(upstream_body).encode(), diagnostic)
            try:
                upstream = await acquire()
            except AuthError:
                diagnostic.failure('auth')
                return JSONResponse({'error': {'message': 'Vibe authentication failed; run proxy.py --login'}}, status_code=401)
            except Exception:
                diagnostic.failure('connection')
                return JSONResponse({'error': {'message': 'Upstream connection failed'}}, status_code=502)
            if upstream.status_code >= 400:
                await upstream.aclose()
                diagnostic.upstream(upstream.status_code)
                status, payload = compat.adapter_error(dialect, upstream.status_code,
                                                       compat.upstream_error_hint(upstream.status_code))
                return JSONResponse(payload, status_code=status)
            if inspect is not None:
                early = await inspect(upstream, adapter_body)
                if early is not None:
                    await upstream.aclose()
                    return early
            transform = make_transform(upstream_body, adapter_body)

            async def replay():
                return upstream
            return request.app.state.stream_owner.response(replay, diagnostic, transform)
        return handler

    @app.api_route('/api/{path:path}', methods=['GET', 'POST'], response_model=None, include_in_schema=False)
    async def api_catchall(path: str, request: Request) -> Response:
        diagnostic = request.scope.get('mvibe.diagnostic') or RequestDiagnostic()
        diagnostic.failure('unsupported')
        _, payload = compat.adapter_error('ollama', 404, 'Unsupported endpoint')
        return JSONResponse(payload, status_code=404)

    # Registered before the catch-alls to expose useful operations while retaining
    # their legacy fallback behavior for wrong methods and unknown endpoints.
    api_fallback = app.router.routes.pop()   # /api/{path:path}
    fallback = app.router.routes.pop()       # /v1/{path:path}

    @app.get('/v1/models', response_model=None, openapi_extra=_operation('models'))
    async def models(request: Request) -> Response:
        if request.headers.get('anthropic-version'):
            # Anthropic-dialect model list; OpenAI shape is unchanged otherwise.
            diagnostic = request.scope.get('mvibe.diagnostic') or RequestDiagnostic()
            return request.app.state.stream_owner.response(
                make_acquire(request, 'models', b'', diagnostic), diagnostic,
                compat.BufferJson(compat.anthropic_models_list, 'anthropic'))
        return await proxy('models', request)

    @app.post('/v1/chat/completions', response_model=None, openapi_extra=_operation('chat'))
    async def chat_completions(request: Request) -> Response:
        return await proxy('chat/completions', request)

    @app.post('/v1/embeddings', response_model=None, openapi_extra=_operation('embeddings'))
    async def embeddings(request: Request) -> Response:
        return await proxy('embeddings', request)

    @app.post('/v1/messages', response_model=None, openapi_extra={
        'summary': 'Anthropic-compatible messages (translated to chat/completions)',
        'responses': {'200': {'description': 'Anthropic message object, or SSE event stream when stream=true.'}}})
    async def anthropic_messages(request: Request) -> Response:
        return await adapter_post('anthropic', target='chat/completions',
            translate_request=compat.anthropic_messages_request,
            make_transform=lambda upstream_body, _adapter: (
                compat.AnthropicStreamTranslator(upstream_body['model']) if upstream_body.get('stream')
                else compat.BufferJson(compat.anthropic_messages_response, 'anthropic')))(request)

    _OLLAMA_DOC = {'responses': {'200': {'description': 'Ollama-shaped response (NDJSON stream when stream=true).'},
                                  '400': {'description': 'Invalid adapter body.'},
                                  'default': {'description': 'Upstream statuses preserved; error bodies are Ollama-shaped with safe hints.'}}}

    @app.get('/api/version', response_model=None, openapi_extra={'summary': 'Ollama-compatible version', **_OLLAMA_DOC})
    async def ollama_version_route() -> Response:
        return JSONResponse(compat.ollama_version())

    @app.get('/api/tags', response_model=None, openapi_extra={'summary': 'Ollama-compatible model list', **_OLLAMA_DOC})
    async def ollama_tags_route(request: Request) -> Response:
        diagnostic = request.scope.get('mvibe.diagnostic') or RequestDiagnostic()
        return request.app.state.stream_owner.response(
            make_acquire(request, 'models', b'', diagnostic), diagnostic,
            compat.BufferJson(compat.ollama_tags, 'ollama'))

    app.post('/api/chat', response_model=None, openapi_extra={'summary': 'Ollama-compatible chat', **_OLLAMA_DOC})(
        adapter_post('ollama', target='chat/completions',
            translate_request=compat.ollama_chat_request,
            make_transform=lambda upstream_body, _adapter: (
                compat.OllamaStreamTranslator(upstream_body['model'], kind='chat') if upstream_body.get('stream')
                else compat.BufferJson(lambda obj: compat.ollama_chat_response(obj, upstream_body['model']), 'ollama'))))

    app.post('/api/generate', response_model=None, openapi_extra={'summary': 'Ollama-compatible generate', **_OLLAMA_DOC})(
        adapter_post('ollama', target='chat/completions',
            translate_request=compat.ollama_generate_request,
            make_transform=lambda upstream_body, _adapter: (
                compat.OllamaStreamTranslator(upstream_body['model'], kind='generate') if upstream_body.get('stream')
                else compat.BufferJson(lambda obj: compat.ollama_generate_response(obj, upstream_body['model']), 'ollama'))))

    async def show_inspect(upstream, adapter_body):
        raw = await upstream.aread()
        try:
            result = compat.ollama_show(json.loads(raw), adapter_body['model'])
        except compat.CompatError as error:
            _, payload = compat.adapter_error('ollama', 404, str(error))
            return JSONResponse(payload, status_code=404)
        return JSONResponse(result)

    app.post('/api/show', response_model=None, openapi_extra={'summary': 'Ollama-compatible model info', **_OLLAMA_DOC})(
        adapter_post('ollama', target='models',
            translate_request=lambda body: {'model': compat.require_str(body, 'model')},
            make_transform=lambda _upstream, _adapter: None,
            inspect=show_inspect))

    app.post('/api/embed', response_model=None, openapi_extra={'summary': 'Ollama-compatible embeddings', **_OLLAMA_DOC})(
        adapter_post('ollama', target='embeddings',
            translate_request=compat.ollama_embed_request,
            make_transform=lambda _upstream, _adapter: compat.BufferJson(compat.ollama_embed_response, 'ollama')))

    app.router.routes.append(fallback)
    app.router.routes.append(api_fallback)

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


def _host(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if re.fullmatch(r'[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', value):
        return value
    raise argparse.ArgumentTypeError('Host must be an IP address or a hostname')


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == 'localhost'


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Local OpenAI-compatible proxy using existing Vibe authentication by default')
    parser.add_argument('--auth-source', choices=['oidc', 'existing-vibe'], default='existing-vibe', help='Read existing native Vibe credentials (default), or explicitly select experimental OIDC')
    parser.add_argument('--host', type=_host, default='127.0.0.1', help='Bind address (default: 127.0.0.1 loopback; 0.0.0.0 exposes all interfaces and prints a warning, since the proxy has no caller authentication)')
    parser.add_argument('--port', type=_port, default=8765)
    parser.add_argument('--login', action='store_true', help='Validate existing native Vibe credentials before serving; with --auth-source oidc, sign in again')
    parser.add_argument('--log', action='store_true', help='Emit safe structured request diagnostics to stdout (default: off)')
    parser.add_argument('--observe', action='store_true', help='Export allowlisted diagnostics to an OpenObserve-style backend configured by MVIBE_OBSERVE_* env vars (default: off)')
    parser.add_argument('--econ', action='store_true', help='Replace historical file-read tool results with deterministic mmhmem summaries; configured by MVIBE_ECON_* env vars (requires MVIBE_ECON_MMHMEM_BIN; validated at startup)')
    args = parser.parse_args(argv)
    if args.log:
        configure_stdout()
    else:
        disable_stdout()
    if not _is_loopback(args.host):
        print(f'WARNING: binding to {args.host} exposes the proxy beyond loopback. '
              'The proxy has no caller authentication; anyone who can reach the port '
              'can use the selected Vibe credentials.', file=sys.stderr)
    observe_config = None
    try:
        observe_config = ObserveConfig.from_env()
    except ObserveConfigError as error:
        if args.observe:
            # Explicit request with incomplete configuration fails fast.
            parser.exit(2, f'error: {error}\n')
        print(f'WARNING: {error}', file=sys.stderr)
    if args.observe and observe_config is None:
        parser.exit(2, 'error: --observe requires MVIBE_OBSERVE_URL, MVIBE_OBSERVE_USER and MVIBE_OBSERVE_TOKEN\n')
    econ_config = None
    try:
        econ_config = EconConfig.from_env()
    except EconConfigError as error:
        if args.econ:
            # Explicit request with invalid configuration fails fast (Req 1.5).
            parser.exit(2, f'error: {error}\n')
        print(f'WARNING: {error}', file=sys.stderr)
    if args.econ and econ_config is None:
        parser.exit(2, 'error: --econ requires MVIBE_ECON_MMHMEM_BIN (path to the mmhmem binary) to enable token economics\n')
    if args.auth_source == 'oidc':
        selected_app = create_app(auth_factory=_oidc_auth_factory, force_login=args.login,
                                  observe_config=observe_config, econ_config=econ_config)
    else:
        selected_app = create_app(force_login=args.login, observe_config=observe_config,
                                  econ_config=econ_config)
    uvicorn.run(selected_app, host=args.host, port=args.port, access_log=False)


if __name__ == '__main__':
    main()
