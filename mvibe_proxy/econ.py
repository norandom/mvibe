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
import hashlib
import json
import os
import re
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


# --- eligibility (Requirements 2.1–2.6) ---------------------------------------
#
# Pure functions over the upstream-shaped `messages` list (the OpenAI shape
# every dialect has been translated into before economics runs: assistant
# messages carry `tool_calls` [{id, function: {name, arguments}}], and each
# result is a role:'tool' message with `tool_call_id` + string content).

READ_HEADER = re.compile(r'^Read file (?P<path>.+?), lines (?P<a>\d+)-(?P<b>\d+)\s*$', re.M)

#: Argument keys consulted, in order, when a result carries no read header.
PATH_ARGUMENT_KEYS = ('filePath', 'file_path', 'path')


@dataclass(frozen=True, slots=True)
class Candidate:
    """One eligible historical file-read result."""

    index: int            # position in messages
    tool_call_id: str
    tool: str             # lower-cased name
    content: str
    sha256: str
    line_count: int
    source_hint: str | None


def _is_tool_message(message: object) -> bool:
    return isinstance(message, dict) and message.get('role') == 'tool'


def _parsed_arguments(raw: object) -> dict:
    """OpenAI `function.arguments` JSON text -> dict; {} when unparseable."""
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def correlate(messages: list[dict]) -> dict[str, tuple[str, dict]]:
    """Map every tool_call_id -> (lower-cased tool name, parsed arguments).

    Scans the assistant messages' `tool_calls` in order; a duplicate id keeps
    the last occurrence. Arguments that are not a JSON object become {}.
    """
    calls: dict[str, tuple[str, dict]] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            continue
        tool_calls = message.get('tool_calls')
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get('id')
            function = call.get('function')
            if not isinstance(call_id, str) or not isinstance(function, dict):
                continue
            name = function.get('name')
            if not isinstance(name, str):
                continue
            calls[call_id] = (name.lower(), _parsed_arguments(function.get('arguments')))
    return calls


def trailing_start(messages: list[dict]) -> int:
    """Index where the trailing contiguous run of tool-result messages begins.

    len(messages) when the list does not end with tool results (an empty
    trailing group), e.g. it ends with a user message or with the assistant's
    tool_calls still awaiting their results.
    """
    index = len(messages)
    while index > 0 and _is_tool_message(messages[index - 1]):
        index -= 1
    return index


def _source_hint(content: str, arguments: Mapping[str, object]) -> str | None:
    """First 'Read file <path>, lines A-B' header, else the call's path argument."""
    header = READ_HEADER.search(content)
    if header is not None:
        return header.group('path')
    for key in PATH_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            return value
    return None


def select(messages: list[dict], config: EconConfig) -> list[Candidate]:
    """Return the eligible historical file-read results, in message order.

    Never mutates `messages`. A result is eligible only when it sits before
    the trailing group, correlates to a tool call, is allowlisted and not
    hard-excluded, has string content of at least `config.min_bytes` UTF-8
    bytes; its source hint comes from the read header or the call's path
    argument (Requirements 2.1–2.3, 2.5, 2.6).
    """
    calls = correlate(messages)
    cutoff = trailing_start(messages)
    candidates: list[Candidate] = []
    for index, message in enumerate(messages[:cutoff]):
        if not _is_tool_message(message):
            continue
        call_id = message.get('tool_call_id')
        correlated = calls.get(call_id) if isinstance(call_id, str) else None
        if correlated is None:
            # A tool result without a resolvable call id is never eligible:
            # the Ollama translation drops tool ids entirely, so its results
            # cannot be correlated to a tool name (Requirement 2.4).
            continue
        tool, arguments = correlated
        # config.tools is lower-cased at config time and hard-excluded names
        # are dropped there; select defends both rules on its own (Req 2.5).
        if tool not in config.tools or _is_hard_excluded(tool):
            continue
        content = message.get('content')
        if not isinstance(content, str):
            continue
        encoded = content.encode('utf-8')
        if len(encoded) < config.min_bytes:
            continue
        candidates.append(Candidate(
            index=index,
            tool_call_id=call_id,
            tool=tool,
            content=content,
            sha256=hashlib.sha256(encoded).hexdigest(),
            line_count=len(content.splitlines()),
            source_hint=_source_hint(content, arguments),
        ))
    return candidates
