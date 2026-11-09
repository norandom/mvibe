"""Offline unit contracts for the proxy rate-limit module (upstream-rate-limit-queue):
operator configuration parsed from MVIBE_RATE_LIMIT_* (task 1.1, Requirements 3.1,
3.2, 3.3, 3.4, 3.6), 429 classification with Retry-After parsing (task 1.2,
Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6) and the UpstreamGate scheduling core
(task 1.3, Requirements 2.2, 3.1, 3.2, 3.3, 3.5, 6.3) and its rate-limit holds
(task 1.4, Requirements 1.3, 2.1, 2.2, 2.5, 5.1, 5.3, 6.3)."""
import asyncio
import dataclasses
import email.utils
import gzip
import json
import time

import httpx2
import pytest

from mvibe_auth.api import AuthError, AuthFailure
from mvibe_proxy import ratelimit
from mvibe_proxy.ratelimit import (
    MODELS_KEY,
    GateRecord,
    RateLimitConfig,
    RateLimitConfigError,
    RateLimitSignal,
    UpstreamGate,
    classify,
    parse_retry_after,
)

RPS = 'MVIBE_RATE_LIMIT_RPS'
MODEL_RPS = 'MVIBE_RATE_LIMIT_MODEL_RPS'
MAX_WAIT_S = 'MVIBE_RATE_LIMIT_MAX_WAIT_S'
MAX_QUEUE = 'MVIBE_RATE_LIMIT_MAX_QUEUE'
BACKOFF_CAP_S = 'MVIBE_RATE_LIMIT_BACKOFF_CAP_S'
RATE_LIMIT_KEYS = (RPS, MODEL_RPS, MAX_WAIT_S, MAX_QUEUE, BACKOFF_CAP_S)

# Distinctive, valid values for every setting plus an unrelated secret; none of
# them may ever appear in an error raised for a different variable.
SENTINEL_ENV = {
    RPS: '0.3125',
    MODEL_RPS: 'sentinel-model-alpha=0.4375',
    MAX_WAIT_S: '987',
    MAX_QUEUE: '4321',
    BACKOFF_CAP_S: '27.75',
    'MISTRAL_API_KEY': 'sk-sentinel-secret-value',
}


# --- defaults (Requirements 3.3, 3.6) ---------------------------------------

def test_unset_env_yields_reactive_defaults():
    config = RateLimitConfig.from_env({})
    assert config.default_rps is None  # no pacing unless configured (3.3)
    assert dict(config.model_rps) == {}
    assert config.max_wait_s == 120
    assert config.max_queue == 64
    assert config.backoff_cap_s == 30
    assert isinstance(config.max_queue, int)
    assert isinstance(config.max_wait_s, float)
    assert isinstance(config.backoff_cap_s, float)


def test_blank_values_are_treated_as_unset():
    for blank in ('', '   '):
        config = RateLimitConfig.from_env({key: blank for key in RATE_LIMIT_KEYS})
        assert config == RateLimitConfig.from_env({})


def test_from_env_reads_os_environ_when_env_is_none(monkeypatch):
    for key in RATE_LIMIT_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert RateLimitConfig.from_env().default_rps is None
    monkeypatch.setenv(RPS, '0.5')
    monkeypatch.setenv(MAX_QUEUE, '3')
    config = RateLimitConfig.from_env()
    assert config.default_rps == 0.5
    assert config.max_queue == 3


def test_unrelated_environment_is_ignored():
    config = RateLimitConfig.from_env({'MISTRAL_API_KEY': 'x', 'MVIBE_ECON_MIN_BYTES': 'abc'})
    assert config == RateLimitConfig.from_env({})


# --- explicit values (Requirements 3.1, 3.2, 3.6) ----------------------------

def test_explicit_values_are_parsed():
    config = RateLimitConfig.from_env({
        RPS: ' 0.5 ',
        MODEL_RPS: ' mistral-large-latest = 0.5 , codestral-latest=2 ',
        MAX_WAIT_S: '90',
        MAX_QUEUE: ' 8 ',
        BACKOFF_CAP_S: '12.5',
    })
    assert config.default_rps == 0.5
    assert dict(config.model_rps) == {'mistral-large-latest': 0.5, 'codestral-latest': 2.0}
    assert config.max_wait_s == 90.0
    assert config.max_queue == 8
    assert config.backoff_cap_s == 12.5


def test_observable_default_rate_applies_to_a_named_model():
    config = RateLimitConfig.from_env({RPS: '0.5'})
    assert config.rate_for('mistral-large-latest') == 0.5


def test_model_override_wins_and_other_models_use_the_default():
    config = RateLimitConfig.from_env({RPS: '0.5', MODEL_RPS: 'codestral-latest=2'})
    assert config.rate_for('codestral-latest') == 2.0
    assert config.rate_for('mistral-large-latest') == 0.5
    assert config.rate_for('default') == 0.5


def test_model_override_without_default_leaves_other_models_unpaced():
    config = RateLimitConfig.from_env({MODEL_RPS: 'mistral-large-latest=0.5'})
    assert config.rate_for('mistral-large-latest') == 0.5
    assert config.rate_for('codestral-latest') is None


def test_no_pacing_configured_means_no_rate_for_any_key():
    config = RateLimitConfig.from_env({})
    for key in ('mistral-large-latest', 'default', 'embeddings', 'models'):
        assert config.rate_for(key) is None


def test_models_key_is_never_paced_even_with_a_default_or_override():
    assert RateLimitConfig.from_env({RPS: '0.5'}).rate_for('models') is None
    config = RateLimitConfig.from_env({RPS: '0.5', MODEL_RPS: 'models=5'})
    assert config.rate_for('models') is None


def test_config_is_frozen_and_model_rates_are_read_only():
    config = RateLimitConfig.from_env({MODEL_RPS: 'a=1'})
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.default_rps = 1.0
    with pytest.raises(TypeError):
        config.model_rps['b'] = 2.0


def test_direct_construction_uses_documented_defaults():
    config = RateLimitConfig()
    assert config == RateLimitConfig.from_env({})


# --- invalid settings (Requirement 3.4) --------------------------------------

INVALID_NUMBERS = ('abc', '0', '0.0', '-1', '-0.5', 'nan', 'inf', '1e', '0x10')


@pytest.mark.parametrize('key', (RPS, MAX_WAIT_S, BACKOFF_CAP_S))
@pytest.mark.parametrize('value', INVALID_NUMBERS)
def test_invalid_positive_number_settings_raise_naming_the_variable(key, value):
    with pytest.raises(RateLimitConfigError, match=key) as excinfo:
        RateLimitConfig.from_env({key: value})
    assert 'positive number' in str(excinfo.value)


@pytest.mark.parametrize('value', ('abc', '0', '-3', '1.5', '64.0', 'nan', 'inf'))
def test_invalid_max_queue_raises_naming_the_variable(value):
    with pytest.raises(RateLimitConfigError, match=MAX_QUEUE) as excinfo:
        RateLimitConfig.from_env({MAX_QUEUE: value})
    assert 'positive integer' in str(excinfo.value)


MALFORMED_MODEL_LISTS = (
    'mistral-large-latest',             # missing '='
    'mistral-large-latest:0.5',         # wrong separator
    '=0.5',                             # empty model name
    '  =0.5',                           # whitespace-only model name
    'mistral-large-latest=',            # empty rate
    'mistral-large-latest=fast',        # non-numeric rate
    'mistral-large-latest=0',           # zero rate
    'mistral-large-latest=-1',          # negative rate
    'mistral-large-latest=nan',         # not a finite number
    'a=b=1',                            # second '=' makes the rate non-numeric
    'a=1,a=2',                          # duplicate model
    ' a =1, a=2',                       # duplicate after whitespace trimming
    'a=1,,b=2',                         # empty entry
    'a=1,',                             # trailing empty entry
    ',a=1',                             # leading empty entry
)


@pytest.mark.parametrize('value', MALFORMED_MODEL_LISTS)
def test_malformed_model_list_raises_naming_the_variable(value):
    with pytest.raises(RateLimitConfigError, match=MODEL_RPS):
        RateLimitConfig.from_env({MODEL_RPS: value})


