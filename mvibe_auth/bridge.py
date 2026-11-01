"""Private protocol v1 dispatcher. Authentication policy belongs to AuthSession.

Only explicit module invocation opts this consumer into OIDC by default; an
explicit disabled setting remains disabled. Imports do not mutate environment.
The Python-only I/O/session injection points are never selectable by wire input.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from importlib.metadata import version
import json
import os
import sys

from mvibe_auth.api import AuthConfig, AuthError, AuthFailure, AuthSession
from mvibe_auth.account_plan import AccountPlanResolver
from mvibe_auth._bridge_protocol import (
    AuthError as WireError, Failure, MAX_FRAME_BYTES, ProtocolError,
    Request, RESULT_MODELS, Success, parse_request,
)

MAX_ACTIVE_REQUESTS = 32
_SETUP_MESSAGE = 'Vibe OIDC setup failed; check the installed mvibe-auth runtime and OIDC configuration.'


def create_session() -> AuthSession:
    config = AuthConfig.from_env()
    if 'VIBE_CLI_OIDC_ENABLED' not in os.environ:
        config = replace(config, enabled=True)
    return AuthSession(config)


def _correlation(frame: bytes) -> str | None:
    """Recover only a valid ID for rejected envelopes, without echoing input."""
    if len(frame) > MAX_FRAME_BYTES:
        return None
    try:
        value = json.loads(frame)
        candidate = value.get('id') if isinstance(value, dict) else None
        return candidate if isinstance(candidate, str) and candidate else None
    except (ValueError, RecursionError):
        return None


async def serve(
    read_frame: Callable[[int], Awaitable[bytes]],
    write_frame: Callable[[bytes], Awaitable[None]],
    *, session_factory: Callable[[], AuthSession] | None = None,
    account_plan_factory: Callable[[AuthConfig], AccountPlanResolver] | None = None,
) -> None:
    """Serve bounded frames with concurrent operations and serialized output.

    A duplicate active ID retires the connection after one invalid-frame reply,
    suppressing the original operation's reply so IDs never become ambiguous.
    Uncorrelatable malformed frames use a fixed protocol ID and retire too.
    Ordinary operations use at most 31 slots; the final slot is reserved for
    login.cancel. Shutdown stops admission and drains rather than adding work.
    """
    factory = session_factory or create_session
    session: AuthSession | None = None
    account_plan: AccountPlanResolver | None = None
    active: dict[str, asyncio.Task[None]] = {}
    suppressed: set[str] = set()
    responding: set[str] = set()
    output_lock = asyncio.Lock()
    resolution_lock = asyncio.Lock()

    async def emit(envelope: Success | Failure, *, suppressible: bool = False) -> None:
        frame = envelope.model_dump_json(exclude_none=True).encode('utf-8') + b'\n'
        if len(frame) > MAX_FRAME_BYTES:
            frame = Failure(version=1, id=envelope.id, ok=False,
                error=WireError(code='frame_too_large', message=str(ProtocolError('frame_too_large')), retryable=False)).model_dump_json().encode() + b'\n'
            # A malicious huge ID can itself exceed the frame allowance.
            if len(frame) > MAX_FRAME_BYTES:
                raise ProtocolError('frame_too_large')
        async with output_lock:
            if suppressible and envelope.id in suppressed:
                return
            if envelope.id in active:
                responding.add(envelope.id)
            await write_frame(frame)

    async def failure(id: str, code: str, message: str, retryable: bool = False, *, suppressible: bool = False) -> None:
        await emit(Failure(version=1, id=id, ok=False,
            error=WireError(code=code, message=message, retryable=retryable)), suppressible=suppressible)

    def owner() -> AuthSession:
        nonlocal session, account_plan
        if session is None:
            session = factory()
            account_plan = (account_plan_factory or AccountPlanResolver)(session.config)
        return session

    async def result(request: Request) -> dict:
        auth = owner()
        match request.method:
            case 'hello':
                return dict(protocol=1, api_version=version('mvibe-auth'), audience=auth.config.audience)
            case 'status':
                return await auth.status()
            case 'login.start':
                handle = await auth.start_login(force=request.params.force)
                return dict(attempt_id=handle.attempt_id, authorization_url=handle.authorization_url,
                            expires_at=handle.expires_at.timestamp())
            case 'login.wait':
                outcome = await auth.wait_login(request.params.attempt_id)
                if isinstance(outcome, AuthFailure):
                    raise AuthError(outcome)
                return dict(outcome=outcome.outcome)
            case 'login.cancel':
                await auth.cancel_login(request.params.attempt_id)
                return dict(cancelled=True)
            case 'resolve':
                async with resolution_lock:
                    snapshot = await auth.resolve(request.params.target_url)
                payload = dict(headers=dict(snapshot.headers), expires_at=snapshot.expires_at.timestamp(),
                               revision=snapshot.revision, target=snapshot.target)
                if request.params.request_context is not None:
                    from mvibe_auth.request_metadata import build_request_metadata
                    plan = await account_plan.resolve(snapshot)
                    payload['metadata'] = build_request_metadata(request.params.request_context, user_plan=plan)
                return payload
        raise ProtocolError('unknown_method')

    async def dispatch(request: Request) -> None:
        try:
            payload = await result(request)
            await emit(Success(version=1, id=request.id, ok=True,
                result=RESULT_MODELS[request.method].model_validate(payload)), suppressible=True)
        except asyncio.CancelledError:
            if request.id not in suppressed:
                error = AuthFailure('cancelled')
                await failure(request.id, error.code, error.message, suppressible=True)
        except AuthError as error:
            safe = error.failure
            # Construction/configuration failures need actionable setup guidance.
            await failure(request.id, safe.code, safe.message if session is not None else _SETUP_MESSAGE, safe.retryable, suppressible=True)
        except Exception:
            await failure(request.id, 'transient', _SETUP_MESSAGE if session is None else 'Vibe authentication is temporarily unavailable; retry.', True, suppressible=True)
        finally:
            active.pop(request.id, None)
            responding.discard(request.id)

    async def close() -> None:
        tasks = tuple(active.items())
        for id, task in tasks:
            # Once a terminal write starts, its bytes may already be visible
            # while to_thread(flush) still owns the pipe. Drain that dispatch
            # with its output lock intact; cancellation could duplicate the
            # reply and release the lock before the actual writer finishes.
            if id not in responding:
                task.cancel()
        if session is not None:
            await session.close()
        await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        if account_plan is not None:
            await account_plan.close()

    try:
        while True:
            frame = await read_frame(MAX_FRAME_BYTES + 1)
            if not frame:
                break
            try:
                request = parse_request(frame)
            except ProtocolError as error:
                id = _correlation(frame)
                duplicate = id in active
                if duplicate:
                    suppressed.add(id)
                if not duplicate or id not in responding:
                    await failure(id or 'invalid-frame', error.code, str(error))
                # Capture duplication before awaiting output: the original may
                # finish its reply while that output await yields the loop.
                if id is None or duplicate or len(frame) > MAX_FRAME_BYTES:
                    break
                continue
            if request.id in active:
                suppressed.add(request.id)
                # A pipe write already started may have delivered the original
                # reply. Never put a second response for that ID on the pipe.
                if request.id not in responding:
                    await failure(request.id, 'invalid_frame', 'Duplicate active bridge request ID.')
                break
            if request.method == 'shutdown':
                await close()
                await emit(Success(version=1, id=request.id, ok=True,
                    result=RESULT_MODELS['shutdown'](closed=True)))
                break
            limit = MAX_ACTIVE_REQUESTS if request.method == 'login.cancel' else MAX_ACTIVE_REQUESTS - 1
            if len(active) >= limit:
                await failure(request.id, 'transient', 'Bridge request limit reached; wait or shut down the helper.', True)
                continue
            active[request.id] = asyncio.create_task(dispatch(request))
            # Give short operations progress even when the input is buffered.
            await asyncio.sleep(0)
    finally:
        await close()


async def stdio() -> None:
    """Portable bounded pipe I/O, including Windows anonymous child pipes."""
    source, destination = sys.stdin.buffer, sys.stdout.buffer

    async def read(limit: int) -> bytes:
        return await asyncio.to_thread(source.readline, limit)

    async def write(frame: bytes) -> None:
        def flush() -> None:
            destination.write(frame)
            destination.flush()
        await asyncio.to_thread(flush)

    # Third-party diagnostic output is not a wire frame or a safe diagnostic.
    # Keep the original binary pipe only in write(); discard incidental output
    # (including worker-thread prints) rather than buffering secret material.
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        await serve(read, write)
