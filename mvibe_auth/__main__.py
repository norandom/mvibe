"""Explicit executable entry points for installed shared authentication."""
import asyncio
import sys


def main() -> int:
    if sys.argv[1:] == ['login-vibe']:
        from mvibe_auth.login_cli import main_existing
        return main_existing()
    if sys.argv[1:] == ['login']:
        try:
            from mvibe_auth.login_cli import main as login
            return login()
        except (Exception, KeyboardInterrupt):
            sys.stderr.write('Vibe OIDC sign-in setup failed; check the installed mvibe-auth runtime.\n')
            return 1
    if sys.argv[1:] not in (['bridge'], ['bridge-vibe']):
        sys.stderr.write('Usage: <installed Python> -m mvibe_auth bridge|login|bridge-vibe|login-vibe\n')
        return 2
    try:
        from mvibe_auth.bridge import stdio
        if sys.argv[1:] == ['bridge-vibe']:
            from mvibe_auth.existing_vibe import ExistingVibeSession
            asyncio.run(stdio(session_factory=ExistingVibeSession))
        else:
            asyncio.run(stdio())
        return 0
    except (Exception, KeyboardInterrupt):
        if sys.argv[1:] == ['bridge-vibe']:
            sys.stderr.write('Existing Vibe helper failed; check the installed mvibe-auth runtime and sign in through normal Vibe.\n')
        else:
            sys.stderr.write('Vibe OIDC helper failed; check the installed mvibe-auth runtime and configuration.\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
