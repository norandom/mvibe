"""Upstream rate-limit policy for the proxy.

Mistral rejects a request that exceeds a workspace limit with HTTP 429 before any
inference runs. This module holds the proxy's policy for that rejection. So far it
contains the operator configuration: RateLimitConfig.from_env reads the
MVIBE_RATE_LIMIT_* settings and validates them all at startup, so the rest of the
rate-limit path can trust the values.

Pacing is off by default. While MVIBE_RATE_LIMIT_RPS and MVIBE_RATE_LIMIT_MODEL_RPS
are unset, no key has a rate and the proxy only reacts to rejections. No
account-specific rate is built in. An invalid setting raises RateLimitConfigError.
Its message names only the offending variable and never repeats a value from the
environment.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import os
from types import MappingProxyType

__all__ = [
    'DEFAULT_BACKOFF_CAP_S',
    'DEFAULT_MAX_QUEUE',
    'DEFAULT_MAX_WAIT_S',
    'ENV_BACKOFF_CAP_S',
    'ENV_MAX_QUEUE',
    'ENV_MAX_WAIT_S',
    'ENV_MODEL_RPS',
    'ENV_RPS',
    'MODELS_KEY',
    'RateLimitConfig',
    'RateLimitConfigError',
]

# --- config (Requirements 3.1, 3.2, 3.3, 3.4, 3.6) ---------------------------

ENV_RPS = 'MVIBE_RATE_LIMIT_RPS'
ENV_MODEL_RPS = 'MVIBE_RATE_LIMIT_MODEL_RPS'
ENV_MAX_WAIT_S = 'MVIBE_RATE_LIMIT_MAX_WAIT_S'
ENV_MAX_QUEUE = 'MVIBE_RATE_LIMIT_MAX_QUEUE'
ENV_BACKOFF_CAP_S = 'MVIBE_RATE_LIMIT_BACKOFF_CAP_S'

DEFAULT_MAX_WAIT_S = 120.0
DEFAULT_MAX_QUEUE = 64
DEFAULT_BACKOFF_CAP_S = 30.0

# Gate key for model-list discovery: never proactively paced (Requirement 3.5).
MODELS_KEY = 'models'


class RateLimitConfigError(ValueError):
    """A MVIBE_RATE_LIMIT_* setting is malformed; the proxy must not start."""


def _setting(env: Mapping[str, str], key: str) -> str | None:
    """The stripped value of `key`, or None when it is unset or blank."""
    raw = env.get(key)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _positive_number(text: str) -> float | None:
    """`text` as a finite number greater than zero, or None when it is not one."""
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _positive_number_setting[D: (float, None)](env: Mapping[str, str], key: str,
                                               default: D) -> float | D:
    text = _setting(env, key)
    if text is None:
        return default
    value = _positive_number(text)
    if value is None:
        raise RateLimitConfigError(f'{key} must be a positive number')
    return value


def _positive_int_setting(env: Mapping[str, str], key: str, default: int) -> int:
    text = _setting(env, key)
    if text is None:
        return default
    try:
        value = int(text)
    except ValueError:
        raise RateLimitConfigError(f'{key} must be a positive integer') from None
    if value <= 0:
        raise RateLimitConfigError(f'{key} must be a positive integer')
    return value


def _model_rps_setting(env: Mapping[str, str]) -> Mapping[str, float]:
    """Parse `model=rps[,model=rps]`; whitespace around names and rates is ignored."""
    text = _setting(env, ENV_MODEL_RPS)
    if text is None:
        return MappingProxyType({})
    rates: dict[str, float] = {}
    for position, entry in enumerate(text.split(','), start=1):
        name, separator, rate_text = entry.partition('=')
        name = name.strip()
        rate = _positive_number(rate_text.strip())
        if not separator or not name or rate is None:
            raise RateLimitConfigError(
                f'{ENV_MODEL_RPS} entry {position} is malformed; expected a '
                'comma-separated list of model=rps with a positive rps')
        if name in rates:
            raise RateLimitConfigError(
                f'{ENV_MODEL_RPS} entry {position} repeats a model named earlier')
        rates[name] = rate
    return MappingProxyType(rates)


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    """Validated MVIBE_RATE_LIMIT_* settings; rates are requests per second."""

    default_rps: float | None = None
    model_rps: Mapping[str, float] = field(
        default_factory=lambda: MappingProxyType({}))
    max_wait_s: float = DEFAULT_MAX_WAIT_S
    max_queue: int = DEFAULT_MAX_QUEUE
    backoff_cap_s: float = DEFAULT_BACKOFF_CAP_S

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RateLimitConfig:
        """Return the validated config; unset or blank settings take their defaults."""
        if env is None:
            env = os.environ
        return cls(
            default_rps=_positive_number_setting(env, ENV_RPS, None),
            model_rps=_model_rps_setting(env),
            max_wait_s=_positive_number_setting(
                env, ENV_MAX_WAIT_S, DEFAULT_MAX_WAIT_S),
            max_queue=_positive_int_setting(env, ENV_MAX_QUEUE, DEFAULT_MAX_QUEUE),
            backoff_cap_s=_positive_number_setting(
                env, ENV_BACKOFF_CAP_S, DEFAULT_BACKOFF_CAP_S),
        )

    def rate_for(self, key: str) -> float | None:
        """The pacing rate for gate `key`, or None when the key is not paced.

        A model override wins over the default rate. Model-list discovery
        (`models`) is never paced, whatever the configuration says.
        """
        if key == MODELS_KEY:
            return None
        return self.model_rps.get(key, self.default_rps)
