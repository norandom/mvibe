"""Offline unit contracts for the token-economics configuration (proxy-econ-client 1.1)."""
import dataclasses
import sys

import pytest

from mvibe_proxy import econ
from mvibe_proxy.econ import EconConfig, EconConfigError, HARD_EXCLUDED

ECON_KEYS = (
    'MVIBE_ECON_MMHMEM_BIN', 'MVIBE_ECON_PROJECT', 'MVIBE_ECON_TOOLS',
    'MVIBE_ECON_MIN_BYTES', 'MVIBE_ECON_TIMEOUT_MS', 'MVIBE_ECON_MEMO_MAX',
    'MVIBE_ECON_RETRY_S', 'MVIBE_ECON_CONCURRENCY',
)


def valid_env(**overrides):
    """A minimal valid environment; the Python interpreter stands in for the binary."""
    env = {'MVIBE_ECON_MMHMEM_BIN': sys.executable}
    env.update(overrides)
    return env


def test_all_unset_disables_economics():
    assert EconConfig.from_env({}) is None


def test_no_binary_stays_disabled_even_with_malformed_other_keys():
    # Off by default (Req 1.1): without the binary key there is no economics
    # path, so nothing else is validated.
    assert EconConfig.from_env({'MVIBE_ECON_MIN_BYTES': 'abc',
                                'MVIBE_ECON_TOOLS': 'bash'}) is None


def test_blank_binary_disables_economics():
    assert EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': ''}) is None
    assert EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': '   '}) is None


def test_from_env_reads_os_environ_when_env_is_none(monkeypatch):
    for key in ECON_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert EconConfig.from_env() is None
    monkeypatch.setenv('MVIBE_ECON_MMHMEM_BIN', sys.executable)
    config = EconConfig.from_env()
    assert config is not None and config.mmhmem_bin == sys.executable


def test_complete_env_applies_documented_defaults():
    config = EconConfig.from_env(valid_env())
    assert config.mmhmem_bin == sys.executable
    assert config.project == 'mvibe'
    assert config.tools == frozenset({'read'})
    assert (config.min_bytes, config.timeout_ms, config.memo_max,
            config.retry_s, config.concurrency) == (4096, 45000, 2000, 300, 2)


def test_complete_env_honors_explicit_values_and_normalizes_tools():
    config = EconConfig.from_env(valid_env(
        MVIBE_ECON_PROJECT='proj-x',
        MVIBE_ECON_TOOLS=' Read, grep ,Read',
        MVIBE_ECON_MIN_BYTES='1024',
        MVIBE_ECON_TIMEOUT_MS='5000',
        MVIBE_ECON_MEMO_MAX='50',
        MVIBE_ECON_RETRY_S='10',
        MVIBE_ECON_CONCURRENCY='4'))
    assert config.project == 'proj-x'
    assert config.tools == frozenset({'read', 'grep'})  # case-folded, deduplicated
    assert config.min_bytes == 1024
    assert config.timeout_ms == 5000
    assert config.memo_max == 50
    assert config.retry_s == 10
    assert config.concurrency == 4


def test_retry_s_zero_is_valid():
    assert EconConfig.from_env(valid_env(MVIBE_ECON_RETRY_S='0')).retry_s == 0


def test_config_is_frozen():
    config = EconConfig.from_env(valid_env())
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.project = 'other'


def test_hard_excluded_set_is_the_v1_security_floor():
    assert isinstance(HARD_EXCLUDED, frozenset)
    assert HARD_EXCLUDED == frozenset(
        {'edit', 'write', 'bash', 'shell', 'execute', 'patch', 'multiedit'})


@pytest.mark.parametrize('key,value', [
    ('MVIBE_ECON_MIN_BYTES', 'abc'),
    ('MVIBE_ECON_MIN_BYTES', '100'),  # below the 256 floor
    ('MVIBE_ECON_TIMEOUT_MS', '0'),
    ('MVIBE_ECON_TIMEOUT_MS', 'soon'),
    ('MVIBE_ECON_MEMO_MAX', '0'),
    ('MVIBE_ECON_RETRY_S', '-1'),
    ('MVIBE_ECON_CONCURRENCY', '0'),
])
def test_malformed_numeric_settings_raise_errors_naming_the_key(key, value):
    with pytest.raises(EconConfigError, match=key):
        EconConfig.from_env(valid_env(**{key: value}))


def test_missing_binary_path_raises_naming_the_key(tmp_path):
    missing = str(tmp_path / 'absent' / 'mmhmem')
    with pytest.raises(EconConfigError, match='MVIBE_ECON_MMHMEM_BIN'):
        EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': missing})


def test_directory_binary_path_raises_naming_the_key(tmp_path):
    with pytest.raises(EconConfigError, match='MVIBE_ECON_MMHMEM_BIN'):
        EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': str(tmp_path)})


def test_validate_binary_not_executable_branch_with_injected_predicate(tmp_path):
    # Windows os.access reports X_OK for any existing file, so the
    # not-executable branch is proven against the pure validator.
    binary = tmp_path / 'mmhmem'
    binary.write_text('#!/bin/sh\n', encoding='utf-8')
    with pytest.raises(EconConfigError, match='MVIBE_ECON_MMHMEM_BIN'):
        econ._validate_binary(str(binary), lambda path: False)
    econ._validate_binary(str(binary), lambda path: True)  # the predicate decides


@pytest.mark.skipif(sys.platform == 'win32',
                    reason='Windows os.access X_OK is true for any existing file')
def test_non_executable_binary_raises_on_posix(tmp_path):
    binary = tmp_path / 'mmhmem'
    binary.write_text('#!/bin/sh\n', encoding='utf-8')
    binary.chmod(0o644)
    with pytest.raises(EconConfigError, match='MVIBE_ECON_MMHMEM_BIN'):
        EconConfig.from_env({'MVIBE_ECON_MMHMEM_BIN': str(binary)})


def test_hard_excluded_tool_dropped_with_exactly_one_warning(capsys):
    config = EconConfig.from_env(valid_env(MVIBE_ECON_TOOLS='read,bash'))
    assert config.tools == frozenset({'read'})
    err = capsys.readouterr().err
    assert 'WARNING' in err
    assert 'bash' in err
    assert err.count('\n') == 1  # one warning, not one per entry


def test_mmhmem_prefixed_and_case_variants_dropped_and_named(capsys):
    config = EconConfig.from_env(valid_env(
        MVIBE_ECON_TOOLS='read,mmhmem_shunt,BASH,Edit'))
    assert config.tools == frozenset({'read'})
    err = capsys.readouterr().err
    for dropped in ('mmhmem_shunt', 'bash', 'edit'):
        assert dropped in err
    assert err.count('\n') == 1
