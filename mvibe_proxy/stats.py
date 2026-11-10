"""Proxy stats: passive upstream usage observation, in-process counters,
throttled propagation to mmhmem and the `mmhmem stats --json` view.

Inert by default (Requirement 5.2): while MVIBE_STATS_USAGE is unset and no
mmhmem binary is configured, StatsConfig.from_env yields the default config
and nothing is reported; the proxy still keeps its in-process counters for
GET /stats. Configuration, the observer, the ledger and the counters are
pure and in-memory: no I/O, no clocks other than the injected one. Only
UsageReporter (tool calls through an injected stdio client) and
mmhmem_stats (one bounded subprocess per call) perform I/O.

Dependency direction is diagnostics -> stats -> proxy: this module reads
RequestDiagnostic records; diagnostics and streaming never import it, and it
types the mmhmem stdio client as the ToolCaller protocol instead of
importing econ. Nothing here retains content. The observer keeps only the
two usage numbers and the request's model id (Requirement 1.6), the
reporter sends numbers and model labels only, and a counters snapshot holds fixed
keys, counts, outcome and status strings and model ids only (Requirement 3.4).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
import time
from types import MappingProxyType
from typing import Protocol
import uuid

from mvibe_proxy.diagnostics import RequestDiagnostic

# --- config (Requirements 2.5, 2.7, 5.2) -------------------------------------

ENV_USAGE = 'MVIBE_STATS_USAGE'
ENV_REPORT_INTERVAL_S = 'MVIBE_STATS_REPORT_INTERVAL_S'
ENV_MMHMEM_TIMEOUT_S = 'MVIBE_STATS_MMHMEM_TIMEOUT_S'
ENV_MMHMEM_BIN = 'MVIBE_STATS_MMHMEM_BIN'
# Shared with economics: the fallback binary and the project both savings
# and spend are recorded under. Reading them never enables economics (2.7).
ENV_ECON_MMHMEM_BIN = 'MVIBE_ECON_MMHMEM_BIN'
ENV_PROJECT = 'MVIBE_ECON_PROJECT'

DEFAULT_REPORT_INTERVAL_S = 60.0
DEFAULT_MMHMEM_TIMEOUT_S = 10.0
DEFAULT_PROJECT = 'mvibe'  # the economics default (econ.DEFAULT_PROJECT)

_ENABLED = frozenset({'1', 'true', 'yes', 'on'})
_DISABLED = frozenset({'0', 'false', 'no', 'off'})


class StatsConfigError(ValueError):
    """A MVIBE_STATS_* setting is malformed. Messages name settings, never values."""


def _setting(env: Mapping[str, str], key: str) -> str | None:
    """The stripped value of `key`, or None when it is unset or blank."""
    raw = env.get(key)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _is_executable_file(path: str) -> bool:
    """The binary rule shared with economics: an existing executable file.

    Module-level so tests can prove the not-executable branch on Windows,
    where os.access reports X_OK for any existing file.
    """
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _usage_setting(env: Mapping[str, str]) -> bool:
    text = _setting(env, ENV_USAGE)
    if text is None:
        return False
    switch = text.lower()
    if switch in _ENABLED:
        return True
    if switch in _DISABLED:
        return False
    raise StatsConfigError(
        f'{ENV_USAGE} must be 1, true, yes or on to enable usage propagation, '
        'or 0, false, no or off to disable it')


def _positive_seconds_setting(env: Mapping[str, str], key: str, default: float) -> float:
    text = _setting(env, key)
    if text is None:
        return default
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        raise StatsConfigError(f'{key} must be a positive number of seconds')
    return value


def _same_file(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _binary_setting(env: Mapping[str, str]) -> str | None:
    """MVIBE_STATS_MMHMEM_BIN, else MVIBE_ECON_MMHMEM_BIN; validated when set."""
    stats_bin = _setting(env, ENV_MMHMEM_BIN)
    econ_bin = _setting(env, ENV_ECON_MMHMEM_BIN)
    if (stats_bin is not None and econ_bin is not None
            and _same_file(stats_bin) != _same_file(econ_bin)):
        raise StatsConfigError(
            f'{ENV_MMHMEM_BIN} and {ENV_ECON_MMHMEM_BIN} name different binaries; '
            f'point both at the same mmhmem executable or unset {ENV_MMHMEM_BIN} '
            f'to reuse {ENV_ECON_MMHMEM_BIN}')
    if stats_bin is not None:
        key, path = ENV_MMHMEM_BIN, stats_bin
    elif econ_bin is not None:
        key, path = ENV_ECON_MMHMEM_BIN, econ_bin
    else:
        return None
    if not _is_executable_file(path):
        raise StatsConfigError(
            f'{key} is not an existing executable file; fix the path or unset {key}')
    return path


@dataclass(frozen=True, slots=True)
class StatsConfig:
    """Validated MVIBE_STATS_* settings. The defaults are the inert configuration.

    `usage` switches propagation to mmhmem on; it never enables economics,
    which keeps its own opt-in switch (Requirement 2.7). `mmhmem_bin` serves
    propagation and the `/stats` mmhmem view.
    """

    usage: bool = False
    report_interval_s: float = DEFAULT_REPORT_INTERVAL_S
    mmhmem_timeout_s: float = DEFAULT_MMHMEM_TIMEOUT_S
    mmhmem_bin: str | None = None
    project: str = DEFAULT_PROJECT

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StatsConfig:
        """Return the validated config; unset or blank settings take their defaults.

        `os.environ` is read only when `env` is None. Raises StatsConfigError
        for an unrecognized switch, a non-positive or non-finite number, a
        binary that is not an existing executable file (naming its key), two
        different binaries (naming both keys), and enabled usage without any
        binary (naming MVIBE_STATS_USAGE and MVIBE_STATS_MMHMEM_BIN).
        """
        if env is None:
            env = os.environ
        usage = _usage_setting(env)
        report_interval_s = _positive_seconds_setting(
            env, ENV_REPORT_INTERVAL_S, DEFAULT_REPORT_INTERVAL_S)
        mmhmem_timeout_s = _positive_seconds_setting(
            env, ENV_MMHMEM_TIMEOUT_S, DEFAULT_MMHMEM_TIMEOUT_S)
        mmhmem_bin = _binary_setting(env)
        if usage and mmhmem_bin is None:
            raise StatsConfigError(
                f'{ENV_USAGE} is enabled but no mmhmem binary is configured; set '
                f'{ENV_MMHMEM_BIN} (or {ENV_ECON_MMHMEM_BIN}) to the mmhmem executable '
                f'or unset {ENV_USAGE}')
        return cls(
            usage=usage,
            report_interval_s=report_interval_s,
            mmhmem_timeout_s=mmhmem_timeout_s,
            mmhmem_bin=mmhmem_bin,
            project=_setting(env, ENV_PROJECT) or DEFAULT_PROJECT,
        )


# --- usage observation (Requirements 1.1, 1.2, 1.4, 1.5, 1.6) ----------------

@dataclass(frozen=True, slots=True)
class Usage:
    """One upstream response's token usage: numbers and the request's model only."""

    prompt_tokens: int
    completion_tokens: int  # 0 for embeddings
    model: str | None


def _count(value: object) -> int | None:
    """`value` when it is a non-negative int (never a bool), else None."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _usage_numbers(payload: object) -> tuple[int, int] | None:
    """(prompt_tokens, completion_tokens) from a decoded body or SSE event.

    Both must be non-negative ints. A missing or null `completion_tokens`
    counts as 0 when `prompt_tokens` is present (embeddings); anything else
    in the payload is ignored and nothing of it is kept.
    """
    if not isinstance(payload, dict):
        return None
    usage = payload.get('usage')
    if not isinstance(usage, dict):
        return None
    prompt = _count(usage.get('prompt_tokens'))
    if prompt is None:
        return None
    raw_completion = usage.get('completion_tokens')
    if raw_completion is None:
        return prompt, 0
    completion = _count(raw_completion)
    if completion is None:
        return None
    return prompt, completion