@pytest.mark.parametrize('key,value', [
    (RPS, 'abc'), (RPS, '0'), (RPS, '-1'),
    (MAX_WAIT_S, 'abc'), (MAX_WAIT_S, '0'), (MAX_WAIT_S, '-1'),
    (MAX_QUEUE, 'abc'), (MAX_QUEUE, '0'), (MAX_QUEUE, '-1'),
    (BACKOFF_CAP_S, 'abc'), (BACKOFF_CAP_S, '0'), (BACKOFF_CAP_S, '-1'),
    (MODEL_RPS, 'sentinel-model-beta'), (MODEL_RPS, 'sentinel-model-beta=0'),
    (MODEL_RPS, 'sentinel-model-beta=1,sentinel-model-beta=2'),
])
def test_errors_never_echo_other_environment_values(key, value):
    env = dict(SENTINEL_ENV)
    env[key] = value
    with pytest.raises(RateLimitConfigError, match=key) as excinfo:
        RateLimitConfig.from_env(env)
    message = str(excinfo.value)
    for other, other_value in SENTINEL_ENV.items():
        if other != key:
            assert other_value not in message
            assert other not in message


def test_config_error_is_a_value_error():
    assert issubclass(RateLimitConfigError, ValueError)


# --- Retry-After parsing (Requirement 1.2) ------------------------------------

# 2025-11-09 12:00:00 UTC as a POSIX timestamp; the HTTP-dates below are relative to it.
NOW = 1_762_689_600.0
TEN_SECONDS_AHEAD = 'Sun, 09 Nov 2025 12:00:10 GMT'


@pytest.mark.parametrize('value,expected', [
    ('7', 7.0),
    (' 7 ', 7.0),
    ('0', 0.0),
    ('120', 120.0),
    ('007', 7.0),
    ('1.5', 1.5),                       # decimal accepted defensively
])
def test_parse_retry_after_delay_seconds(value, expected):
    wait = parse_retry_after(value, NOW)
    assert wait == expected
    assert isinstance(wait, float)


@pytest.mark.parametrize('value', [
    TEN_SECONDS_AHEAD,                  # IMF-fixdate
    'Sunday, 09-Nov-25 12:00:10 GMT',   # obsolete RFC 850 form
    'Sun Nov  9 12:00:10 2025',         # obsolete asctime form, no zone: UTC
    'Sun, 09 Nov 2025 12:00:10 -0000',  # unknown zone: UTC
    'Sun, 09 Nov 2025 13:00:10 +0100',  # explicit offset is honoured
])
def test_parse_retry_after_http_date_ten_seconds_ahead(value):
    assert parse_retry_after(value, NOW) == pytest.approx(10.0)


def test_parse_retry_after_http_date_uses_the_fractional_now():
    assert parse_retry_after(TEN_SECONDS_AHEAD, NOW + 0.25) == pytest.approx(9.75)


@pytest.mark.parametrize('value', [
    'Sun, 09 Nov 2025 12:00:00 GMT',    # exactly now
    'Sun, 09 Nov 2025 11:59:00 GMT',    # one minute ago
    'Wed, 21 Oct 2015 07:28:00 GMT',    # years ago
])
def test_parse_retry_after_past_or_present_http_date_yields_zero(value):
    assert parse_retry_after(value, NOW) == 0.0


@pytest.mark.parametrize('value', [
    None, '', '   ',
    'abc', 'soon', '7 seconds', '7s',
    '-1', '-0', '-0.5', '+5',           # delay-seconds is a non-negative number
    'nan', 'inf', '1e3', '0x10', '1_000', '1.', '.5', '1,5', '3, 5',
    '\u0661\u0662',                     # non-ASCII digits
    '9' * 400,                          # overflows to infinity
    'Wed, 99 Foo 2015 07:28:00 GMT',    # not a date
    'Wed, 21 Oct 2015',                 # date without a time
    'Sat, 01 Jan 10000 00:00:00 GMT',   # year out of range
])
def test_parse_retry_after_garbage_yields_none(value):
    assert parse_retry_after(value, NOW) is None


# --- classification (Requirements 1.1, 1.3, 1.4, 1.5, 1.6) -------------------

def _classify(status, headers, *, fallback_wait_s=1.0, cap_s=30.0, now=NOW):
    return classify(status, headers, fallback_wait_s=fallback_wait_s, cap_s=cap_s, now=now)


def test_observable_retry_after_seconds_sets_the_wait():
    assert classify(429, {'retry-after': '3'}, fallback_wait_s=1, cap_s=30, now=0).wait_s == 3


@pytest.mark.parametrize('status', (200, 201, 400, 401, 403, 404, 428, 430, 500, 502, 503, 504))
def test_statuses_other_than_429_are_not_rate_limit_rejections(status):
    headers = {'retry-after': '7', 'x-ratelimit-remaining-req-minute': '0'}
    assert _classify(status, headers) is None


def test_429_without_retry_after_uses_the_fallback():
    signal = _classify(429, {'content-type': 'application/json'}, fallback_wait_s=2.0)
    assert isinstance(signal, RateLimitSignal)
    assert signal.wait_s == 2.0
    assert signal.retry_after_present is False
    assert dict(signal.headers) == {}


def test_429_fallback_is_capped():
    signal = _classify(429, {}, fallback_wait_s=45.0, cap_s=30.0)
    assert signal.wait_s == 30.0
    assert signal.retry_after_present is False


@pytest.mark.parametrize('fallback', (0.001, 0.5, 1.0, 29.999, 30.0, 30.001, 1e9, float('inf')))
def test_429_fallback_wait_is_positive_and_never_exceeds_the_cap(fallback):
    signal = _classify(429, {}, fallback_wait_s=fallback, cap_s=30.0)
    assert 0 < signal.wait_s <= 30.0


def test_429_retry_after_seconds_sets_the_wait():
    signal = _classify(429, {'retry-after': '7'}, fallback_wait_s=1.0)
    assert signal.wait_s == 7.0
    assert signal.retry_after_present is True


def test_429_retry_after_http_date_sets_the_wait():
    signal = _classify(429, {'retry-after': TEN_SECONDS_AHEAD})
    assert signal.wait_s == pytest.approx(10.0)
    assert signal.retry_after_present is True


def test_429_retry_after_in_the_past_yields_zero_wait():
    signal = _classify(429, {'retry-after': 'Wed, 21 Oct 2015 07:28:00 GMT'}, fallback_wait_s=5.0)
    assert signal.wait_s == 0.0
    assert signal.retry_after_present is True


def test_429_retry_after_is_not_capped_here():
    # Upstream guidance wins; the gate clamps it to max_wait_s later.
    signal = _classify(429, {'retry-after': '45'}, cap_s=30.0)
    assert signal.wait_s == 45.0


@pytest.mark.parametrize('value', ('soon', '-1', '', '3, 5'))
def test_429_with_unusable_retry_after_counts_as_absent(value):
    signal = _classify(429, {'retry-after': value}, fallback_wait_s=4.0)
    assert signal.wait_s == 4.0
    assert signal.retry_after_present is False


@pytest.mark.parametrize('name', ('Retry-After', 'RETRY-AFTER', 'retry-after'))
def test_retry_after_name_is_matched_case_insensitively(name):
    signal = _classify(429, {name: '7'})
    assert signal.wait_s == 7.0
    assert signal.retry_after_present is True


def test_429_is_classified_whatever_the_content_type():
    # The body shape is irrelevant (1.1): classify never sees the body.
    for content_type in ('application/json', 'text/html', 'text/plain', ''):
        assert _classify(429, {'content-type': content_type}) is not None


