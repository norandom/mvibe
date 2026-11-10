"""Upstream rate-limit policy for the proxy.

Mistral rejects a request that exceeds a workspace limit with HTTP 429 before any
inference runs. This module holds the proxy's policy for that rejection.
RateLimitConfig.from_env reads the MVIBE_RATE_LIMIT_* settings and validates them
all at startup, so the rest of the rate-limit path can trust the values. classify
recognizes the rejection by its status alone and turns it into a RateLimitSignal:
the wait from Retry-After (parse_retry_after) or a capped fallback, plus a copy of
the x-ratelimit-* headers that is safe to record. UpstreamGate schedules the
upstream attempts: one FIFO line per key with a single attempt in flight,
optional pacing per key, and a bound on the runs held across all keys. A 429
holds its key for the Retry-After or a jittered backoff, and the rejected
request is sent again first. When the hold would outlast the request's maximum
wait, the gate answers with that last 429 instead.

Pacing is off by default. While MVIBE_RATE_LIMIT_RPS and MVIBE_RATE_LIMIT_MODEL_RPS
are unset, no key has a rate and the proxy only reacts to rejections. No
account-specific rate is built in. An invalid setting raises RateLimitConfigError.
Its message names only the offending variable and never repeats a value from the
environment.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
import contextlib
from dataclasses import dataclass, field
from datetime import UTC
import email.utils
import json
import math
import os
import random
import re
import time
from types import MappingProxyType

import httpx2

from mvibe_proxy.diagnostics import GateRecord

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
    'GateRecord',
    'RateLimitConfig',
    'RateLimitConfigError',
    'RateLimitSignal',
    'UpstreamGate',
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


# --- scheduling (Requirements 1.3, 2.1, 2.2, 2.5, 3.1, 3.2, 3.3, 3.5, 5.1, 5.3, 6.3) ---

_JSON = 'application/json'
_CONTENT_TYPE = 'content-type'
_EXHAUSTED_MESSAGE = 'Upstream rate limit persisted; retry after {} seconds'
# A rejected attempt's body is kept for the client only up to this size (Req 6.3).
_REJECTION_BODY_LIMIT = 64 * 1024
# The backoff stops doubling here. Any cap is reached long before, and without a
# bound 2.0 ** n raises OverflowError after about a thousand rejections.
_MAX_DOUBLINGS = 64


@dataclass(slots=True)
class _KeyState:
    """One gate key: its pacing clock, its rate-limit hold and its FIFO line.

    `waiters[0]` belongs to the run that owns the key. That run waits for the
    slot and then has the key's only attempt in flight. Every later run waits on
    its own future until the run ahead of it leaves and promotes it. A 429 sets
    `hold_until`, which every run on the key honours, whoever set it.
    """

    next_slot: float = 0.0
    hold_until: float = 0.0
    waiters: deque[asyncio.Future[None]] = field(default_factory=deque)

    def ready_at(self) -> float:
        """The earliest `clock` time at which the head may send."""
        return max(self.next_slot, self.hold_until)


@dataclass(frozen=True, slots=True)
class _Rejection:
    """An upstream 429 read into memory, so its stream is closed while the run waits.

    `headers` keeps only what may reach the client again: `content-type`, every
    `x-ratelimit-*` header as received, and `retry-after` when the upstream sent
    a usable one of at least one second.
    """

    body: bytes
    headers: Mapping[str, str]


async def _read_rejection(response: httpx2.Response,
                          signal: RateLimitSignal) -> _Rejection:
    """Read at most 64 KiB of the 429's decoded body; the stream is closed in any case.

    The body is decoded, so `content-encoding` and `content-length` are not kept.
    """
    body = bytearray()
    try:
        async with contextlib.aclosing(response.aiter_bytes()) as chunks:
            async for chunk in chunks:
                body += chunk[:_REJECTION_BODY_LIMIT - len(body)]
                if len(body) >= _REJECTION_BODY_LIMIT:
                    break
    finally:
        await response.aclose()
    headers: dict[str, str] = {}
    for name, value in response.headers.items():
        lowered = name.lower()
        if lowered == _CONTENT_TYPE or lowered.startswith(_SNAPSHOT_PREFIX):
            headers[lowered] = value
    if signal.retry_after_present and signal.wait_s >= 1:
        headers[_RETRY_AFTER] = response.headers[_RETRY_AFTER]
    return _Rejection(bytes(body), MappingProxyType(headers))


def _exhausted(record: GateRecord, wait_s: float,
               rejection: _Rejection | None = None) -> httpx2.Response:
    """The 429 the gate answers when it gives up, with a Retry-After of at least 1 second.

    After an upstream 429 the answer is that rejection as read: its body and kept
    headers, plus a `retry-after` of `wait_s` rounded up when the upstream sent
    none that is usable. Without a rejection the body is fixed text plus the
    number of seconds; nothing from the request, the environment or an upstream
    response goes into it.
    """
    record.exhausted = True
    retry_after = str(max(1, math.ceil(wait_s)))
    if rejection is not None:
        headers = dict(rejection.headers)
        headers.setdefault(_RETRY_AFTER, retry_after)
        return httpx2.Response(_RATE_LIMIT_STATUS, content=rejection.body, headers=headers)
    body = {'error': {'message': _EXHAUSTED_MESSAGE.format(retry_after),
                      'type': 'rate_limit_error', 'code': 'rate_limited'}}
    return httpx2.Response(
        _RATE_LIMIT_STATUS, content=json.dumps(body).encode(),
        headers={'content-type': _JSON, _RETRY_AFTER: retry_after})


class UpstreamGate:
    """Schedules upstream attempts per key: FIFO order, optional pacing, rate-limit
    holds, and a bounded wait and depth.

    A key (`models`, a model id, `default`, `embeddings`) is one line. Runs on a
    key are served in arrival order, and only the run at the head of the line
    has an attempt in flight. A paced key (`config.rate_for(key)` returns a
    rate) starts its attempts at least `1 / rate` seconds apart. An unpaced key,
    `models` always among them, lets the head send as soon as the run ahead has
    finished. Keys never wait for one another.

    An upstream 429 holds its key: no run on it is sent before the hold ends.
    The hold lasts as long as `Retry-After` asks, but at least the pacing
    interval (1 s when unpaced). Without a usable `Retry-After` it is a jittered
    backoff that starts at that interval, doubles with each rejection of the run
    and never exceeds `config.backoff_cap_s`. No hold is longer than
    `config.max_wait_s`. `warn(key, wait)` is called as each hold starts. The
    rejected run keeps the head of the line and is sent again when the hold ends.

    At most `config.max_queue` runs are inside the gate at once, waiting or in
    flight, counted across all keys. A run beyond that bound, a run that reaches
    the head after its deadline (`arrived + config.max_wait_s`), and a run whose
    next slot or hold ends past that deadline get a 429 at once, without a
    further attempt.

    `clock` and `sleep` share one time base in seconds (monotonic by default);
    tests inject a fake clock. `wall_clock` is the POSIX time (`time.time` by
    default) and only turns an HTTP-date `Retry-After` into a delay. `random`
    draws the backoff jitter from [0, 1).
    """

    def __init__(self, config: RateLimitConfig, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 random: Callable[[], float] = random.random,
                 warn: Callable[[str, float], None] | None = None,
                 wall_clock: Callable[[], float] = time.time) -> None:
        self._config = config
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._random = random
        self._warn = warn
        self._keys: dict[str, _KeyState] = {}
        self._held = 0

    @property
    def held(self) -> int:
        """The runs inside the gate right now, waiting or in flight, on every key."""
        return self._held

    async def run(self, key: str, attempt: Callable[[], Awaitable[httpx2.Response]], *,
                  arrived: float, record: GateRecord) -> httpx2.Response:
        """Call `attempt` when `key` comes to this run and return the upstream answer.

        `arrived` is the `clock` reading taken when the request arrived; the run
        gives up at `arrived + config.max_wait_s`. A response other than a 429 is
        returned unchanged and unread. A 429 is read (64 KiB at most) and closed,
        counted in `record` with its `x-ratelimit-*` headers, and holds the key;
        the run then waits at the head of its line and calls `attempt` again.
        When the hold would end past the deadline, the run returns that last 429
        instead: its body, `content-type` and `x-ratelimit-*` headers, with a
        `retry-after` of at least 1. When the gate is full, the run reaches the
        head too late, or the key's slot lies past the deadline before any
        attempt, the gate returns a synthesized 429 (JSON body, `retry-after` of
        at least 1) without calling `attempt`. Every such answer sets
        `record.exhausted`. Time spent waiting, holds included, is added to
        `record.queued_ms`.

        A run cancelled while it waits leaves the line without sending and
        promotes the next run; the key's hold stays. An exception from `attempt`
        propagates unchanged once the key is released.
        """
        if self._held >= self._config.max_queue:
            now = self._clock()
            state = self._keys.get(key)
            ready_at = now if state is None else state.ready_at()
            return _exhausted(record, max(ready_at - now, self._interval(key)))
        state = self._state(key)
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        state.waiters.append(waiter)
        self._held += 1
        try:
            return await self._serve(key, state, waiter, attempt,
                                     deadline=arrived + self._config.max_wait_s,
                                     record=record)
        finally:
            self._held -= 1
            self._leave(state, waiter)

    async def _serve(self, key: str, state: _KeyState, waiter: asyncio.Future[None],
                     attempt: Callable[[], Awaitable[httpx2.Response]], *,
                     deadline: float, record: GateRecord) -> httpx2.Response:
        """Wait for the head position, then send until the upstream takes the request.

        Each 429 holds the key and sends again from the head, until the run is
        out of time.
        """
        waited_from = self._clock()
        if state.waiters[0] is not waiter:
            await waiter
        interval = self._interval(key)
        shortest = interval if interval > 0 else 1.0
        rejection: _Rejection | None = None
        rejections = 0
        while True:
            on_time = await self._wait_for_slot(state, deadline)
            now = self._clock()
            record.queued_ms += (now - waited_from) * 1000
            if not on_time:
                return _exhausted(record, max(state.ready_at() - now, interval), rejection)
            if interval > 0:
                state.next_slot = now + interval
            # The head keeps the key while its attempt is in flight.
            response = await attempt()
            signal = classify(
                response.status_code, response.headers,
                fallback_wait_s=shortest * 2.0 ** min(rejections, _MAX_DOUBLINGS),
                cap_s=self._config.backoff_cap_s, now=self._wall_clock())
            if signal is None:
                return response
            rejection = await _read_rejection(response, signal)
            rejections += 1
            record.rejections += 1
            record.headers.update(signal.headers)
            self._hold(key, state, signal, shortest)
            waited_from = self._clock()

    def _hold(self, key: str, state: _KeyState, signal: RateLimitSignal,
              shortest: float) -> None:
        """Hold `key` after a 429 and warn once as the hold starts.

        `Retry-After` sets the hold, floored at `shortest` (the pacing interval,
        1 s when unpaced) so that a zero or past value never makes the re-send
        follow at once. Without one, the backoff from `classify` (already capped)
        is jittered between half and one and a half times itself, then kept
        within `shortest` and the cap. No hold is longer than `max_wait_s`.
        """
        if signal.retry_after_present:
            wait = max(signal.wait_s, shortest)
        else:
            jittered = signal.wait_s * (0.5 + self._random())
            wait = min(max(jittered, shortest), self._config.backoff_cap_s)
        wait = min(wait, self._config.max_wait_s)
        hold_until = self._clock() + wait
        if hold_until > state.hold_until:
            state.hold_until = hold_until
            if self._warn is not None:
                self._warn(key, wait)

    async def _wait_for_slot(self, state: _KeyState, deadline: float) -> bool:
        """Sleep until the key may send and return True, or return False to give up.

        The run gives up at once, without sleeping, when it is already past its
        deadline or when the key's next slot or hold ends past it. Each sleep
        covers exactly the remaining delta, and the slot is checked again after
        waking. Only the slot is held against the deadline after a sleep, so a
        late wake-up from a sleep that ended within it still sends.
        """
        if self._clock() > deadline:
            return False
        while True:
            now = self._clock()
            ready_at = state.ready_at()
            if now >= ready_at:
                return True
            if ready_at > deadline:
                return False
            await self._sleep(ready_at - now)

    def _interval(self, key: str) -> float:
        """The pacing interval of `key` in seconds, or 0.0 when the key is not paced."""
        rate = self._config.rate_for(key)
        return 0.0 if rate is None else 1 / rate

    def _state(self, key: str) -> _KeyState:
        """The state for `key`, created on first use.

        Before creating one, the gate drops idle keys whose slot and hold have
        passed. A dropped key behaves exactly like a new one, so the key map
        holds only keys that still have something to enforce.
        """
        state = self._keys.get(key)
        if state is None:
            now = self._clock()
            for idle in [name for name, known in self._keys.items()
                         if not known.waiters and known.ready_at() <= now]:
                del self._keys[idle]
            state = self._keys[key] = _KeyState()
        return state

    def _leave(self, state: _KeyState, waiter: asyncio.Future[None]) -> None:
        """Take `waiter` out of its line. If it was the head, promote the next run.

        A next run whose future is already done was cancelled. That run leaves
        through this method as well, from the head, and promotes the run after it.
        """
        was_head = state.waiters[0] is waiter
        state.waiters.remove(waiter)
        if was_head and state.waiters and not state.waiters[0].done():
            state.waiters[0].set_result(None)