def _parse_usage(data: bytes | bytearray) -> tuple[int, int] | None:
    """Usage numbers from one JSON document; undecodable input yields None.

    Only decoding errors (ValueError, which covers JSONDecodeError and
    UnicodeDecodeError) are expected here; any other exception propagates
    to the observer's guard and disables it for the response.
    """
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    return _usage_numbers(payload)


class _Overflow(Exception):
    """The observer's buffer would exceed JSON_LIMIT."""


_USAGE_KEY = b'"usage"'
_EVENT_STREAM = 'text/event-stream'


class UsageObserver:
    """Read-only tap extracting `usage` from one upstream response.

    Server-sent events (content type `text/event-stream`) are line-buffered;
    `data:` lines are joined per event and only an event containing
    `"usage"` is decoded, the last valid usage winning. Any other body is
    accumulated up to JSON_LIMIT bytes and decoded once at finish(). The
    same bound applies to an SSE partial line plus the current event.
    Exceeding the bound, or any unexpected internal error, disables the
    observer for the rest of the response: it then yields None. A malformed
    SSE usage event is skipped. `feed` never raises, performs no I/O and
    never mutates its input; buffers are released when the observer is
    disabled or finished, so only the numbers and the model remain.
    """

    JSON_LIMIT = 4 * 1024 * 1024

    def __init__(self, *, content_type: str | None, model: str | None) -> None:
        self._model = model
        self._sse = (isinstance(content_type, str)
                     and content_type.strip().lower().startswith(_EVENT_STREAM))
        # SSE: the current partial line. JSON: the body so far.
        self._buffer = bytearray()
        # SSE: the `data:` values of the current event and their size.
        self._event: list[bytes] = []
        self._event_bytes = 0
        self._last: tuple[int, int] | None = None  # SSE: last usage seen
        self._disabled = False
        self._finished = False
        self._result: Usage | None = None

    def feed(self, chunk: bytes) -> None:
        """Observe one upstream chunk. Never raises."""
        if self._disabled or self._finished:
            return
        try:
            if self._sse:
                self._feed_sse(chunk)
            else:
                self._feed_json(chunk)
        except Exception:
            self._disable()

    def finish(self) -> Usage | None:
        """The response's usage, or None. Idempotent; never raises."""
        if self._finished:
            return self._result
        self._finished = True
        try:
            if not self._disabled:
                numbers = self._finish_sse() if self._sse else self._finish_json()
                if numbers is not None:
                    self._result = Usage(numbers[0], numbers[1], self._model)
        except Exception:
            self._disabled = True
            self._result = None
        finally:
            self._release()
        return self._result

    def as_callable(self, diagnostic: RequestDiagnostic) -> Callable[[bytes | None], None]:
        """The streaming tap: a chunk is fed; the terminal None stores the
        observed (prompt, completion) on `diagnostic.usage`, which is left
        untouched when nothing was observed. The callable never raises."""
        def observe(chunk: bytes | None) -> None:
            if chunk is not None:
                self.feed(chunk)
                return
            try:
                usage = self.finish()
                if usage is not None:
                    diagnostic.usage = (usage.prompt_tokens, usage.completion_tokens)
            except Exception:
                pass
        return observe

    # -- internals --

    def _disable(self) -> None:
        self._disabled = True
        self._release()

    def _release(self) -> None:
        self._buffer = bytearray()
        self._event = []
        self._event_bytes = 0

    def _feed_json(self, chunk: bytes) -> None:
        if len(self._buffer) + len(chunk) > self.JSON_LIMIT:
            raise _Overflow
        self._buffer += chunk

    def _finish_json(self) -> tuple[int, int] | None:
        body = self._buffer
        if _USAGE_KEY not in body:
            return None
        return _parse_usage(body)

    def _feed_sse(self, chunk: bytes) -> None:
        buffer = self._buffer
        buffer += chunk
        start = 0
        while (end := buffer.find(b'\n', start)) >= 0:
            self._line(buffer[start:end])
            start = end + 1
        if start:
            del buffer[:start]
        if len(buffer) + self._event_bytes > self.JSON_LIMIT:
            raise _Overflow

    def _finish_sse(self) -> tuple[int, int] | None:
        # A stream may end without the blank line closing its last event.
        if self._buffer:
            self._line(self._buffer)
        self._dispatch()
        return self._last

    def _line(self, line: bytes | bytearray) -> None:
        if line.endswith(b'\r'):
            line = line[:-1]
        if not line:
            self._dispatch()
            return
        if not line.startswith(b'data:'):
            return  # comments, event:, id: and retry: carry no usage
        value = line[5:]
        if value.startswith(b' '):
            value = value[1:]
        self._event.append(bytes(value))
        self._event_bytes += len(value) + 1
        if self._event_bytes > self.JSON_LIMIT:
            raise _Overflow

    def _dispatch(self) -> None:
        event = self._event
        self._event, self._event_bytes = [], 0
        if not event:
            return
        data = event[0] if len(event) == 1 else b'\n'.join(event)
        if _USAGE_KEY not in data:
            return  # the cheap check: only usage-bearing events are decoded
        numbers = _parse_usage(data)
        if numbers is not None:
            self._last = numbers


