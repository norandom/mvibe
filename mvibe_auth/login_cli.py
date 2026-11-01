"""Browser sign-in consumer; stdout is OpenCode's non-secret connection marker."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import os
import signal
import sys
import webbrowser

from mvibe_auth import AuthConfig, AuthError, AuthFailure, AuthSession, LoginCompleted, LoginReusedCurrent

CONNECTION_MARKER = 'vibe-oidc-connected-v1'
_FAILURE = 'Vibe OIDC sign-in failed; check the installed mvibe-auth runtime and OIDC configuration, then retry.\n'


def create_session() -> AuthSession:
    config = AuthConfig.from_env()
    if 'VIBE_CLI_OIDC_ENABLED' not in os.environ:
        config = replace(config, enabled=True)
    return AuthSession(config)


async def login(*, session_factory: Callable[[], AuthSession] = create_session,
                open_browser: Callable[[str], bool] | None = None) -> None:
    """Present only the library-owned URL; close owned work before success."""
    session = session_factory()
    attempt = None
    try:
        handle = await session.start_login()
        attempt = handle.attempt_id
        if handle.authorization_url:
            if not (open_browser or webbrowser.open)(handle.authorization_url):
                raise AuthError(AuthFailure('cancelled'))
        outcome = await session.wait_login(attempt)
        if isinstance(outcome, AuthFailure):
            raise AuthError(outcome)
        if isinstance(outcome, LoginReusedCurrent):
            await session.resolve(session.config.audience)
        elif not isinstance(outcome, LoginCompleted):
            raise AuthError(AuthFailure('invalid'))
    except BaseException:
        if attempt is not None:
            try:
                await session.cancel_login(attempt)
            except Exception:
                pass
        raise
    finally:
        await session.close()


async def _run() -> None:
    task = asyncio.current_task()
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_: task.cancel() if task else None)
    try:
        await login()
    finally:
        signal.signal(signal.SIGTERM, previous)


def main() -> int:
    try:
        # Dependency/browser diagnostics must never become connection data.
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            asyncio.run(_run())
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        sys.stderr.write(_FAILURE)
        return 1
    sys.stdout.write(CONNECTION_MARKER)
    return 0


async def login_existing(*, session_factory=None) -> None:
    """Validate the native credential locally; native Vibe owns sign-in."""
    from mvibe_auth.existing_vibe import ExistingVibeSession
    session = (session_factory or ExistingVibeSession)()
    try:
        await session.resolve(session.config.audience.rstrip('/') + '/v1/models')
    finally:
        await session.close()


def main_existing() -> int:
    from mvibe_auth.existing_vibe import NATIVE_SIGN_IN_MESSAGE
    try:
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            asyncio.run(login_existing())
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        sys.stderr.write(NATIVE_SIGN_IN_MESSAGE + '\n')
        return 1
    sys.stdout.write(CONNECTION_MARKER)
    return 0