# Distinctive values that must never reach the signal (1.6).
SECRET_HEADERS = {
    'Authorization': 'Bearer sk-sentinel-secret-value',
    'Cookie': 'session=sentinel-cookie',
    'Set-Cookie': 'session=sentinel-set-cookie',
    'Content-Type': 'application/json',
    'Content-Length': '87',
    'X-Request-Id': 'sentinel-request-id',
    'X-Mistral-Error': '{"message":"Rate limit exceeded","code":"1300"}',
    'RateLimit-Policy': 'sentinel-ietf-policy',
    'X-RateLimit': 'sentinel-no-dash',
    'X-RateLimitRemaining': 'sentinel-no-separator',
}


def test_header_snapshot_keeps_only_x_ratelimit_headers():
    headers = dict(SECRET_HEADERS)
    headers.update({
        'Retry-After': '3',
        'X-RateLimit-Limit-Req-Minute': '5',
        'x-ratelimit-remaining-req-minute': '0',
    })
    signal = _classify(429, headers)
    assert dict(signal.headers) == {
        'x-ratelimit-limit-req-minute': '5',
        'x-ratelimit-remaining-req-minute': '0',
    }
    rendered = repr(signal)
    for name, value in SECRET_HEADERS.items():
        assert value not in rendered
        assert name.lower() not in signal.headers
    assert 'Rate limit exceeded' not in rendered


def test_header_snapshot_is_recorded_without_retry_after():
    signal = _classify(429, {'x-ratelimit-remaining-req-minute': '0'})
    assert signal.retry_after_present is False
    assert dict(signal.headers) == {'x-ratelimit-remaining-req-minute': '0'}


@pytest.mark.parametrize('length,kept', [(0, 0), (63, 63), (64, 64), (65, 64), (500, 64)])
def test_header_snapshot_values_are_truncated_to_64_characters(length, kept):
    signal = _classify(429, {'x-ratelimit-note': 'v' * length})
    assert signal.headers['x-ratelimit-note'] == 'v' * kept


def test_signal_is_frozen_and_header_snapshot_is_a_read_only_copy():
    headers = {'x-ratelimit-remaining-req-minute': '0'}
    signal = _classify(429, headers)
    headers['x-ratelimit-remaining-req-minute'] = '9'
    headers['x-ratelimit-added-later'] = '1'
    assert dict(signal.headers) == {'x-ratelimit-remaining-req-minute': '0'}
    with pytest.raises(TypeError):
        signal.headers['x-ratelimit-other'] = '1'
    with pytest.raises(dataclasses.FrozenInstanceError):
        signal.wait_s = 0.0


def test_classify_accepts_upstream_httpx2_headers():
    headers = httpx2.Headers({
        'Retry-After': '7',
        'X-RateLimit-Remaining-Req-Minute': '0',
        'Authorization': 'Bearer sk-sentinel-secret-value',
    })
    signal = _classify(429, headers)
    assert signal.wait_s == 7.0
    assert signal.retry_after_present is True
    assert dict(signal.headers) == {'x-ratelimit-remaining-req-minute': '0'}


@pytest.mark.parametrize('fallback,cap', [(0.0, 30.0), (-1.0, 30.0), (float('nan'), 30.0),
                                          (1.0, 0.0), (1.0, -1.0), (1.0, float('nan'))])
def test_classify_rejects_a_non_positive_fallback_or_cap(fallback, cap):
    with pytest.raises(ValueError):
        _classify(429, {}, fallback_wait_s=fallback, cap_s=cap)


def test_classification_api_is_exported():
    assert {'RateLimitSignal', 'classify', 'parse_retry_after'} <= set(ratelimit.__all__)


# --- gate scheduling (Requirements 2.2, 3.1, 3.2, 3.3, 3.5, 6.3) -------------

PACED = RateLimitConfig(default_rps=0.5)          # one start every 2 s per key
UNPACED = RateLimitConfig()
LARGE = 'mistral-large-latest'


def _response(status=200):
    return httpx2.Response(status, content=b'{"ok":true}',
                           headers={'content-type': 'application/json'})


class Upstream:
    """Fake `attempt` factory: logs (label, clock time) when each attempt starts."""

    def __init__(self, clock):
        self.clock = clock
        self.sent = []

    def attempt(self, label, *, response=None, release=None, error=None):
        async def send():
            self.sent.append((label, self.clock.time()))
            if release is not None:
                await release.wait()
            if error is not None:
                raise error
            return response if response is not None else _response()
        return send

    def script(self, label, *steps, latency=0.0, release=None):
        """An attempt that answers call n with `steps[n]`; the last step repeats.

        A step is a zero-argument response factory, called once per use, or an
        exception to raise. `latency` advances the fake clock inside every call,
        like an upstream round trip; `release` holds every call until it is set.
        """
        remaining = list(steps)

        async def send():
            self.sent.append((label, self.clock.time()))
            if release is not None:
                await release.wait()
            self.clock.advance(latency)
            step = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            if isinstance(step, BaseException):
                raise step
            return step()
        return send

    def labels(self):
        return [label for label, _ in self.sent]

    def times(self, label=None):
        return [at for name, at in self.sent if label is None or name == label]


class Sleeps:
    """Wraps the fake clock's sleep and records every requested delay."""

    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)
        await self.clock.sleep(seconds)


def _never(*_args):
    raise AssertionError('called outside rejection handling')


def _gate(clock, config=PACED, *, sleep=None):
    return UpstreamGate(config, clock=clock.time, sleep=sleep or clock.sleep,
                        random=_never, warn=_never)


def _start(gate, clock, key, attempt, record=None, *, arrived=None):
    return asyncio.create_task(gate.run(
        key, attempt, arrived=clock.time() if arrived is None else arrived,
        record=GateRecord() if record is None else record))


async def _settle(rounds=5):
    for _ in range(rounds):
        await asyncio.sleep(0)


def _bounded(awaitable):
    """Bound an await on wall-clock time, so a scheduling regression fails, not hangs.

    The gate keeps running on the fake clock; the bound is protection only.
    """
    return asyncio.wait_for(awaitable, 5)


def _assert_synthesized_429(response, retry_after):
    assert response.status_code == 429
    assert response.headers['content-type'] == 'application/json'
    assert response.headers['retry-after'] == str(retry_after)
    assert int(response.headers['retry-after']) >= 1
    assert json.loads(response.content) == {'error': {
        'message': f'Upstream rate limit persisted; retry after {retry_after} seconds',
        'type': 'rate_limit_error',
        'code': 'rate_limited',
    }}


@pytest.mark.anyio
async def test_observable_three_runs_on_one_key_start_one_interval_apart(clock):
    sleeps = Sleeps(clock)
    gate = _gate(clock, sleep=sleeps)
    upstream = Upstream(clock)
    t0 = clock.time()
    responses = await _bounded(asyncio.gather(*(
        gate.run(LARGE, upstream.attempt(LARGE), arrived=t0, record=GateRecord())
        for _ in range(3))))
    times = upstream.times()
    assert times == [t0, t0 + 2, t0 + 4]
    assert [later - earlier for earlier, later in zip(times, times[1:])] == [1 / 0.5] * 2
    assert sleeps.calls == [2.0, 2.0]       # the exact remaining delta, no polling
    assert [response.status_code for response in responses] == [200, 200, 200]
    assert gate.held == 0


@pytest.mark.anyio
async def test_waiting_time_is_recorded_as_queued_ms(clock):
    gate = _gate(clock)
    upstream = Upstream(clock)
    release = asyncio.Event()
    records = [GateRecord() for _ in range(3)]
    first = _start(gate, clock, LARGE, upstream.attempt('a1', release=release), records[0])
    rest = [_start(gate, clock, LARGE, upstream.attempt(label), record)
            for label, record in (('a2', records[1]), ('a3', records[2]))]
    await _settle()
    assert upstream.labels() == ['a1']
    release.set()
    await _bounded(asyncio.gather(first, *rest))
    assert upstream.labels() == ['a1', 'a2', 'a3']
    assert [record.queued_ms for record in records] == [0.0, 2000.0, 4000.0]
    assert all(record.rejections == 0 and not record.exhausted and record.headers == {}
               for record in records)


