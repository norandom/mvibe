"""Offline unit contracts for the proxy rate-limit module (upstream-rate-limit-queue):
operator configuration parsed from MVIBE_RATE_LIMIT_* (task 1.1, Requirements 3.1,
3.2, 3.3, 3.4, 3.6)."""
import dataclasses

import pytest

from mvibe_proxy.ratelimit import RateLimitConfig, RateLimitConfigError

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
