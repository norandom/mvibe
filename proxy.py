# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "fastapi", "uvicorn", "authlib==1.9.1", "httpx2==2.16.0",
#   "httpx", "joserfc==1.7.7", "pydantic==2.13.4", "keyring==25.7.0",
#   "certifi", "truststore", "anyio", "filelock", "pydantic-settings", "python-dotenv",
# ]
# ///
"""Run with: uv run --script proxy.py"""
from __future__ import annotations

import argparse
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
import os
import webbrowser

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from mvibe_auth.api import AuthConfig, AuthError, AuthFailure, AuthSession
from mvibe_auth.http import VibeAsyncHTTPClient
import uvicorn

def _default_auth_factory() -> AuthSession:
    # Choosing the OIDC-only proxy opts in, while explicit disablement wins.
    config = AuthConfig.from_env()
    if 'VIBE_CLI_OIDC_ENABLED' not in os.environ:
        config = replace(config, enabled=True)
    return AuthSession(config)


async def login(session: AuthSession, *, force: bool = False) -> None:
    """The caller presents URLs; the shared library owns the login lifecycle."""
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
) -> FastAPI:
    """Construct an inert app; lifespan owns the public session and HTTP client."""
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        session = auth_factory()
        try:
            state = await session.status()
            if force_login or not (state['signed_in'] and state['config_match']):
                await login(session, force=force_login)
            async with client_factory(auth_config=session.config, timeout=600,
                                      follow_redirects=False) as client:
                app.state.auth = session
                app.state.client = client
                yield
        finally:
            await session.close()

    app = FastAPI(lifespan=lifespan)
    app.state.force_login = force_login

    @app.get('/health')
    async def health() -> dict[str, str]:
        return {'status': 'ok'}

    @app.api_route('/v1/{path:path}', methods=['GET', 'POST'], response_model=None)
    async def proxy(path: str, request: Request) -> JSONResponse | StreamingResponse:
        if (request.method, path) not in {
            ('GET', 'models'), ('POST', 'chat/completions'), ('POST', 'embeddings'),
        }:
            return JSONResponse({'error': {'message': 'Unsupported endpoint'}}, status_code=404)
        try:
            session = request.app.state.auth
            url = session.config.audience.rstrip('/') + '/v1/' + path
            snapshot = await session.resolve(url)
            headers = dict(snapshot.headers)
            headers['Content-Type'] = 'application/json'
            client = request.app.state.client
            upstream = await client.send(client.build_request(
                request.method, url, params=request.query_params,
                headers=headers, content=await request.body()), stream=True)
        except AuthError:
            return JSONResponse(
                {'error': {'message': 'Vibe authentication failed; run proxy.py --login'}},
                status_code=401)
        except Exception:
            return JSONResponse({'error': {'message': 'Upstream connection failed'}}, status_code=502)

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(chunks(), status_code=upstream.status_code, headers={
            'Content-Type': upstream.headers.get('content-type', 'application/json'),
            'Cache-Control': 'no-cache',
        })

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
    parser.add_argument('--port', type=_port, default=8765)
    parser.add_argument('--login', action='store_true', help='Sign in again before serving')
    args = parser.parse_args(argv)
    uvicorn.run(create_app(force_login=args.login), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
