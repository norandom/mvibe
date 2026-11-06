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

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Mapping
import contextlib
from dataclasses import dataclass
import hashlib
import json
import os
import re
import sys
import time
from typing import Literal

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


# --- memo (Requirements 3.3, 4.3, 4.4) -----------------------------------------
#
# Deterministic in-process outcomes keyed by sha256 content hash. For a
# summary the memo stores the final rendered replacement text — exactly what
# the rewrite emits — fixed once stored, so repeated reads of the same hash
# are byte-identical by construction for the life of the process (Req 3.3).
# Bypass and failure outcomes carry their reason and a timestamp; a failed
# hash is not retried before the configured back-off elapses (Req 4.3). The
# store is bounded: at capacity the least-recently-used entry is evicted,
# where both get() and put() count as a use (Req 4.4). Eviction only means
# one further raw relay before a re-warm; it is never an error.


@dataclass(frozen=True, slots=True)
class Outcome:
    """What became of one content hash; frozen so stored entries never drift."""

    kind: Literal['summary', 'bypassed', 'failed']
    summary: str | None = None  # rendered replacement (marker + summary), fixed once stored
    reason: str | None = None   # bypass reason or error class name
    at: float = 0.0             # monotonic time, for back-off


class Memo:
    """Bounded LRU of Outcome entries keyed by sha256 hex.

    Pure in-memory state on a single event loop (no locks; the sync apply
    path and the async warm path share this object on that loop). Each method
    is a one-step OrderedDict operation, so re-entrancy cannot corrupt the
    LRU order. `max_entries` arrives validated (>= 1) as EconConfig.memo_max.
    """

    def __init__(self, max_entries: int):
        self._max_entries = max_entries
        self._entries: OrderedDict[str, Outcome] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, sha256: str) -> Outcome | None:
        """Return the stored outcome, touching its LRU position; None when absent."""
        outcome = self._entries.get(sha256)
        if outcome is not None:
            self._entries.move_to_end(sha256)
        return outcome

    def put(self, sha256: str, outcome: Outcome) -> None:
        """Store `outcome` for `sha256` as the most-recently-used entry.

        An existing key is refreshed in place — new value, new position —
        without growing the store; a new key first evicts the
        least-recently-used entry when the store is at capacity.
        """
        if sha256 in self._entries:
            del self._entries[sha256]
        elif len(self._entries) >= self._max_entries:
            self._entries.popitem(last=False)
        self._entries[sha256] = outcome

    def retry_due(self, outcome: Outcome, retry_s: int, now: float) -> bool:
        """True when a failed outcome may be refetched at `now` (Req 4.3).

        Non-failed outcomes have nothing to retry, so they report due; a
        failed one becomes due once `retry_s` has elapsed since `outcome.at`.
        """
        if outcome.kind != 'failed':
            return True
        return now >= outcome.at + retry_s


# --- transport: supervised stdio JSON-RPC client (Requirements 1.3, 4.1–4.3, 4.6) --
#
# The minimal MCP client for exactly one tool over the mmhmem child's pipes.
# Framing is one JSON-RPC message per '\n'-terminated UTF-8 line — the framing
# rmcp's stdio transport uses, pinned by mmhmem's own stdio test harness
# (Amendment 1, D3). Production start() spawns `[bin, 'stdio']` with piped
# stdio and supervises it; injected reader/writer streams replace the child
# entirely, so tests exercise the protocol in-process and never spawn.
# Every failure is one sanitized error type: a message never carries request
# content, source hints, or paths — only a short tag plus a JSON-RPC error
# code, an errno, or the child's own bounded tool-error text.

MCP_PROTOCOL_VERSION = '2025-06-18'
AGENT_HARNESS = 'mvibe-proxy'
SHUNT_TOOL = 'shunt_content'

#: Bounds fixed by the design: the initialize handshake and the capped
#: exponential restart ladder (first death waits 1 s, each further death
#: doubles the wait, never past 30 s; a healthy round trip resets it).
HANDSHAKE_TIMEOUT_S = 5.0
RESTART_BACKOFF_S = 1.0
RESTART_BACKOFF_MAX_S = 30.0

_TOOL_MESSAGE_LIMIT = 120
_SHUNT_OUTCOMES = frozenset({'summarized', 'cached', 'bypassed'})


class McpError(RuntimeError):
    """A transport, protocol, or tool error from the stdio child.

    Sanitized by construction: the message never contains request content,
    source hints, file paths, or child stderr — only a class tag plus a
    JSON-RPC error code, an errno, or the child's bounded tool-error text.
    """


