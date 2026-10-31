"""Public authentication contracts, independent of proxy routing and host hooks.

AuthSession orchestration will be assembled here by the integration task. These
primitive contracts remain below service/runtime in the dependency graph.
"""
from mvibe_auth._auth_config import AuthConfig as AuthConfig
from mvibe_auth._auth_contracts import (
    AuthError as AuthError,
    AuthFailure as AuthFailure,
    AuthFailureCode as AuthFailureCode,
    AuthSnapshot as AuthSnapshot,
    LoginCompleted as LoginCompleted,
    LoginHandle as LoginHandle,
    LoginOutcome as LoginOutcome,
    LoginReusedCurrent as LoginReusedCurrent,
)

__all__ = ['AuthConfig', 'AuthError', 'AuthFailure', 'AuthFailureCode', 'AuthSnapshot',
           'LoginCompleted', 'LoginHandle', 'LoginOutcome', 'LoginReusedCurrent']
