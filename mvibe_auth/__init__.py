"""Public Vibe authentication API; importing performs no I/O."""
__version__ = "3.0.4"

from mvibe_auth.api import (
    AuthConfig, AuthError, AuthFailure, AuthFailureCode, AuthSession, AuthSnapshot,
    AuthStatus, LoginCompleted, LoginHandle, LoginOutcome, LoginReusedCurrent,
)

from mvibe_auth.existing_vibe import ExistingVibeSession

__all__ = ['ExistingVibeSession', 'AuthConfig', 'AuthError', 'AuthFailure', 'AuthFailureCode', 'AuthSession',
           'AuthSnapshot', 'AuthStatus', 'LoginCompleted', 'LoginHandle',
           'LoginOutcome', 'LoginReusedCurrent']