@pytest.mark.anyio
async def test_per_model_rate_overrides_the_default(clock):
    config = RateLimitConfig(default_rps=0.5, model_rps={'codestral-latest': 2.0})
    gate = _gate(clock, config)
    upstream = Upstream(clock)
    t0 = clock.time()
    await _bounded(asyncio.gather(*(gate.run('codestral-latest', upstream.attempt('c'),
                                             arrived=t0, record=GateRecord())
                                    for _ in range(3))))
    assert upstream.times('c') == [t0, t0 + 0.5, t0 + 1.0]
    t1 = clock.time()
    await _bounded(asyncio.gather(*(gate.run(LARGE, upstream.attempt('m'), arrived=t1,
                                             record=GateRecord()) for _ in range(3))))
    assert upstream.times('m') == [t1, t1 + 2, t1 + 4]


@pytest.mark.anyio
async def test_models_key_is_never_paced(clock):
    sleeps = Sleeps(clock)
    gate = _gate(clock, sleep=sleeps)
    upstream = Upstream(clock)
    t0 = clock.time()
    records = [GateRecord() for _ in range(3)]
    await _bounded(asyncio.gather(*(gate.run(MODELS_KEY, upstream.attempt('models'),
                                             arrived=t0, record=record)
                                    for record in records)))
    assert upstream.times() == [t0, t0, t0]
    assert sleeps.calls == []
    assert [record.queued_ms for record in records] == [0.0, 0.0, 0.0]


@pytest.mark.anyio
async def test_without_a_configured_rate_requests_are_not_spaced(clock):
    sleeps = Sleeps(clock)
    gate = _gate(clock, UNPACED, sleep=sleeps)
    upstream = Upstream(clock)
    t0 = clock.time()
    await _bounded(asyncio.gather(*(gate.run(LARGE, upstream.attempt(LARGE), arrived=t0,
                                             record=GateRecord()) for _ in range(3))))
    assert upstream.times() == [t0, t0, t0]
    assert sleeps.calls == []


@pytest.mark.anyio
async def test_two_keys_do_not_block_each_other(clock):
    gate = _gate(clock)
    upstream = Upstream(clock)
    release = asyncio.Event()
    t0 = clock.time()
    a1 = _start(gate, clock, 'a', upstream.attempt('a1', release=release))
    a2 = _start(gate, clock, 'a', upstream.attempt('a2'))
    await _settle()
    assert gate.held == 2
    # Key 'a' has an attempt in flight, a waiter queued and its next slot at
    # t0 + 2; key 'b' is served at once all the same.
    record = GateRecord()
    response = await _bounded(gate.run('b', upstream.attempt('b1'), arrived=t0,
                                       record=record))
    assert response.status_code == 200
    assert record.queued_ms == 0.0
    assert upstream.sent == [('a1', t0), ('b1', t0)]
    assert not a2.done()
    release.set()
    await _bounded(asyncio.gather(a1, a2))
    assert upstream.sent == [('a1', t0), ('b1', t0), ('a2', t0 + 2)]
    assert gate.held == 0


@pytest.mark.anyio
async def test_one_attempt_in_flight_per_key_and_fifo_order(clock):
    gate = _gate(clock, UNPACED)
    upstream = Upstream(clock)
    gates = [asyncio.Event() for _ in range(4)]
    tasks = [_start(gate, clock, LARGE, upstream.attempt(f'r{n}', release=gates[n]))
             for n in range(4)]
    await _settle()
    assert upstream.labels() == ['r0']
    for n in range(4):
        gates[n].set()
        await _settle()
        assert upstream.labels() == [f'r{i}' for i in range(min(n + 2, 4))]
    await _bounded(asyncio.gather(*tasks))
    assert gate.held == 0


@pytest.mark.anyio
async def test_held_counts_waiting_and_in_flight_runs_across_keys(clock):
    gate = _gate(clock, UNPACED)
    upstream = Upstream(clock)
    release = asyncio.Event()
    assert gate.held == 0
    tasks = [_start(gate, clock, key, upstream.attempt(key, release=release))
             for key in ('a', 'a', 'b', MODELS_KEY)]
    await _settle()
    assert gate.held == 4
    assert upstream.labels() == ['a', 'b', MODELS_KEY]   # the second 'a' waits
    release.set()
    await _bounded(asyncio.gather(*tasks))
    assert gate.held == 0


@pytest.mark.anyio
async def test_run_beyond_max_queue_gets_a_synthesized_429_without_an_attempt(clock):
    gate = _gate(clock, UNPACED)              # max_queue defaults to 64
    upstream = Upstream(clock)
    release = asyncio.Event()
    keys = ('a', 'b', MODELS_KEY)
    tasks = [_start(gate, clock, keys[n % 3], upstream.attempt(f'r{n}', release=release))
             for n in range(64)]
    await _settle()
    assert gate.held == 64
    record = GateRecord()
    attempted = []

    async def attempt():
        attempted.append(True)
        return _response()

    response = await _bounded(gate.run('c', attempt, arrived=clock.time(), record=record))
    assert attempted == []
    _assert_synthesized_429(response, 1)
    assert record.exhausted is True
    assert record.queued_ms == 0.0
    assert record.rejections == 0
    assert gate.held == 64
    release.set()
    responses = await _bounded(asyncio.gather(*tasks))
    assert [r.status_code for r in responses] == [200] * 64
    assert gate.held == 0
    # Capacity is back once the held runs settle.
    again = await _bounded(gate.run('c', attempt, arrived=clock.time(), record=GateRecord()))
    assert again.status_code == 200
    assert attempted == [True]


@pytest.mark.anyio
async def test_queue_bound_counts_runs_on_every_key(clock):
    gate = _gate(clock, RateLimitConfig(default_rps=0.5, max_queue=2))
    upstream = Upstream(clock)
    release = asyncio.Event()
    a1 = _start(gate, clock, 'a', upstream.attempt('a1', release=release))
    b1 = _start(gate, clock, 'b', upstream.attempt('b1', release=release))
    await _settle()
    record = GateRecord()
    response = await _bounded(gate.run('a', upstream.attempt('a2'), arrived=clock.time(),
                                       record=record))
    # Key 'a' has its next slot 2 s ahead; the hint says so.
    _assert_synthesized_429(response, 2)
    assert record.exhausted is True
    assert upstream.labels() == ['a1', 'b1']
    release.set()
    await _bounded(asyncio.gather(a1, b1))
    assert gate.held == 0


@pytest.mark.anyio
async def test_next_slot_past_the_deadline_yields_a_synthesized_429(clock):
    sleeps = Sleeps(clock)
    gate = _gate(clock, RateLimitConfig(default_rps=0.5, max_wait_s=1.0), sleep=sleeps)
    upstream = Upstream(clock)
    t0 = clock.time()
    await _bounded(gate.run(LARGE, upstream.attempt('a1'), arrived=t0, record=GateRecord()))
    record = GateRecord()
    response = await _bounded(gate.run(LARGE, upstream.attempt('a2'), arrived=t0,
                                       record=record))
    _assert_synthesized_429(response, 2)
    assert record.exhausted is True
    assert upstream.labels() == ['a1']
    assert sleeps.calls == []                 # gave up without waiting
    assert clock.time() == t0
    assert gate.held == 0
    # The refused run did not take the slot: the next run gets it at t0 + 2.
    clock.advance(2)
    await _bounded(gate.run(LARGE, upstream.attempt('a3'), arrived=clock.time(),
                            record=GateRecord()))
    assert upstream.sent == [('a1', t0), ('a3', t0 + 2)]


@pytest.mark.anyio
@pytest.mark.parametrize('arrived_offset,sent', [(0.0, True), (-1.5, False)])
async def test_deadline_counts_from_arrival(clock, arrived_offset, sent):
    gate = _gate(clock, RateLimitConfig(default_rps=0.5, max_wait_s=3.0))
    upstream = Upstream(clock)
    t0 = clock.time()
    await _bounded(gate.run(LARGE, upstream.attempt('a1'), arrived=t0, record=GateRecord()))
    record = GateRecord()
    response = await _bounded(gate.run(LARGE, upstream.attempt('a2'),
                                       arrived=t0 + arrived_offset, record=record))
    assert (response.status_code == 200) is sent
    assert record.exhausted is not sent
    assert upstream.labels() == (['a1', 'a2'] if sent else ['a1'])


