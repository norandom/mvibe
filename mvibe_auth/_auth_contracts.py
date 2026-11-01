"""Primitive authentication results; no service, storage or adapter dependencies."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal


type AuthFailureCode = Literal['missing', 'invalid', 'rejected', 'transient', 'conflict', 'cancelled']
_FAILURE_MESSAGES: Mapping[AuthFailureCode, str] = MappingProxyType({
    'missing': 'Sign in to Vibe to continue.',
    'invalid': 'Invalid authentication configuration or credentials.',
    'rejected': 'OIDC authentication was disabled or rejected.',
    'transient': 'Authentication is temporarily unavailable; retry.',
    'conflict': 'Authentication changed concurrently; retry.',
    'cancelled': 'Authentication was cancelled.',
})


@dataclass(frozen=True, slots=True)
class AuthFailure:
    """Safe public failure. Messages come from a fixed catalogue, never dependencies."""

    code: AuthFailureCode
    retryable: bool = False
    message: str = field(init=False)

    def __post_init__(self) -> None:
        if self.code not in _FAILURE_MESSAGES:
            raise ValueError('Invalid authentication failure code')
        object.__setattr__(self, 'message', _FAILURE_MESSAGES[self.code])


class AuthError(Exception):
    """Typed public exception; dependency causes remain internal to the library."""

    def __init__(self, failure: AuthFailure) -> None:
        self.failure = failure
        super().__init__(failure.message)


@dataclass(frozen=True, slots=True)
class AuthSnapshot:
    """Opaque inference authorization for one target and request.

    UserInfo's access-token credential contract remains separate. No token bundle
    or refresh credential is part of this interface. Adapters must not serialize
    or log headers except onto the validated outbound request.
    """

    headers: Mapping[str, str] = field(repr=False)
    expires_at: datetime
    revision: str
    target: str
    outcome: Literal['resolved', 'completed', 'reused_current'] = 'resolved'
    kind: Literal['id_token', 'vibe_credential'] = 'id_token'

    def __post_init__(self) -> None:
        headers = dict(self.headers)
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items()):
            raise AuthError(AuthFailure('invalid'))
        object.__setattr__(self, 'headers', MappingProxyType(headers))


@dataclass(frozen=True, slots=True)
class LoginHandle:
    attempt_id: str
    authorization_url: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginCompleted:
    """The browser-selected identity was persisted successfully."""

    outcome: Literal['completed'] = field(default='completed', init=False)


@dataclass(frozen=True, slots=True)
class LoginReusedCurrent:
    """Current storage won a conflict; the browser-selected identity was not saved."""

    outcome: Literal['reused_current'] = field(default='reused_current', init=False)


type LoginOutcome = LoginCompleted | LoginReusedCurrent | AuthFailure
