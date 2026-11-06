"""Token economics: summarize historical file-read results through mmhmem.

Off by default: while MVIBE_ECON_MMHMEM_BIN is unset or blank, EconConfig.from_env
returns None, no economics state exists, and the proxy relays request bodies
exactly as before. When the binary is configured, everything is validated here —
binary path, numeric bounds, and the tool allowlist — so the rest of the
economics path can trust the config. Hard-excluded tool names (editors, shells,
and anything mmhmem itself serves) are never eligible in v1; they are dropped
from the allowlist with one startup warning naming them.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
import sys

# --- config (Requirements 1.1, 1.2, 1.3, 2.5) -------------------------------

DEFAULT_PROJECT = 'mvibe'
DEFAULT_TOOLS = frozenset({'read'})
DEFAULT_MIN_BYTES = 4096
DEFAULT_TIMEOUT_MS = 45_000
DEFAULT_MEMO_MAX = 2_000
DEFAULT_RETRY_S = 300
DEFAULT_CONCURRENCY = 2

# Requirement 2.5: tool results that are never eligible in v1, even when an
# operator lists them; names starting with 'mmhmem' are excluded as well.
HARD_EXCLUDED = frozenset({
    'edit', 'write', 'bash', 'shell', 'execute', 'patch', 'multiedit',
})


class EconConfigError(ValueError):
    """A MVIBE_ECON_* setting is malformed; economics must not start."""


def _is_hard_excluded(name: str) -> bool:
    lowered = name.lower()
    return lowered in HARD_EXCLUDED or lowered.startswith('mmhmem')


def _default_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _validate_binary(path: str,
                     executable_file: Callable[[str], bool] | None = None) -> None:
    """Raise EconConfigError unless `path` is an existing executable file.

    The injectable predicate keeps the not-executable branch testable on
    Windows, where os.access reports X_OK for any existing file.
    """
    is_executable_file = executable_file or _default_executable_file
    if not is_executable_file(path):
        raise EconConfigError(
            f'MVIBE_ECON_MMHMEM_BIN {path!r} is not an existing executable file; '
            'fix the path or unset MVIBE_ECON_MMHMEM_BIN to disable economics')


def _int_setting(env: Mapping[str, str], key: str, default: int, minimum: int) -> int:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    text = raw.strip()
    try:
        value = int(text)
    except ValueError:
        raise EconConfigError(f'{key} must be an integer, got {text!r}') from None
    if value < minimum:
        raise EconConfigError(f'{key} must be >= {minimum}, got {value}')
    return value


def _tools_setting(env: Mapping[str, str]) -> frozenset[str]:
    """Allowlist as lowercase names; hard-excluded entries drop with one warning."""
    raw = env.get('MVIBE_ECON_TOOLS')
    if raw is None or not raw.strip():
        return DEFAULT_TOOLS
    names = [name.strip().lower() for name in raw.split(',') if name.strip()]
    dropped = sorted({name for name in names if _is_hard_excluded(name)})
    if dropped:
        print('WARNING: MVIBE_ECON_TOOLS ignores hard-excluded tool names, '
              f'which are never eligible in v1: {", ".join(dropped)}',
              file=sys.stderr)
    return frozenset(name for name in names if not _is_hard_excluded(name))


@dataclass(frozen=True, slots=True)
class EconConfig:
    """Validated MVIBE_ECON_* settings; tool names match case-insensitively."""

    mmhmem_bin: str
    project: str
    tools: frozenset[str]
    min_bytes: int
    timeout_ms: int
    memo_max: int
    retry_s: int
    concurrency: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EconConfig | None:
        """Return None (economics disabled) or a fully validated config."""
        if env is None:
            env = os.environ
        raw_bin = env.get('MVIBE_ECON_MMHMEM_BIN')
        if raw_bin is None or not raw_bin.strip():
            return None
        mmhmem_bin = raw_bin.strip()
        _validate_binary(mmhmem_bin)
        return cls(
            mmhmem_bin=mmhmem_bin,
            project=(env.get('MVIBE_ECON_PROJECT') or '').strip() or DEFAULT_PROJECT,
            tools=_tools_setting(env),
            min_bytes=_int_setting(env, 'MVIBE_ECON_MIN_BYTES',
                                   DEFAULT_MIN_BYTES, minimum=256),
            timeout_ms=_int_setting(env, 'MVIBE_ECON_TIMEOUT_MS',
                                    DEFAULT_TIMEOUT_MS, minimum=1),
            memo_max=_int_setting(env, 'MVIBE_ECON_MEMO_MAX',
                                  DEFAULT_MEMO_MAX, minimum=1),
            retry_s=_int_setting(env, 'MVIBE_ECON_RETRY_S',
                                 DEFAULT_RETRY_S, minimum=0),
            concurrency=_int_setting(env, 'MVIBE_ECON_CONCURRENCY',
                                     DEFAULT_CONCURRENCY, minimum=1),
        )