# --- ledger and counters (Requirements 1.7, 3.1, 3.4) ------------------------

UNKNOWN_MODEL = 'unknown'


def _model_id(model: object) -> str:
    return model if isinstance(model, str) and model else UNKNOWN_MODEL


class UsageLedger:
    """Cumulative usage per model for the process lifetime.

    Totals never decrease; `revision` increments on every add so the
    reporter can skip unchanged ledgers. A missing model is 'unknown'.
    """

    def __init__(self) -> None:
        self._models: dict[str, tuple[int, int]] = {}
        self._requests: dict[str, int] = {}
        self._prompt = 0
        self._completion = 0
        self._revision = 0

    def add(self, usage: Usage) -> None:
        """Add one response's usage; raises ValueError unless both are counts."""
        prompt, completion = _count(usage.prompt_tokens), _count(usage.completion_tokens)
        if prompt is None or completion is None:
            raise ValueError('usage token counts must be non-negative integers')
        model = _model_id(usage.model)
        model_prompt, model_completion = self._models.get(model, (0, 0))
        self._models[model] = (model_prompt + prompt, model_completion + completion)
        self._requests[model] = self._requests.get(model, 0) + 1
        self._prompt += prompt
        self._completion += completion
        self._revision += 1

    def totals(self) -> tuple[int, int]:
        """(prompt_tokens, completion_tokens) over all models."""
        return self._prompt, self._completion

    def by_model(self) -> Mapping[str, tuple[int, int]]:
        """A read-only snapshot of (prompt_tokens, completion_tokens) per model."""
        return MappingProxyType(dict(self._models))

    def requests_by_model(self) -> Mapping[str, int]:
        """A read-only snapshot of how many usage records each model received."""
        return MappingProxyType(dict(self._requests))

    @property
    def revision(self) -> int:
        return self._revision


