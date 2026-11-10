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
from starlette.requests import ClientDisconnect
import json
from urllib.parse import urlencode
from mvibe_proxy import compat
from mvibe_proxy.streaming import ClientDisconnected, ProxyStreamOwner
from mvibe_proxy.diagnostics import GateRecord, ProxyDiagnostics, RequestDiagnostic, configure_stdout, disable_stdout, configure_exporter, clear_exporter, emit_econ_warm
from mvibe_proxy.econ import EconConfig, EconConfigError, EconShunt, McpError, StdioMcpClient
from mvibe_proxy.observability import ObserveConfig, ObserveConfigError, OpenObserveExporter, start_telemetry, inject_traceparent
from mvibe_proxy.ratelimit import RateLimitConfig, RateLimitConfigError, UpstreamGate
from mvibe_proxy.stats import UsageObserver
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


# A gate key comes from the client's model name and reaches the hold warning.
_GATE_KEY_LIMIT = 128


def key_for(path: str, model: str | None) -> str:
    """The rate-limit gate key of one upstream request.

    `models` for the model list, which is never paced (Req 3.5); otherwise the
    model the request names, else `default`. Only the model's printable
    characters are kept, at most 128 of them, so a client-supplied name can
    never start a log line of its own."""
    if path == 'models':
        return 'models'
    if isinstance(model, str):
        printable = ''.join(char for char in model if char.isprintable())[:_GATE_KEY_LIMIT]
        if printable:
            return printable
    return 'default'


def _has_tool_result(payload: dict) -> bool:
    """Cheap Req 4.6 pre-check: economics only ever applies to bodies carrying
    tool-result messages; no parse, no hashing, no warm-up."""
    messages = payload.get('messages')
    if not isinstance(messages, list):
        return False
    return any(isinstance(message, dict) and message.get('role') == 'tool'
               for message in messages)


async def _read_body(request: Request, diagnostic: RequestDiagnostic) -> bytes | None:
    """The request body, or None when the client left while it was being read.

    That disconnect is ordinary, not a server error: it records `cancelled`,
    and the route returns an empty response without contacting the upstream."""
    try:
        return await request.body()
    except ClientDisconnect:
        diagnostic.failure('cancelled')
        return None


_ECON_HOOK_LOG = logging.getLogger('mvibe-proxy.econ')
_RATE_LIMIT_LOG = logging.getLogger('mvibe.proxy')


def _warn_rate_limit_hold(key: str, wait_s: float) -> None:
    """One warning as each rate-limit hold starts: the gate key and the wait, nothing else.

    The key is rendered quoted (`%r`), so a space or `=` in a client-supplied
    model name stays inside one token and cannot forge a `key=value` pair."""
    _RATE_LIMIT_LOG.warning('proxy_rate_limit_hold model=%r wait_s=%.1f', key, wait_s)


def _default_gate(config: RateLimitConfig) -> UpstreamGate:
    """The serving gate: real clocks and jitter, warning as each hold starts."""
    return UpstreamGate(config, warn=_warn_rate_limit_hold)


