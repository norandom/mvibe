"""Public Vibe ID-token authentication API; importing performs no I/O."""
__version__ = "3.0.4"

from mvibe_auth.api import (
    AuthConfig, AuthError, AuthFailure, AuthFailureCode, AuthSession, AuthSnapshot,
    AuthStatus, LoginCompleted, LoginHandle, LoginOutcome, LoginReusedCurrent,
)

__all__ = ['AuthConfig', 'AuthError', 'AuthFailure', 'AuthFailureCode', 'AuthSession',
           'AuthSnapshot', 'AuthStatus', 'LoginCompleted', 'LoginHandle',
           'LoginOutcome', 'LoginReusedCurrent']