_ECON_FIELDS = ('eligible', 'replaced', 'bypassed', 'pending', 'est_saved_tokens')


def _econ_number(econ: object, name: str) -> int | None:
    """One counter from EconCounters (attribute) or an allowlisted econ_* mapping.

    The saved-tokens estimate may be negative and is summed unchanged; the
    other counters must be non-negative ints.
    """
    value = econ.get(f'econ_{name}') if isinstance(econ, Mapping) else getattr(econ, name, None)
    if name == 'est_saved_tokens':
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    return _count(value)


def _milliseconds(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number) and number >= 0:
            return number
    return 0.0


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


#: Queue time from which a request counts as held by pacing or a 429 hold.
HELD_MIN_QUEUED_MS = 1.0


class ProxyCounters:
    """In-process aggregates for GET /stats, fed by the diagnostics sink.

    `observe` is registered with `diagnostics.set_counters_sink` and reads
    each finished request's outcome, status, economics counters, rate-limit
    record and usage; values that are not counts are ignored. The usage
    reporter records its outcomes through `report_succeeded(at)`
    and `report_failed()`; `last_report_at` and `report_failures` are their
    read-only views. All updates run on the event loop, so no lock is held.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._started_at = clock()
        self.usage = UsageLedger()
        self._last_report_at: float | None = None
        self._report_failures = 0
        self._total = 0
        self._by_outcome: dict[str, int] = {}
        self._by_status: dict[str, int] = {}
        self._requests_with_econ = 0
        self._econ = dict.fromkeys(_ECON_FIELDS, 0)
        self._held_requests = 0
        self._queued_ms_total = 0.0
        self._rejections = 0
        self._exhausted = 0

    @property
    def started_at(self) -> float:
        return self._started_at

    @property
    def last_report_at(self) -> float | None:
        return self._last_report_at

    @property
    def report_failures(self) -> int:
        return self._report_failures

    def report_succeeded(self, at: float) -> None:
        """Record a successful usage report at wall-clock time `at`."""
        self._last_report_at = at

    def report_failed(self) -> None:
        """Count one failed usage report."""
        self._report_failures += 1

    def observe(self, diagnostic: RequestDiagnostic) -> None:
        """The counters sink: aggregate one finished request diagnostic."""
        self._total += 1
        outcome = diagnostic.outcome
        outcome_key = outcome if isinstance(outcome, str) else 'unknown'
        self._by_outcome[outcome_key] = self._by_outcome.get(outcome_key, 0) + 1
        status = diagnostic.status
        if isinstance(status, int) and not isinstance(status, bool):
            status_key = str(status)
            self._by_status[status_key] = self._by_status.get(status_key, 0) + 1
        econ = diagnostic.econ
        if econ is not None:
            self._requests_with_econ += 1
            for name in _ECON_FIELDS:
                number = _econ_number(econ, name)
                if number is not None:
                    self._econ[name] += number
        record = diagnostic.rate_limit
        if record is not None:
            queued_ms = _milliseconds(record.queued_ms)
            rejections = _count(record.rejections) or 0
            self._queued_ms_total += queued_ms
            self._rejections += rejections
            # Sub-millisecond queue time is event-loop scheduling, not a hold.
            if queued_ms >= HELD_MIN_QUEUED_MS or rejections > 0:
                self._held_requests += 1
            if record.exhausted is True:
                self._exhausted += 1
        usage = diagnostic.usage
        if isinstance(usage, tuple) and len(usage) == 2:
            prompt, completion = _count(usage[0]), _count(usage[1])
            if prompt is not None and completion is not None:
                self.usage.add(Usage(prompt, completion, _model_id(diagnostic.model)))

    def snapshot(self) -> dict[str, object]:
        """A JSON-safe copy with the fixed key layout from the design.

        Model ids, outcome codes and status codes are the only free strings;
        hints, headers, request ids and bodies are never included.
        """
        prompt, completion = self.usage.totals()
        requests = self.usage.requests_by_model()
        by_model = {
            model: {'prompt_tokens': model_prompt, 'completion_tokens': model_completion,
                    'requests': requests.get(model, 0)}
            for model, (model_prompt, model_completion) in sorted(self.usage.by_model().items())
        }
        last_report_at = self._last_report_at
        return {
            'started_at': _iso(self._started_at),
            'requests': {
                'total': self._total,
                'by_outcome': dict(sorted(self._by_outcome.items())),
                'by_status': dict(sorted(self._by_status.items())),
            },
            'usage': {
                'prompt_tokens': prompt,
                'completion_tokens': completion,
                'by_model': by_model,
                'last_report_at': None if last_report_at is None else _iso(last_report_at),
                'report_failures': self._report_failures,
            },
            'econ': {'requests_with_econ': self._requests_with_econ, **self._econ},
            'rate_limit': {
                'held_requests': self._held_requests,
                'queued_ms_total': round(self._queued_ms_total, 2),
                'rejections': self._rejections,
                'exhausted': self._exhausted,
            },
        }


# --- propagation and the mmhmem view (Requirements 2.1-2.4, 3.2, 3.3) --------

REPORT_TOOL = 'session_resume_or_handoff'
# The same harness economics records its savings under (econ.AGENT_HARNESS);
# each model's usage is reported as the source `mvibe-proxy <model>`.
AGENT_HARNESS = 'mvibe-proxy'
DEFAULT_REPORT_TIMEOUT_MS = 10_000

_LOG = logging.getLogger('mvibe.proxy')


class ToolCaller(Protocol):
    """What the reporter needs from the mmhmem stdio client.

    `econ.StdioMcpClient` satisfies it structurally, so this module never
    imports `econ` (design dependency direction: stats names the client's
    type only).
    """

    async def call_tool(self, name: str, arguments: Mapping[str, object], *,
                        timeout_ms: int) -> object: ...


def _default_task_id(started: float) -> str:
    """`mvibe-proxy-<UTC start>-<8 hex>`: unique per process (Requirement 2.3)."""
    stamp = datetime.fromtimestamp(started, tz=timezone.utc)
    return f'{AGENT_HARNESS}-{stamp:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}'


def _log_warning(message: str) -> None:
    _LOG.warning(message)


class UsageReporter:
    """Throttled propagation of the ledger's cumulative usage to mmhmem.

    Every `interval_s` the loop checks the ledger's revision and, when it
    changed since the last fully successful report, calls `report()`: one
    `session_resume_or_handoff` per model with non-zero totals (sorted),
    carrying that model's cumulative `client_usage` for this process under
    harness `mvibe-proxy` and one task id per process, so mmhmem's
    latest-report-per-task semantics count each process once per source
    (Requirements 2.1-2.3). The call result, which may hold checkpoint text,
    is discarded unread and never logged.

    Any exception from a call is a failure (Requirement 2.4): the rest of
    that tick's sources are skipped, `failures` (the current streak) and the
    counters' `report_failures` grow, and `warn` fires once as a streak
    starts, naming the exception class only. A success resets the streak and
    records `last_report_at`. Nothing here ever raises into the caller or
    the event loop; inference never waits on it. `stop()` cancels the loop
    and makes one final report bounded by FINAL_REPORT_TIMEOUT_S.
    """

    FINAL_REPORT_TIMEOUT_S = 2.0  # Requirement 2.2: shutdown waits at most this long

    def __init__(self, client: ToolCaller, ledger: UsageLedger, *, project: str,
                 interval_s: float, counters: ProxyCounters | None = None,
                 task_id: str | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
                 warn: Callable[[str], None] | None = None,
                 timeout_ms: int = DEFAULT_REPORT_TIMEOUT_MS) -> None:
        if (isinstance(interval_s, bool) or not isinstance(interval_s, (int, float))
                or not math.isfinite(interval_s) or interval_s <= 0):
            raise ValueError('interval_s must be a positive finite number of seconds')
        self._client = client
        self._ledger = ledger
        self._project = project
        self._interval_s = float(interval_s)
        self._counters = counters
        self._clock = clock
        self._sleep = sleep
        self._warn = _log_warning if warn is None else warn
        self._timeout_ms = timeout_ms
        self._task_id = _default_task_id(clock()) if task_id is None else task_id
        self._reported_revision = 0  # a fresh ledger has nothing to report
        self._loop_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._failures = 0
        self._last_report_at: float | None = None

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def failures(self) -> int:
        """The length of the current failure streak (0 after a success)."""
        return self._failures

    @property
    def last_report_at(self) -> float | None:
        """Clock time of the last fully successful report, or None."""
        return self._last_report_at

    async def start(self) -> None:
        """Start the background loop; idempotent, and a no-op after stop()."""
        if self._stopped or self._loop_task is not None:
            return
        self._loop_task = asyncio.create_task(self._run(), name='mvibe-stats-reporter')

    async def report(self) -> bool:
        """One call per model source; True when every call succeeded. Never raises."""
        try:
            revision = self._ledger.revision
            sources = [(model, prompt, completion)
                       for model, (prompt, completion) in sorted(self._ledger.by_model().items())
                       if prompt or completion]
            for model, prompt, completion in sources:
                await self._client.call_tool(REPORT_TOOL, {
                    'project': self._project,
                    'agent_harness': AGENT_HARNESS,
                    'workspace_task_id': self._task_id,
                    'client_usage': {'prompt_tokens': prompt, 'completion_tokens': completion,
                                     'source': f'{AGENT_HARNESS} {model}'},
                }, timeout_ms=self._timeout_ms)  # the result is discarded unread
        except Exception as error:
            self._record_failure(error)
            return False
        self._reported_revision = revision
        if sources:
            self._record_success()
        return True

    async def stop(self) -> None:
        """Cancel the loop, then report once more if the ledger changed, waiting
        at most FINAL_REPORT_TIMEOUT_S for that report. Idempotent; never raises."""
        if self._stopped:
            return
        self._stopped = True
        task, self._loop_task = self._loop_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._ledger.revision == self._reported_revision:
            return
        try:
            await asyncio.wait_for(self.report(), self.FINAL_REPORT_TIMEOUT_S)
        except TimeoutError as error:
            self._record_failure(error)
        except Exception:
            pass  # report() never raises; this keeps stop() fail-safe regardless

    # -- internals --

    async def _run(self) -> None:
        while True:
            await self._sleep(self._interval_s)
            if self._ledger.revision != self._reported_revision:
                await self.report()

    def _record_success(self) -> None:
        at = self._clock()
        self._last_report_at = at
        self._failures = 0
        if self._counters is not None:
            self._counters.report_succeeded(at)

    def _record_failure(self, error: BaseException) -> None:
        self._failures += 1
        if self._counters is not None:
            self._counters.report_failed()
        if self._failures != 1:
            return  # one warning per failure streak
        # The class name only: messages could echo child or transport text.
        name = type(error).__name__
        if self._stopped:
            message = (f'Final stats report to mmhmem failed ({name}); '
                       'usage since the last report was not recorded')
        else:
            message = f'Stats report to mmhmem failed ({name}); will retry next interval'
        try:
            self._warn(message)
        except Exception:
            pass


#: `mmhmem stats --json` output beyond this many bytes is rejected (invalid_json).
MMHMEM_STATS_STDOUT_LIMIT = 8 * 1024 * 1024
# mmhmem's NO_STATS_MESSAGE (src/cli/mod.rs) starts with this text.
_NO_STATS_NOTICE = b'No token-savings statistics'
_STATS_READ_CHUNK = 64 * 1024
_STATS_REAP_TIMEOUT_S = 1.0

Spawn = Callable[..., Awaitable[asyncio.subprocess.Process]]


def _unavailable(reason: str) -> dict[str, object]:
    return {'available': False, 'reason': reason}


async def mmhmem_stats(bin_path: str | None, *, env: Mapping[str, str], timeout_s: float,
                       spawn: Spawn | None = None) -> dict[str, object]:
    """mmhmem's own statistics from `[bin_path, 'stats', '--json']` (Requirements 3.2, 3.3).

    Returns `{"available": True, "days": [...], "totals": {...}}`, or
    `{"available": False, "reason": ...}` with a fixed reason:
    `not_configured` (no binary; nothing is spawned), `spawn_failed` (the
    binary could not be started), `timeout` (no exit within `timeout_s`; the
    child is killed), `exit_<code>` (non-zero exit), `no_stats_yet` (mmhmem's
    plain-text empty-store notice) or `invalid_json` (output that is not one
    JSON object with a `days` list and a `totals` object, or more than
    MMHMEM_STATS_STDOUT_LIMIT bytes; an oversized child is killed).

    The child gets `env` (the proxy's environment, so AGENT_MEM_DATA_DIR and
    AGENT_MEM_LOG_FILE apply), no stdin, and its stderr is discarded unread:
    the result never carries stderr, environment values, paths or exception
    text. Never raises except on cancellation, which kills the child too.
    The call returns within about `timeout_s` plus a 1 s reap bound.
    `spawn` replaces `asyncio.create_subprocess_exec` (resolved per call) for
    tests; it receives the fixed argv and the subprocess keywords.
    """
    if bin_path is None:
        return _unavailable('not_configured')
    spawner = asyncio.create_subprocess_exec if spawn is None else spawn
    try:
        process = await spawner(bin_path, 'stats', '--json', env=dict(env),
                                stdin=asyncio.subprocess.DEVNULL,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.DEVNULL)
    except (OSError, ValueError):
        return _unavailable('spawn_failed')
    finished = False
    try:
        try:
            output = await asyncio.wait_for(_read_stats_output(process), timeout_s)
        except TimeoutError:
            return _unavailable('timeout')
        if output is None:
            return _unavailable('invalid_json')  # beyond MMHMEM_STATS_STDOUT_LIMIT
        finished = True
        if process.returncode != 0:
            return _unavailable(f'exit_{process.returncode}')
        return _parse_stats_output(output)
    finally:
        if not finished:
            await _abandon(process)


async def _read_stats_output(process: asyncio.subprocess.Process) -> bytes | None:
    """Stdout to EOF, then the exit; None as soon as the output exceeds the cap."""
    stdout = process.stdout
    output = bytearray()
    if stdout is not None:
        while chunk := await stdout.read(_STATS_READ_CHUNK):
            if len(output) + len(chunk) > MMHMEM_STATS_STDOUT_LIMIT:
                return None
            output += chunk
    await process.wait()
    return bytes(output)


async def _abandon(process: asyncio.subprocess.Process) -> None:
    """Kill the child, discard its pending output and reap it, bounded."""
    if process.returncode is None:
        with contextlib.suppress(OSError):  # ProcessLookupError: already gone
            process.kill()

    async def drain() -> None:
        # Unread output could keep the pipe open and `wait()` pending.
        if process.stdout is not None:
            while await process.stdout.read(_STATS_READ_CHUNK):
                pass
        await process.wait()

    with contextlib.suppress(TimeoutError, OSError):
        await asyncio.wait_for(drain(), _STATS_REAP_TIMEOUT_S)


def _parse_stats_output(output: bytes) -> dict[str, object]:
    if output.lstrip().startswith(_NO_STATS_NOTICE):
        return _unavailable('no_stats_yet')
    try:
        document = json.loads(output)
    except (ValueError, RecursionError):  # JSONDecodeError and UnicodeDecodeError included
        return _unavailable('invalid_json')
    if not isinstance(document, dict):
        return _unavailable('invalid_json')
    days, totals = document.get('days'), document.get('totals')
    if not isinstance(days, list) or not isinstance(totals, dict):
        return _unavailable('invalid_json')
    return {'available': True, 'days': days, 'totals': totals}