def create_app(
    auth_factory: Callable[[], AuthSession | ExistingVibeSession] = _default_auth_factory,
    client_factory: Callable[..., VibeAsyncHTTPClient] = VibeAsyncHTTPClient,
    *, force_login: bool = False,
    account_plan_factory: Callable[[AuthConfig], AccountPlanResolver] = AccountPlanResolver,
    observe_config: ObserveConfig | None = None,
    econ_config: EconConfig | None = None,
    econ_client: StdioMcpClient | None = None,
    rate_limit_config: RateLimitConfig | None = None,
    gate_factory: Callable[[RateLimitConfig], UpstreamGate] = _default_gate,
) -> FastAPI:
    """Construct an inert app; lifespan owns the public session and HTTP client.

    With ``econ_config`` the lifespan also owns the economics shunt and its
    supervised ``mmhmem stdio`` child (started at startup, stopped on
    shutdown); ``econ_client`` replaces the spawned child for injected-stream
    tests. Without it nothing economics-related is ever created.

    The lifespan builds the upstream rate-limit gate as ``app.state.rate_gate``
    with ``gate_factory(rate_limit_config)``. Without ``rate_limit_config`` the
    gate gets the defaults, which pace nothing and only hold and re-send on a
    429; the environment is never read here (``main`` owns it)."""
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
                gate_config = (rate_limit_config if rate_limit_config is not None
                               else RateLimitConfig.from_env({}))
                app.state.rate_limit_config = gate_config
                app.state.rate_gate = gate_factory(gate_config)
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
    app.state.rate_limit_config = None
    app.state.rate_gate = None

    @app.get('/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok'}

    def usage_factory(diagnostic: RequestDiagnostic, target: str):
        """The owned response's observe_factory for inference targets (stats Req 1.1-1.3).

        Built after acquisition, so the upstream content type and the model the
        body parse recorded on the diagnostic are known; it taps the raw upstream
        bytes before any adapter transform and never alters them. Model-list
        targets carry no usage and get no observer."""
        if target == 'models':
            return None

        def factory(upstream):
            return UsageObserver(content_type=upstream.headers.get('content-type'),
                                 model=diagnostic.model).as_callable(diagnostic)
        return factory

    def make_acquire(request: Request, path: str, body: bytes | None = None, diagnostic=None):
        """Shared upstream acquisition for any /v1 target, through the rate-limit gate.

        The body is parsed once, before the gate, for the gate key and the
        diagnostic model. Every attempt the gate makes resolves a fresh
        credential snapshot and assembles its own headers (Req 2.8); the first
        attempt also runs metadata attribution and economics with its snapshot,
        and every attempt sends those same prepared bytes."""
        async def acquire():
            state = request.app.state
            session = state.auth
            url = session.config.audience.rstrip('/') + '/v1/' + path
            # HTTPX QueryParams groups keys; encode ordered Starlette pairs
            # directly into the URL to preserve interleaved repeated values.
            query = urlencode(request.query_params.multi_items())
            target = url + ('?' + query if query else '')
            raw = body if body is not None else await request.body()
            parsed = None
            model = None
            if path == 'chat/completions':
                # The single strict parse feeds the gate key, the model field
                # and the attribution below; the tolerant fallback keeps model
                # extraction working for bodies that will be relayed raw.
                parsed = parse_request_body(raw)
                found = parsed.get('model') if parsed is not None else _request_model(raw)
                model = found if isinstance(found, str) else None
                if diagnostic is not None:
                    diagnostic.model = model
            elif path != 'models':
                model = _request_model(raw)
                if diagnostic is not None and model is not None:
                    diagnostic.model = model
            prepared: bytes | None = None

            async def prepare(snapshot) -> bytes:
                """The bytes every attempt sends, attributed and economised once."""
                nonlocal parsed
                if path != 'chat/completions':
                    return raw
                payload = raw
                plan = await state.account_plan.resolve(snapshot)
                attributed = (None if parsed is None
                              else attach_request_metadata_dict(parsed, user_plan=plan))
                # Only the prepared bytes stay with a held request (Req 6.3).
                parsed = None
                if attributed is not None:
                    shunt = getattr(state, 'econ', None)
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
                return payload

            async def attempt():
                nonlocal prepared
                telemetry = getattr(state, 'telemetry', None)
                if telemetry is not None:
                    with telemetry.tracer.start_as_current_span('auth.resolve'):
                        snapshot = await session.resolve(url)
                else:
                    snapshot = await session.resolve(url)
                headers = dict(snapshot.headers)
                if telemetry is not None:
                    inject_traceparent(headers)
                headers['Content-Type'] = 'application/json'
                if prepared is None:
                    prepared = await prepare(snapshot)
                client = state.client
                return await client.send(client.build_request(
                    request.method, target, headers=headers,
                    content=prepared), stream=True, follow_redirects=False)

            gate = state.rate_gate
            record = GateRecord()
            if diagnostic is not None:
                diagnostic.rate_limit = record
            # Exceptions from attempt (AuthError, transport errors) leave the
            # gate unchanged, so the callers' 401/502 handling still applies.
            return await gate.run(key_for(path, model), attempt,
                                  arrived=gate.now(), record=record)
        return acquire

    @app.api_route('/v1/{path:path}', methods=['GET', 'POST'], response_model=None, include_in_schema=False)
    async def proxy(path: str, request: Request) -> Response:
        diagnostic = request.scope.get('mvibe.diagnostic') or RequestDiagnostic()
        if (request.method, path) not in {
            ('GET', 'models'), ('POST', 'chat/completions'), ('POST', 'embeddings'),
        }:
            diagnostic.failure('unsupported')
            return JSONResponse({'error': {'message': 'Unsupported endpoint'}}, status_code=404)
        # Read the body before the owned response watches receive() for a
        # disconnect; make_acquire's own request.body() is then a cache hit.
        if await _read_body(request, diagnostic) is None:
            return Response(status_code=204)
        return request.app.state.stream_owner.response(
            make_acquire(request, path, diagnostic=diagnostic), diagnostic,
            observe_factory=usage_factory(diagnostic, path))

    def adapter_post(dialect: str, *, target: str, translate_request, make_transform, inspect=None):
        """POST route translating an adapter dialect body to the upstream /v1 target."""
        async def handler(request: Request) -> Response:
            diagnostic = request.scope.get('mvibe.diagnostic') or RequestDiagnostic()
            raw = await _read_body(request, diagnostic)
            if raw is None:
                return Response(status_code=204)
            try:
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
                # The body is read, so the owner watches receive() while the
                # request is acquired, held included: a client leaving cancels
                # it before anything more reaches the upstream (Req 2.4).
                upstream = await request.app.state.stream_owner.acquire_watching(
                    request.receive, acquire, diagnostic)
            except ClientDisconnected:
                # Recorded as `cancelled`; the client is gone, so nothing to say.
                return Response(status_code=204)
            except Exception as error:
                # A failing receive() arrives wrapped by the watcher's task group;
                # such a group is an auth failure only when every member is one.
                auth = (error.split(AuthError)[1] is None if isinstance(error, ExceptionGroup)
                        else isinstance(error, AuthError))
                if auth:
                    diagnostic.failure('auth')
                    return JSONResponse({'error': {'message': 'Vibe authentication failed; run proxy.py --login'}}, status_code=401)
                # Transport failures, StreamAborted on retirement, a failing receive().
                diagnostic.failure('connection')
                return JSONResponse({'error': {'message': 'Upstream connection failed'}}, status_code=502)
            if upstream.status_code >= 400:
                await upstream.aclose()
                diagnostic.upstream(upstream.status_code)
                status, payload = compat.adapter_error(dialect, upstream.status_code,
                                                       compat.upstream_error_hint(upstream.status_code))
                # The dialect body stays curated (Req 2.6); only a 429 carries
                # the upstream's rate-limit guidance headers (Req 6.4).
                guidance = None
                if upstream.status_code == 429:
                    guidance = {name.decode('latin-1').lower(): value.decode('latin-1')
                                for name, value in upstream.headers.raw
                                if name.lower() == b'retry-after'
                                or name.lower().startswith(b'x-ratelimit-')}
                return JSONResponse(payload, status_code=status, headers=guidance)
            if inspect is not None:
                early = await inspect(upstream, adapter_body)
                if early is not None:
                    await upstream.aclose()
                    return early
            transform = make_transform(upstream_body, adapter_body)

            async def replay():
                return upstream
            return request.app.state.stream_owner.response(
                replay, diagnostic, transform, observe_factory=usage_factory(diagnostic, target))
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
            # Consumed before the disconnect watcher starts.
            if await _read_body(request, diagnostic) is None:
                return Response(status_code=204)
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
        # Consumed before the disconnect watcher starts.
        if await _read_body(request, diagnostic) is None:
            return Response(status_code=204)
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
    rate_limit_config = None
    try:
        rate_limit_config = RateLimitConfig.from_env()
    except RateLimitConfigError as error:
        # Rate limiting is always on (reactive at least), so an invalid
        # MVIBE_RATE_LIMIT_* setting always fails fast (Req 3.4).
        parser.exit(2, f'error: {error}\n')
    if args.auth_source == 'oidc':
        selected_app = create_app(auth_factory=_oidc_auth_factory, force_login=args.login,
                                  observe_config=observe_config, econ_config=econ_config,
                                  rate_limit_config=rate_limit_config)
    else:
        selected_app = create_app(force_login=args.login, observe_config=observe_config,
                                  econ_config=econ_config, rate_limit_config=rate_limit_config)
    uvicorn.run(selected_app, host=args.host, port=args.port, access_log=False)


if __name__ == '__main__':
    main()
