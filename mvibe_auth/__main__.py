"""Explicit executable entry points for installed shared authentication."""
import asyncio
import sys


def main() -> int:
    if sys.argv[1:] == ['login']:
        try:
            from mvibe_auth.login_cli import main as login
            return login()
        except (Exception, KeyboardInterrupt):
            sys.stderr.write('Vibe OIDC sign-in setup failed; check the installed mvibe-auth runtime.\n')
            return 1
    if sys.argv[1:] != ['bridge']:
        sys.stderr.write('Usage: <installed Python> -m mvibe_auth bridge|login\n')
        return 2
    try:
        from mvibe_auth.bridge import stdio
        asyncio.run(stdio())
        return 0
    except (Exception, KeyboardInterrupt):
        sys.stderr.write('Vibe OIDC helper failed; check the installed mvibe-auth runtime and configuration.\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