@pytest.mark.anyio
async def test_a_run_reaching_the_head_after_its_deadline_is_refused(clock):
    # Strict Requirement 2.5: a run that reaches the head late, behind a long
    # attempt, is refused even though its slot has already come.
    gate = _gate(clock, RateLimitConfig(default_rps=0.5, max_wait_s=1.0))
    upstream = Upstream(clock)
    release = asyncio.Event()
    t0 = clock.time()
    a1 = _start(gate, clock, LARGE, upstream.attempt('a1', release=release), arrived=t0)
    record = GateRecord()
    a2 = _start(gate, clock, LARGE, upstream.attempt('a2'), record, arrived=t0)
    await _settle()
    clock.advance(3)                          # a1 is in flight for 3 s
    release.set()
    response = await _bounded(a2)
    await _bounded(a1)
    # The slot (t0 + 2) has passed; the hint is still the 2 s pacing interval.
    _assert_synthesized_429(response, 2)
    assert upstream.sent == [('a1', t0)]
    assert record.exhausted is True
    assert record.queued_ms == 3000.0
    assert gate.held == 0


@pytest.mark.anyio
async def test_cancelling_a_queued_run_removes_it_and_the_next_proceeds(clock):
    gate = _gate(clock, UNPACED)
    upstream = Upstream(clock)
    release = asyncio.Event()
    a1 = _start(gate, clock, LARGE, upstream.attempt('a1', release=release))
    a2 = _start(gate, clock, LARGE, upstream.attempt('a2'))
    a3 = _start(gate, clock, LARGE, upstream.attempt('a3'))
    await _settle()
    assert gate.held == 3
    a2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await _bounded(a2)
    assert gate.held == 2
    release.set()
    responses = await _bounded(asyncio.gather(a1, a3))
    assert [r.status_code for r in responses] == [200, 200]
    assert upstream.labels() == ['a1', 'a3']
    assert gate.held == 0


@pytest.mark.anyio
async def test_cancelling_the_head_in_its_pacing_wait_sends_nothing(clock):
    gate = _gate(clock)
    upstream = Upstream(clock)
    t0 = clock.time()
    await gate.run(LARGE, upstream.attempt('a1'), arrived=t0, record=GateRecord())
    a2 = _start(gate, clock, LARGE, upstream.attempt('a2'), arrived=t0)
    a3 = _start(gate, clock, LARGE, upstream.attempt('a3'), arrived=t0)
    await asyncio.sleep(0)                    # a2 sleeps towards t0 + 2, a3 queues
    a2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await _bounded(a2)
    await _bounded(a3)
    # a3 takes the slot a2 never used.
    assert upstream.sent == [('a1', t0), ('a3', t0 + 2)]
    assert gate.held == 0


@pytest.mark.anyio
async def test_cancelling_a_run_just_promoted_to_the_head_wakes_the_next(clock):
    gate = _gate(clock, UNPACED)
    upstream = Upstream(clock)
    release = asyncio.Event()
    a1 = _start(gate, clock, LARGE, upstream.attempt('a1', release=release))
    a2 = _start(gate, clock, LARGE, upstream.attempt('a2'))
    a3 = _start(gate, clock, LARGE, upstream.attempt('a3'))
    await _settle()
    release.set()
    await asyncio.sleep(0)                    # a1 finishes and promotes a2 ...
    assert a1.done()
    assert not a2.done()
    a2.cancel()                               # ... which is cancelled before it runs
    with pytest.raises(asyncio.CancelledError):
        await _bounded(a2)
    await _bounded(a3)
    assert upstream.labels() == ['a1', 'a3']
    assert gate.held == 0


@pytest.mark.anyio
async def test_head_finishing_while_the_next_run_is_cancelled_returns_normally(clock):
    gate = _gate(clock, UNPACED)
    upstream = Upstream(clock)
    release = asyncio.Event()
    a1 = _start(gate, clock, LARGE, upstream.attempt('a1', release=release))
    a2 = _start(gate, clock, LARGE, upstream.attempt('a2'))
    a3 = _start(gate, clock, LARGE, upstream.attempt('a3'))
    await _settle()
    release.set()                             # a1 resumes first and leaves ...
    a2.cancel()                               # ... while a2 is cancelled, not yet cleaned up
    assert (await _bounded(a1)).status_code == 200
    with pytest.raises(asyncio.CancelledError):
        await _bounded(a2)
    assert (await _bounded(a3)).status_code == 200
    assert upstream.labels() == ['a1', 'a3']
    assert gate.held == 0


@pytest.mark.anyio
async def test_auth_error_from_attempt_propagates_and_releases_the_key(clock):
    gate = _gate(clock, UNPACED)
    upstream = Upstream(clock)
    release = asyncio.Event()
    error = AuthError(AuthFailure('invalid'))
    a1 = _start(gate, clock, LARGE, upstream.attempt('a1', release=release, error=error))
    a2 = _start(gate, clock, LARGE, upstream.attempt('a2'))
    await _settle()
    release.set()
    with pytest.raises(AuthError) as excinfo:
        await _bounded(a1)
    assert excinfo.value is error
    assert (await _bounded(a2)).status_code == 200
    assert upstream.labels() == ['a1', 'a2']
    assert gate.held == 0
    later = await _bounded(gate.run(LARGE, upstream.attempt('a3'), arrived=clock.time(),
                                    record=GateRecord()))
    assert later.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize('status', (200, 201, 400, 401, 404, 500, 503))
async def test_non_429_responses_are_returned_unchanged(clock, status):
    gate = _gate(clock)
    response = _response(status)

    async def attempt():
        return response

    record = GateRecord()
    assert await gate.run(LARGE, attempt, arrived=clock.time(), record=record) is response
    assert record == GateRecord()


@pytest.mark.anyio
async def test_idle_keys_are_dropped_once_their_slot_has_passed(clock):
    gate = _gate(clock)
    upstream = Upstream(clock)
    release = asyncio.Event()
    for key in ('a', 'b'):
        await gate.run(key, upstream.attempt(key), arrived=clock.time(), record=GateRecord())
    busy = _start(gate, clock, MODELS_KEY, upstream.attempt('busy', release=release))
    await _settle()
    clock.advance(2)
    await _bounded(gate.run('c', upstream.attempt('c'), arrived=clock.time(),
                            record=GateRecord()))
    # 'a' and 'b' are idle with their slot reached, so they behave as new keys;
    # the busy key and the freshly paced 'c' stay.
    assert set(gate._keys) == {MODELS_KEY, 'c'}
    release.set()
    await _bounded(busy)
    assert gate.held == 0


@pytest.mark.anyio
async def test_a_new_key_does_not_reset_another_keys_pending_slot(clock):
    # Creating key 'b' prunes idle keys. Key 'a' has no waiter, but its next
    # slot (t0 + 2) is still ahead, so it must keep it: a2 waits for that slot.
    gate = _gate(clock)
    upstream = Upstream(clock)
    t0 = clock.time()
    await _bounded(gate.run('a', upstream.attempt('a1'), arrived=t0, record=GateRecord()))
    clock.advance(0.5)
    await _bounded(gate.run('b', upstream.attempt('b1'), arrived=clock.time(),
                            record=GateRecord()))
    await _bounded(gate.run('a', upstream.attempt('a2'), arrived=clock.time(),
                            record=GateRecord()))
    assert upstream.sent == [('a1', t0), ('b1', t0 + 0.5), ('a2', t0 + 2.0)]


