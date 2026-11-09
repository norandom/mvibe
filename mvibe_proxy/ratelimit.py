"""Upstream rate-limit policy for the proxy.

Mistral rejects a request that exceeds a workspace limit with HTTP 429 before any
inference runs. This module holds the proxy's policy for that rejection.
RateLimitConfig.from_env reads the MVIBE_RATE_LIMIT_* settings and validates them
all at startup, so the rest of the rate-limit path can trust the values. classify
recognizes the rejection by its status alone and turns it into a RateLimitSignal:
the wait from Retry-After (parse_retry_after) or a capped fallback, plus a copy of
the x-ratelimit-* headers that is safe to record.

Pacing is off by default. While MVIBE_RATE_LIMIT_RPS and MVIBE_RATE_LIMIT_MODEL_RPS
are unset, no key has a rate and the proxy only reacts to rejections. No
account-specific rate is built in. An invalid setting raises RateLimitConfigError.
Its message names only the offending variable and never repeats a value from the
environment.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC
import email.utils
import math
import os
import re
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
    'RateLimitSignal',
    'classify',
    'parse_retry_after',
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


# --- classification (Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6) -------------

_RATE_LIMIT_STATUS = 429
_RETRY_AFTER = 'retry-after'
_SNAPSHOT_PREFIX = 'x-ratelimit-'
_SNAPSHOT_VALUE_CHARS = 64
# delay-seconds is 1*DIGIT (RFC 9110 section 10.2.3); a decimal fraction is
# tolerated. ASCII digits only, no sign, exponent, underscore or 'inf'/'nan'.
_DELAY_SECONDS = re.compile('[0-9]+(?:[.][0-9]+)?')


@dataclass(frozen=True, slots=True)
class RateLimitSignal:
    """A classified upstream 429: how long to wait and what the upstream said.

    `headers` holds only the upstream `x-ratelimit-*` headers, with lower-cased
    names and values cut to 64 characters. Nothing else is copied, so the signal
    never carries credentials or body content and is safe to record.
    """

    wait_s: float
    retry_after_present: bool
    headers: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}))


def parse_retry_after(value: str | None, now: float) -> float | None:
    """The wait in seconds that a `Retry-After` value asks for, or None if unusable.

    Delay-seconds must be a non-negative number of ASCII digits (a decimal
    fraction is tolerated). An HTTP-date is read with `email.utils` in any form
    RFC 9110 names (IMF-fixdate, RFC 850, asctime); a date without a zone is UTC,
    and a date at or before `now` yields 0.0. Anything else, a negative number
    included, yields None.

    `now` is the current POSIX time in seconds (`time.time()`). It only turns an
    HTTP-date into a delay; the gate's monotonic clock is separate.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if _DELAY_SECONDS.fullmatch(text):
        delay = float(text)
        return delay if math.isfinite(delay) else None
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delay = when.timestamp() - now
    if not math.isfinite(delay):
        return None
    return delay if delay > 0 else 0.0


def classify(status: int, headers: Mapping[str, str], *, fallback_wait_s: float,
             cap_s: float, now: float) -> RateLimitSignal | None:
    """The rate-limit signal for an upstream response, or None unless it is a 429.

    The status alone decides; the body is never read. A usable `Retry-After` sets
    `wait_s` to the parsed value, uncapped (the gate clamps it to `max_wait_s`).
    Without one, `wait_s` is `fallback_wait_s` capped at `cap_s`; an unparsable
    `Retry-After` counts as absent. Header names match case-insensitively, so a
    plain dict and `httpx2.Headers` behave the same. Only `x-ratelimit-*` headers
    are copied into the read-only snapshot.

    `now` is the current POSIX time in seconds, used only for an HTTP-date
    `Retry-After` (see parse_retry_after). `fallback_wait_s` and `cap_s` must be
    positive.
    """
    if not (fallback_wait_s > 0 and cap_s > 0):
        raise ValueError('fallback_wait_s and cap_s must be positive')
    if status != _RATE_LIMIT_STATUS:
        return None
    retry_after: str | None = None
    snapshot: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered == _RETRY_AFTER:
            retry_after = value
        elif lowered.startswith(_SNAPSHOT_PREFIX):
            snapshot[lowered] = value[:_SNAPSHOT_VALUE_CHARS]
    parsed = parse_retry_after(retry_after, now)
    if parsed is None:
        return RateLimitSignal(wait_s=float(min(fallback_wait_s, cap_s)),
                               retry_after_present=False,
                               headers=MappingProxyType(snapshot))
    return RateLimitSignal(wait_s=parsed, retry_after_present=True,
                           headers=MappingProxyType(snapshot))