@dataclass(frozen=True, slots=True)
class ShuntResult:
    """One companion-contract shunt_content outcome (absent fields omitted)."""

    outcome: Literal['summarized', 'cached', 'bypassed']
    summary: str | None
    bypass_reason: str | None
    content_sha256: str
    line_count: int
    avoided_input_tokens_est: int


def _sanitize_message(text: str) -> str:
    """One bounded line: whitespace collapsed, length capped, nothing else."""
    collapsed = ' '.join(text.split())
    if len(collapsed) > _TOOL_MESSAGE_LIMIT:
        collapsed = collapsed[:_TOOL_MESSAGE_LIMIT - 3] + '...'
    return collapsed or '<no message>'


def _capped_backoff(previous: float) -> float:
    """Next restart delay: double the previous, capped at 30 s (1 s -> 30 s)."""
    return min(previous * 2, RESTART_BACKOFF_MAX_S)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _tool_error(result: dict) -> McpError:
    """The child reported isError: surface its bounded text, sanitized."""
    content = result.get('content')
    text = ''
    if (isinstance(content, list) and content
            and isinstance(content[0], dict)
            and isinstance(content[0].get('text'), str)):
        text = content[0]['text']
    return McpError(f'tool: {_sanitize_message(text)}')


def _parse_shunt_result(result: object) -> ShuntResult:
    """Validate one tools/call result against the companion contract.

    Shape violations raise a sanitized McpError(protocol); an `isError`
    result raises a sanitized McpError(tool). Absent optional inner fields
    (summary, bypass_reason, latency_ms, worker_model) are tolerated.
    """
    if not isinstance(result, dict):
        raise McpError('protocol: tools/call result is not an object')
    if result.get('isError'):
        raise _tool_error(result)
    content = result.get('content')
    block = content[0] if isinstance(content, list) and content else None
    if not isinstance(block, dict) or not isinstance(block.get('text'), str):
        raise McpError('protocol: tools/call result has no text content block')
    try:
        inner = json.loads(block['text'])
    except ValueError:
        raise McpError('protocol: text content block is not one JSON object') from None
    if not isinstance(inner, dict):
        raise McpError('protocol: text content block is not one JSON object') from None
    outcome = inner.get('outcome')
    sha256 = inner.get('content_sha256')
    line_count = inner.get('line_count')
    est = inner.get('avoided_input_tokens_est')
    if (not isinstance(outcome, str) or outcome not in _SHUNT_OUTCOMES
            or not isinstance(sha256, str)
            or not _is_int(line_count) or not _is_int(est)):
        raise McpError('protocol: malformed shunt_content result fields')
    summary = inner.get('summary')
    bypass_reason = inner.get('bypass_reason')
    return ShuntResult(
        outcome=outcome,
        summary=summary if isinstance(summary, str) else None,
        bypass_reason=bypass_reason if isinstance(bypass_reason, str) else None,
        content_sha256=sha256,
        line_count=line_count,
        avoided_input_tokens_est=est,
    )