@pytest.mark.anyio
async def test_a_new_key_does_not_drop_a_key_with_an_attempt_in_flight(clock):
    # Key 'a' is unpaced, so its slot has always passed, but a1 is still in
    # flight. Creating key 'b' must not drop 'a', or a2 would start beside a1.
    gate = _gate(clock, UNPACED)
    upstream = Upstream(clock)
    release = asyncio.Event()
    a1 = _start(gate, clock, 'a', upstream.attempt('a1', release=release))
    await _settle()
    await _bounded(gate.run('b', upstream.attempt('b1'), arrived=clock.time(),
                            record=GateRecord()))
    a2 = _start(gate, clock, 'a', upstream.attempt('a2'))
    await _settle()
    assert upstream.labels() == ['a1', 'b1']
    release.set()
    await _bounded(asyncio.gather(a1, a2))
    assert upstream.labels() == ['a1', 'b1', 'a2']
    assert gate.held == 0


@pytest.mark.anyio
async def test_gate_runs_on_the_real_clock_by_default():
    gate = UpstreamGate(RateLimitConfig(default_rps=1000.0))
    response = _response()

    async def attempt():
        return response

    record = GateRecord()
    for _ in range(2):
        assert await _bounded(gate.run(LARGE, attempt, arrived=time.monotonic(),
                                       record=record)) is response
    assert gate.held == 0
    assert record.exhausted is False


# --- rate-limit holds (task 1.4; Requirements 1.3, 2.1, 2.2, 2.5, 5.1, 5.3, 6.3) ---

RATE_LIMITED_BODY = b'{"object":"error","message":"Rate limit exceeded","type":"rate_limited"}'
RATE_LIMIT_HEADERS = {'x-ratelimit-limit-req-minute': '5',
                      'x-ratelimit-remaining-req-minute': '0'}
BODY_LIMIT = 64 * 1024


class Body(httpx2.AsyncByteStream):
    """A streamed upstream body of `chunks` copies of `data`; records reads and close."""

    def __init__(self, data=RATE_LIMITED_BODY, *, chunks=1):
        self.data = data
        self.chunks = chunks
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(self.chunks):
            self.read += 1
            yield self.data

    async def aclose(self):
        self.closed = True


def _rejection(retry_after=None, *, headers=None, stream=None):
    """A Mistral-shaped upstream 429, streamed from `stream` when one is given."""
    values = {'content-type': 'application/json'}
    if retry_after is not None:
        values['retry-after'] = retry_after
    values.update(headers or {})
    if stream is not None:
        return httpx2.Response(429, headers=values, stream=stream)
    return httpx2.Response(429, headers=values, content=RATE_LIMITED_BODY)


def _http_date(posix):
    return email.utils.formatdate(posix, usegmt=True)


class Warnings:
    """Records every warn(key, wait) call the gate makes."""

    def __init__(self):
        self.calls = []

    def __call__(self, key, wait):
        self.calls.append((key, wait))

    def waits(self):
        return [wait for _, wait in self.calls]


class HeldSleep:
    """A sleep that records its delay and returns only when the test wakes it.

    The clock stands still while a run sleeps, so the test can watch other runs
    during a hold. `wake(seconds)` advances the clock and ends every pending sleep.
    """

    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self._pending = []

    async def __call__(self, seconds):
        self.calls.append(seconds)
        woken = asyncio.Event()
        self._pending.append(woken)
        await woken.wait()

    def wake(self, seconds):
        self.clock.advance(seconds)
        for woken in self._pending:
            woken.set()
        self._pending.clear()


def _holding_gate(clock, config=UNPACED, *, sleep=None, random=lambda: 0.5, warn=None):
    """A gate for rejection tests. A draw of 0.5 makes every backoff exactly its base."""
    return UpstreamGate(config, clock=clock.time, sleep=sleep or clock.sleep, random=random,
                        warn=warn, wall_clock=clock.time)


@pytest.mark.anyio
async def test_observable_429_then_200_completes_with_the_200_after_two_attempts(clock):
    warnings = Warnings()
    gate = _holding_gate(clock, PACED, warn=warnings)
    upstream = Upstream(clock)
    rejected = Body()
    accepted = _response()
    record = GateRecord()
    t0 = clock.time()
    response = await _bounded(gate.run(LARGE, upstream.script(
        'a', lambda: _rejection(headers=RATE_LIMIT_HEADERS, stream=rejected),
        lambda: accepted), arrived=t0, record=record))
    assert response is accepted
    # Without Retry-After the first backoff is the key's 2 s pacing interval.
    assert upstream.sent == [('a', t0), ('a', t0 + 2)]
    assert rejected.closed
    assert record.rejections == 1
    assert record.queued_ms == 2000.0
    assert record.headers == RATE_LIMIT_HEADERS
    assert record.exhausted is False
    assert warnings.calls == [(LARGE, 2.0)]
    assert gate.held == 0


@pytest.mark.anyio
async def test_the_rejected_run_is_sent_again_first_and_later_runs_wait_for_the_hold(clock):
    warnings = Warnings()
    gate = _holding_gate(clock, PACED, random=_never, warn=warnings)
    upstream = Upstream(clock)
    release = asyncio.Event()
    t0 = clock.time()
    records = [GateRecord() for _ in range(3)]
    tasks = [
        _start(gate, clock, LARGE, upstream.script('a', lambda: _rejection('5'), _response,
                                                   release=release), records[0]),
        _start(gate, clock, LARGE, upstream.attempt('b'), records[1]),
        _start(gate, clock, LARGE, upstream.attempt('c'), records[2]),
    ]
    await _settle()                             # all three are in the gate at t0
    release.set()
    responses = await _bounded(asyncio.gather(*tasks))
    assert [response.status_code for response in responses] == [200, 200, 200]
    # a holds the key for 5 s and is sent again ahead of b and c, which then
    # follow at the 2 s pace.
    assert upstream.sent == [('a', t0), ('a', t0 + 5), ('b', t0 + 7), ('c', t0 + 9)]
    assert warnings.calls == [(LARGE, 5.0)]
    assert [record.rejections for record in records] == [1, 0, 0]
    assert [record.queued_ms for record in records] == [5000.0, 7000.0, 9000.0]
    assert gate.held == 0


@pytest.mark.anyio
async def test_a_run_arriving_after_the_rejection_waits_for_the_shared_hold(clock):
    # The key is unpaced, so only its hold can delay a2. Creating key 'b' in
    # between must not drop the held key either.
    warnings = Warnings()
    gate = _holding_gate(clock, RateLimitConfig(max_wait_s=5.0), random=_never, warn=warnings)
    upstream = Upstream(clock)
    t0 = clock.time()
    first = GateRecord()
    # a1 arrived 1 s before it reached the gate, so its deadline is t0 + 4; the
    # 7 s Retry-After, clamped to max_wait 5 s, ends past it.
    response = await _bounded(gate.run('a', upstream.script('a1', lambda: _rejection('7')),
                                       arrived=t0 - 1, record=first))
    assert response.status_code == 429
    assert first.exhausted is True
    assert warnings.calls == [('a', 5.0)]
    await _bounded(gate.run('b', upstream.attempt('b1'), arrived=t0, record=GateRecord()))
    record = GateRecord()
    response = await _bounded(gate.run('a', upstream.attempt('a2'), arrived=t0,
                                       record=record))
    assert response.status_code == 200
    assert upstream.sent == [('a1', t0), ('b1', t0), ('a2', t0 + 5)]
    assert record.queued_ms == 5000.0
    assert record.rejections == 0


@pytest.mark.anyio
@pytest.mark.parametrize('config', [PACED, UNPACED], ids=['paced', 'unpaced'])
async def test_retry_after_seconds_sets_the_hold(clock, config):
    warnings = Warnings()
    gate = _holding_gate(clock, config, random=_never, warn=warnings)
    upstream = Upstream(clock)
    record = GateRecord()
    t0 = clock.time()
    response = await _bounded(gate.run(LARGE, upstream.script(
        'a', lambda: _rejection('7'), _response), arrived=t0, record=record))
    assert response.status_code == 200
    assert upstream.times() == [t0, t0 + 7]
    assert warnings.calls == [(LARGE, 7.0)]
    assert record.queued_ms == 7000.0


@pytest.mark.anyio
async def test_retry_after_http_date_sets_the_hold_from_the_wall_clock(clock):
    warnings = Warnings()
    gate = _holding_gate(clock, random=_never, warn=warnings)
    upstream = Upstream(clock)
    t0 = clock.time()
    await _bounded(gate.run(LARGE, upstream.script(
        'a', lambda: _rejection(_http_date(t0 + 10)), _response), arrived=t0,
        record=GateRecord()))
    assert upstream.times() == [t0, t0 + 10]
    assert warnings.calls == [(LARGE, 10.0)]


@pytest.mark.anyio
@pytest.mark.parametrize('retry_after', ['0', 'past'])
@pytest.mark.parametrize('config,floor', [(PACED, 2.0), (UNPACED, 1.0)],
                         ids=['paced', 'unpaced'])
async def test_a_zero_or_past_retry_after_still_holds_for_the_pacing_interval(
        clock, config, floor, retry_after):
    warnings = Warnings()
    gate = _holding_gate(clock, config, random=_never, warn=warnings)
    upstream = Upstream(clock)
    t0 = clock.time()
    value = _http_date(t0 - 60) if retry_after == 'past' else retry_after
    await _bounded(gate.run(LARGE, upstream.script(
        'a', lambda: _rejection(value), _response), arrived=t0, record=GateRecord()))
    # Never back to back: the hold is the pacing interval, or 1 s when unpaced.
    assert upstream.times() == [t0, t0 + floor]
    assert warnings.calls == [(LARGE, floor)]


@pytest.mark.anyio
async def test_without_retry_after_each_rejection_doubles_the_backoff(clock):
    warnings = Warnings()
    gate = _holding_gate(clock, warn=warnings)
    upstream = Upstream(clock)
    record = GateRecord()
    t0 = clock.time()
    response = await _bounded(gate.run(LARGE, upstream.script(
        'a', _rejection, _rejection, _rejection, _response), arrived=t0, record=record))
    assert response.status_code == 200
    # Unpaced, the backoff starts at 1 s and doubles; one warning per hold.
    assert warnings.calls == [(LARGE, 1.0), (LARGE, 2.0), (LARGE, 4.0)]
    assert upstream.times() == [t0, t0 + 1, t0 + 3, t0 + 7]
    assert record.rejections == 3
    assert record.queued_ms == 7000.0


@pytest.mark.anyio
async def test_paced_backoff_starts_at_the_pacing_interval_and_stops_at_the_cap(clock):
    warnings = Warnings()
    gate = _holding_gate(clock, PACED, warn=warnings)          # backoff cap 30 s
    upstream = Upstream(clock)
    t0 = clock.time()
    await _bounded(gate.run(LARGE, upstream.script('a', *[_rejection] * 6, _response),
                            arrived=t0, record=GateRecord()))
    assert warnings.waits() == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert upstream.times() == [t0, t0 + 2, t0 + 6, t0 + 14, t0 + 30, t0 + 60, t0 + 90]


@pytest.mark.anyio
@pytest.mark.parametrize('draw,waits', [
    (0.0, [1.0, 1.0, 1.5, 1.5, 1.5]),
    (0.999999, [1.499999, 2.999998, 3.0, 3.0, 3.0]),
], ids=['lowest-draw', 'highest-draw'])
async def test_jittered_backoff_is_positive_and_never_beyond_the_cap(clock, draw, waits):
    warnings = Warnings()
    gate = _holding_gate(clock, RateLimitConfig(backoff_cap_s=3.0), random=lambda: draw,
                         warn=warnings)
    upstream = Upstream(clock)
    response = await _bounded(gate.run(LARGE, upstream.script(
        'a', *[_rejection] * 5, _response), arrived=clock.time(), record=GateRecord()))
    assert response.status_code == 200
    assert warnings.waits() == pytest.approx(waits)
    assert all(0 < wait <= 3.0 for wait in warnings.waits())


@pytest.mark.anyio
async def test_the_backoff_honours_a_cap_below_one_second(clock):
    warnings = Warnings()
    gate = _holding_gate(clock, RateLimitConfig(backoff_cap_s=0.25), warn=warnings)
    upstream = Upstream(clock)
    t0 = clock.time()
    await _bounded(gate.run(LARGE, upstream.script('a', _rejection, _rejection, _response),
                            arrived=t0, record=GateRecord()))
    assert warnings.waits() == [0.25, 0.25]
    assert upstream.times() == [t0, t0 + 0.25, t0 + 0.5]


@pytest.mark.anyio
async def test_a_long_run_of_rejections_keeps_the_backoff_finite(clock):
    # 1100 one-second holds fit in max_wait; the doubling must not overflow.
    warnings = Warnings()
    gate = _holding_gate(clock, RateLimitConfig(max_wait_s=2000.0), random=_never,
                         warn=warnings)
    upstream = Upstream(clock)
    record = GateRecord()
    response = await _bounded(gate.run(LARGE, upstream.script(
        'a', *[lambda: _rejection('1')] * 1100, _response), arrived=clock.time(),
        record=record))
    assert response.status_code == 200
    assert record.rejections == 1100
    assert set(warnings.waits()) == {1.0}


@pytest.mark.anyio
async def test_exhaustion_returns_the_last_upstream_429_with_its_body_and_headers(clock):
    warnings = Warnings()
    gate = _holding_gate(clock, RateLimitConfig(max_wait_s=1.0), random=_never, warn=warnings)
    upstream = Upstream(clock)
    body = Body()
    record = GateRecord()
    t0 = clock.time()
    response = await _bounded(gate.run(LARGE, upstream.script(
        'a', lambda: _rejection('30', headers=RATE_LIMIT_HEADERS, stream=body),
        latency=0.25), arrived=t0, record=record))
    # The 30 s Retry-After, clamped to max_wait 1 s, would end at t0 + 1.25, past
    # the deadline t0 + 1: the gate gives up without a further attempt.
    assert upstream.sent == [('a', t0)]
    assert response.status_code == 429
    assert response.content == RATE_LIMITED_BODY
    assert response.headers['content-type'] == 'application/json'
    assert response.headers['retry-after'] == '30'
    assert {name: response.headers[name] for name in RATE_LIMIT_HEADERS} == RATE_LIMIT_HEADERS
    assert body.closed
    assert record.exhausted is True
    assert record.rejections == 1
    assert record.headers == RATE_LIMIT_HEADERS
    assert warnings.calls == [(LARGE, 1.0)]
    assert gate.held == 0
    # The body is in memory: streaming it again and closing it are safe.
    assert b''.join([chunk async for chunk in response.aiter_bytes()]) == RATE_LIMITED_BODY
    await response.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize('retry_after', [None, 'soon', '0', '0.5'])
async def test_exhaustion_sets_retry_after_when_the_upstream_gave_no_usable_one(
        clock, retry_after):
    gate = _holding_gate(clock, RateLimitConfig(default_rps=0.5, max_wait_s=1.5),
                         warn=Warnings())
    upstream = Upstream(clock)
    record = GateRecord()
    t0 = clock.time()
    response = await _bounded(gate.run(LARGE, upstream.script(
        'a', lambda: _rejection(retry_after, headers=RATE_LIMIT_HEADERS), latency=0.25),
        arrived=t0, record=record))
    # The hold (the 2 s interval, clamped to 1.5 s) would end at t0 + 1.75, past
    # t0 + 1.5. The hint is max(1.75 s to the next slot, the 2 s interval).
    assert upstream.sent == [('a', t0)]
    assert response.status_code == 429
    assert response.headers['retry-after'] == '2'
    assert response.content == RATE_LIMITED_BODY
    assert {name: response.headers[name] for name in RATE_LIMIT_HEADERS} == RATE_LIMIT_HEADERS
    assert record.exhausted is True