class StdioMcpClient:
    """Minimal MCP JSON-RPC client for a supervised `mmhmem stdio` child.

    Production: `await start()` spawns `[bin_path, 'stdio']` with piped stdio,
    runs the initialize handshake, and supervises the child — an abnormal exit
    fails in-flight calls fast and schedules a restart with capped exponential
    back-off (1 s -> 30 s); an idle healthy child is left running. Tests inject
    the reader/writer streams instead, which replaces the child entirely: no
    subprocess is ever spawned, and a "restart" stays recorded state.

    The client never logs request bodies or child stderr (stderr is drained
    and discarded); error messages carry a JSON-RPC code or an errno only.
    """

    def __init__(self, bin_path: str,
                 reader: asyncio.StreamReader | None = None,
                 writer: asyncio.StreamWriter | None = None):
        if (reader is None) != (writer is None):
            raise ValueError('reader and writer must be injected together')
        self._bin_path = bin_path
        self._injected_reader = reader
        self._injected_writer = writer
        self._injected = reader is not None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._restart_task: asyncio.Task | None = None
        self._waiters: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._connected = False
        self._handshaked = False
        self._closed = False
        self._backoff_s = RESTART_BACKOFF_S
        #: Back-off chosen per recorded restart (observable supervision state).
        self.restart_backoffs: list[float] = []
        #: Monotonic deadline before which no re-spawn may happen (None: none due).
        self.restart_at: float | None = None

    # -- lifecycle --------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the child and initialize; a healthy child is left running.

        Idempotent while connected. After a termination, a spawn-mode start
        waits out the remaining back-off, then re-spawns; with injected
        streams the restart stays recorded — the test owns any replacement.
        """
        if self._closed or self._connected:
            return
        if self._injected:
            if self._handshaked:
                return  # injected streams are fixed: nothing to re-spawn
            await self._connect(self._injected_reader, self._injected_writer)
            return
        if self.restart_at is not None:
            remaining = self.restart_at - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
        restart_task, self._restart_task = self._restart_task, None
        if restart_task is not None and not restart_task.done():
            restart_task.cancel()
            await asyncio.gather(restart_task, return_exceptions=True)
        await self._spawn_and_connect()

    async def shunt_content(self, *, project: str, content: str, kind: str,
                            source_hint: str | None, timeout_ms: int) -> ShuntResult:
        """One tools/call shunt_content round trip; the answer is matched by id."""
        if self._closed:
            raise McpError('terminated: the client is shut down')
        if not self._connected:
            raise McpError('terminated: the mmhmem stdio child is not running')
        request_id = self._next_id + 1
        self._next_id = request_id
        waiter = self._register_waiter(request_id)
        arguments: dict[str, object] = {
            'project': project,
            'agent_harness': AGENT_HARNESS,
            'content': content,
            'kind': kind,
        }
        if source_hint is not None:
            arguments['source_hint'] = source_hint
        request = {'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/call',
                   'params': {'name': SHUNT_TOOL, 'arguments': arguments}}
        try:
            await self._send(request, waiter)
            result = await asyncio.wait_for(waiter, timeout_ms / 1000)
        except asyncio.TimeoutError:
            raise McpError(
                f'timeout: no shunt_content response within {timeout_ms} ms') from None
        finally:
            self._waiters.pop(request_id, None)
        shunt = _parse_shunt_result(result)
        self._backoff_s = RESTART_BACKOFF_S  # a healthy round trip resets the ladder
        return shunt

    async def shutdown(self, timeout_s: float = 2.0) -> None:
        """Close stdin, wait <= timeout_s for exit, then terminate; idempotent."""
        if self._closed:
            return
        self._closed = True
        restart_task, self._restart_task = self._restart_task, None
        if restart_task is not None and not restart_task.done():
            restart_task.cancel()
            await asyncio.gather(restart_task, return_exceptions=True)
        writer, self._writer = self._writer, None
        proc, self._proc = self._proc, None
        tasks = [task for task in (self._reader_task, self._stderr_task, self._watch_task)
                 if task is not None]
        self._reader_task = self._stderr_task = self._watch_task = None
        if writer is not None:
            try:
                writer.write_eof()  # a well-behaved child exits on stdin EOF
            except (OSError, RuntimeError):
                pass
        if proc is not None:
            await self._await_exit(proc, timeout_s)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._connected = False
        self._fail_inflight('terminated: the client shut down')
        if writer is not None:
            try:
                writer.close()
            except (OSError, RuntimeError):
                pass
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass

    # -- transport setup ----------------------------------------------------------

    async def _spawn_and_connect(self) -> None:
        proc = await self._spawn()
        stderr_task = asyncio.create_task(self._drain_stderr(proc))
        self._proc = proc
        self._stderr_task = stderr_task
        try:
            await self._connect(proc.stdout, proc.stdin)
        except BaseException:
            self._proc = None
            self._stderr_task = None
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            await self._await_exit(proc, 1.0)
            raise
        self._watch_task = asyncio.create_task(self._watch_exit(proc))

    async def _spawn(self) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                self._bin_path, 'stdio',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            # Sanitized per the design: the exception class and errno only,
            # never the path (str(exc) of FileNotFoundError names the path).
            raise McpError(
                f'spawn: {type(exc).__name__} errno {getattr(exc, "errno", None)}'
            ) from None

    async def _connect(self, reader: asyncio.StreamReader,
                       writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._connected = True
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await self._handshake()
        except BaseException:
            reader_task, self._reader_task = self._reader_task, None
            if reader_task is not None:
                reader_task.cancel()
                await asyncio.gather(reader_task, return_exceptions=True)
            self._connected = False
            self._fail_inflight('terminated: the mmhmem stdio child exited')
            raise

    async def _handshake(self) -> None:
        """initialize -> capabilities -> notifications/initialized, in order."""
        request_id = self._next_id + 1
        self._next_id = request_id
        waiter = self._register_waiter(request_id)
        request = {'jsonrpc': '2.0', 'id': request_id, 'method': 'initialize',
                   'params': {'protocolVersion': MCP_PROTOCOL_VERSION,
                              'capabilities': {},
                              'clientInfo': {'name': 'mvibe-econ', 'version': '1'}}}
        try:
            await self._send(request, waiter)
            result = await asyncio.wait_for(waiter, HANDSHAKE_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise McpError('timeout: no initialize response within '
                           f'{HANDSHAKE_TIMEOUT_S:g} s') from None
        finally:
            self._waiters.pop(request_id, None)
        if not isinstance(result, dict) or not isinstance(result.get('capabilities'), dict):
            raise McpError('protocol: initialize response has no capabilities object')
        await self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, None)
        self._handshaked = True

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """Drain and discard child stderr (never logged; mmhmem logs to files)."""
        try:
            while await proc.stderr.readline():
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _await_exit(self, proc: asyncio.subprocess.Process,
                          timeout_s: float) -> None:
        """Wait <= timeout_s for exit, then terminate, then kill."""
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
            return
        except asyncio.TimeoutError:
            pass
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout_s)
            return
        except asyncio.TimeoutError:
            pass
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
            await proc.wait()

    # -- protocol pump and supervision ---------------------------------------------

    async def _read_loop(self) -> None:
        """Pump stdout lines to waiters; EOF or a broken pipe ends the loop."""
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break  # EOF: the child closed its stdout
                text = line.decode('utf-8', 'replace').strip()
                if text:
                    self._dispatch(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # any read failure is a termination, handled below
        finally:
            self._handle_termination()

    def _dispatch(self, text: str) -> None:
        """Route one message: only client-originated ids are ever awaited."""
        try:
            message = json.loads(text)
        except ValueError:
            self._fail_inflight('protocol: non-JSON line on stdout')
            return
        if not isinstance(message, dict):
            self._fail_inflight('protocol: stdout line is not a JSON-RPC object')
            return
        message_id = message.get('id')
        if message_id is None or isinstance(message_id, (bool, list, dict)):
            return  # a server notification is never answered or awaited
        waiter = self._waiters.get(message_id)
        if waiter is None or waiter.done():
            return  # a late answer to an already-timed-out or failed call
        error = message.get('error')
        if isinstance(error, dict):
            waiter.set_exception(McpError(f"rpc error {error.get('code')}"))
        else:
            waiter.set_result(message.get('result'))

    def _register_waiter(self, request_id: int) -> asyncio.Future:
        waiter = asyncio.get_running_loop().create_future()
        self._waiters[request_id] = waiter
        return waiter

    async def _send(self, payload: dict, waiter: asyncio.Future | None) -> None:
        writer = self._writer
        if writer is None:
            raise McpError('terminated: the stdio pipe is closed')
        line = (json.dumps(payload) + '\n').encode('utf-8')
        try:
            writer.write(line)
            await writer.drain()
        except OSError:
            if waiter is not None and not waiter.done():
                waiter.cancel()  # never leave an unretrieved exception behind
            self._handle_termination()
            raise McpError('terminated: the stdio pipe is closed') from None

    def _fail_inflight(self, message: str) -> None:
        """Fail every pending call with a fresh sanitized McpError."""
        for request_id in list(self._waiters):
            waiter = self._waiters.pop(request_id)
            if not waiter.done():
                waiter.set_exception(McpError(message))

    def _handle_termination(self) -> None:
        """The transport ended: fail in-flight calls fast, schedule a restart."""
        was_connected = self._connected
        self._connected = False
        self._fail_inflight('terminated: the mmhmem stdio child exited')
        if self._closed or not was_connected or not self._handshaked:
            return  # expected at shutdown / one death reported twice / never healthy
        self._schedule_restart()

    def _schedule_restart(self) -> None:
        """Record the capped back-off; spawn mode re-spawns once it elapses."""
        delay = self._backoff_s
        self._backoff_s = _capped_backoff(delay)
        self.restart_at = time.monotonic() + delay
        self.restart_backoffs.append(delay)
        if self._injected or self._closed:
            return  # injected streams are test-owned: the record is the restart
        self._restart_task = asyncio.create_task(self._restart_after(delay))

    async def _restart_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._closed or self._connected:
            return
        try:
            await self._spawn_and_connect()
        except Exception:
            self._schedule_restart()  # keep backing off; callers fail open meanwhile

    async def _watch_exit(self, proc: asyncio.subprocess.Process) -> None:
        """Fail in-flight calls the moment the child dies, not just at EOF."""
        try:
            await proc.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        self._handle_termination()