@pytest.mark.anyio
async def test_exhaustion_after_two_rejections_hints_the_remaining_hold(clock):
    warnings = Warnings()
    gate = _holding_gate(clock, RateLimitConfig(max_wait_s=3.0), warn=warnings)
    upstream = Upstream(clock)
    record = GateRecord()
    t0 = clock.time()
    response = await _bounded(gate.run(LARGE, upstream.script('a', _rejection, latency=0.25),
                                       arrived=t0, record=record))
    # A 1 s hold to t0 + 1.25 and a re-send; the 2 s hold after it would end at
    # t0 + 3.5, past the deadline t0 + 3.
    assert upstream.times() == [t0, t0 + 1.25]
    assert warnings.waits() == [1.0, 2.0]
    assert response.status_code == 429
    assert response.headers['retry-after'] == '2'
    assert response.content == RATE_LIMITED_BODY
    assert record.rejections == 2
    assert record.queued_ms == 1000.0
    assert record.exhausted is True


@pytest.mark.anyio
async def test_the_materialized_429_keeps_the_decoded_body_and_only_rate_limit_headers(clock):
    gate = _holding_gate(clock, RateLimitConfig(max_wait_s=1.0), random=_never,
                         warn=Warnings())
    upstream = Upstream(clock)

    def rejection():
        return httpx2.Response(429, content=gzip.compress(RATE_LIMITED_BODY), headers={
            'content-type': 'application/json', 'content-encoding': 'gzip',
            'retry-after': '30', 'set-cookie': 'session=sentinel-secret',
            'x-request-id': 'req-1', **RATE_LIMIT_HEADERS})

    response = await _bounded(gate.run(LARGE, upstream.script('a', rejection, latency=0.25),
                                       arrived=clock.time(), record=GateRecord()))
    assert response.content == RATE_LIMITED_BODY
    assert set(response.headers) == {'content-type', 'content-length', 'retry-after',
                                     *RATE_LIMIT_HEADERS}
    assert response.headers['content-length'] == str(len(RATE_LIMITED_BODY))


@pytest.mark.anyio
async def test_a_429_body_is_read_up_to_64_kib_and_its_stream_closed(clock):
    body = Body(b'x' * 10_000, chunks=100)      # 1 MB on offer
    gate = _holding_gate(clock, RateLimitConfig(max_wait_s=1.0), random=_never,
                         warn=Warnings())
    upstream = Upstream(clock)
    response = await _bounded(gate.run(LARGE, upstream.script(
        'a', lambda: _rejection('30', stream=body), latency=0.25), arrived=clock.time(),
        record=GateRecord()))
    assert response.status_code == 429
    assert response.content == b'x' * BODY_LIMIT
    assert body.read == 7                       # stopped once 64 KiB were in
    assert body.closed


@pytest.mark.anyio
async def test_a_run_whose_deadline_passed_behind_a_hold_is_refused_at_the_head(clock):
    gate = _holding_gate(clock, RateLimitConfig(max_wait_s=8.0), random=_never,
                         warn=Warnings())
    upstream = Upstream(clock)
    t0 = clock.time()

    def slow_acceptance():
        clock.advance(2)                        # the re-send takes 2 s
        return _response()

    release = asyncio.Event()
    a = _start(gate, clock, LARGE, upstream.script('a', lambda: _rejection('5'),
                                                    slow_acceptance, release=release))
    late = GateRecord()
    b = _start(gate, clock, LARGE, upstream.attempt('b'), late, arrived=t0 - 2)
    c = _start(gate, clock, LARGE, upstream.attempt('c'))
    await _settle()                             # all three are in the gate at t0
    release.set()
    first, refused, last = await _bounded(asyncio.gather(a, b, c))
    # b reaches the head at t0 + 7, past its deadline t0 + 6: it is refused and
    # never sent. c, within its deadline t0 + 8, is sent at once after it.
    assert (first.status_code, last.status_code) == (200, 200)
    _assert_synthesized_429(refused, 1)
    assert late.exhausted is True
    assert late.queued_ms == 7000.0
    assert upstream.sent == [('a', t0), ('a', t0 + 5), ('c', t0 + 7)]
    assert gate.held == 0


@pytest.mark.anyio
async def test_cancelling_a_run_during_its_hold_keeps_the_hold_for_the_next(clock):
    sleep = HeldSleep(clock)
    gate = _holding_gate(clock, sleep=sleep, random=_never, warn=Warnings())
    upstream = Upstream(clock)
    t0 = clock.time()
    a = _start(gate, clock, LARGE, upstream.script('a', lambda: _rejection('5'), _response))
    b = _start(gate, clock, LARGE, upstream.attempt('b'))
    await _settle()
    assert sleep.calls == [5.0]                 # a sleeps through its hold
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await _bounded(a)
    await _settle()
    # b now leads the line, but the key is still held: b sleeps the same 5 s.
    assert sleep.calls == [5.0, 5.0]
    assert upstream.sent == [('a', t0)]
    sleep.wake(5)
    assert (await _bounded(b)).status_code == 200
    assert upstream.sent == [('a', t0), ('b', t0 + 5)]
    assert gate.held == 0


@pytest.mark.anyio
async def test_a_hold_on_one_key_does_not_hold_another(clock):
    sleep = HeldSleep(clock)
    gate = _holding_gate(clock, sleep=sleep, random=_never, warn=Warnings())
    upstream = Upstream(clock)
    t0 = clock.time()
    a = _start(gate, clock, 'a', upstream.script('a', lambda: _rejection('5'), _response))
    await _settle()
    assert sleep.calls == [5.0]
    record = GateRecord()
    response = await _bounded(gate.run('b', upstream.attempt('b'), arrived=t0, record=record))
    assert response.status_code == 200
    assert record.queued_ms == 0.0
    sleep.wake(5)
    assert (await _bounded(a)).status_code == 200
    assert upstream.sent == [('a', t0), ('b', t0), ('a', t0 + 5)]
    assert gate.held == 0


@pytest.mark.anyio
async def test_an_error_from_the_re_send_propagates_and_releases_the_key(clock):
    gate = _holding_gate(clock, random=_never, warn=Warnings())
    upstream = Upstream(clock)
    error = AuthError(AuthFailure('invalid'))
    t0 = clock.time()
    a = _start(gate, clock, LARGE, upstream.script('a', lambda: _rejection('3'), error))
    b = _start(gate, clock, LARGE, upstream.attempt('b'))
    with pytest.raises(AuthError) as excinfo:
        await _bounded(a)
    assert excinfo.value is error
    assert (await _bounded(b)).status_code == 200
    assert upstream.sent == [('a', t0), ('a', t0 + 3), ('b', t0 + 3)]
    assert gate.held == 0


@pytest.mark.anyio
async def test_a_queue_full_refusal_on_a_paced_key_hints_at_least_its_interval(clock):
    gate = _gate(clock, RateLimitConfig(default_rps=0.5, max_queue=1))
    upstream = Upstream(clock)
    release = asyncio.Event()
    a1 = _start(gate, clock, 'a', upstream.attempt('a1', release=release))
    await _settle()
    record = GateRecord()
    # Key 'b' has no state yet, but its pacing interval is still 2 s.
    response = await _bounded(gate.run('b', upstream.attempt('b1'), arrived=clock.time(),
                                       record=record))
    _assert_synthesized_429(response, 2)
    assert record.exhausted is True
    release.set()
    await _bounded(a1)
    assert upstream.labels() == ['a1']
    assert gate.held == 0


@pytest.mark.anyio
async def test_a_gate_without_a_warn_callback_still_holds(clock):
    gate = UpstreamGate(UNPACED, clock=clock.time, sleep=clock.sleep, random=lambda: 0.5)
    upstream = Upstream(clock)
    t0 = clock.time()
    response = await _bounded(gate.run(LARGE, upstream.script('a', _rejection, _response),
                                       arrived=t0, record=GateRecord()))
    assert response.status_code == 200
    assert upstream.times() == [t0, t0 + 1]


def test_gate_record_defaults():
    record = GateRecord()
    assert (record.queued_ms, record.rejections, record.headers, record.exhausted) == (
        0.0, 0, {}, False)
    record.headers['x-ratelimit-remaining-req-minute'] = '0'
    assert GateRecord().headers == {}


def test_gate_api_is_exported():
    assert {'GateRecord', 'UpstreamGate'} <= set(ratelimit.__all__)
