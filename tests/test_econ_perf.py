"""Request-path performance measurements for the econ rewrite (proxy-econ-client 4.3).

The design's performance budget is < 5 ms added request-path latency for a
250 KB body carrying 40 eligible read results (design.md, Performance &
Scalability). These are measurement tests, not correctness gates: they are
skipped unless ``MVIBE_ECON_PERF`` is set, so the offline gate stays fast and
deterministic. One always-run shape test anchors the benchmark's synthetic
body (it asserts eligibility arithmetic, never timing).

Run explicitly from the repo root:

    MVIBE_ECON_PERF=1 uv run --locked --offline -- pytest -q tests/test_econ_perf.py -s

(PowerShell: ``$env:MVIBE_ECON_PERF = '1'; uv run --locked --offline -- pytest -q tests/test_econ_perf.py -s``.)

Three measurements, all on the same synthetic body: 40 distinct historical
read results of exactly 6400 bytes each (40 x 6400 = 256000 bytes = 250 KiB
of eligible content) plus an ineligible trailing group.

1. micro — ``EconShunt.apply()`` alone on the attributed payload dict the
   hook receives, memo pre-warmed with all 40 rendered summaries (the
   steady-state replacement path), a fresh ``deepcopy`` per iteration with
   only the ``apply`` call inside the ``perf_counter_ns`` window;
2. full app — POST wall-clock through the real ASGI app (ASGITransport),
   econ enabled+warmed vs disabled on the same body, 50 timed requests each
   after 5 untimed warm-ups, reporting mean/p95 for both and the delta;
3. concurrency — the same full-app setup with 4 concurrent clients x 50
   requests each (``asyncio.gather``), reporting per-request p95 for both
   configurations and the delta; this exposes event-loop blocking of the
   sync parse+walk+rewrite slices.

The enabled full-app path also forwards a much smaller rewritten body
(~10 KB instead of ~250 KB) and pays one extra fail-open snapshot
serialization of the attributed body, so its wall-clock delta against the
disabled run is the *net* request-path impact; the micro figure is the pure
rewrite cost. Both go to the spec's implementation log.
"""
import asyncio
import copy
import json
import math
import os
import platform
import statistics
import time
import types

import httpx
import pytest

from econ_helpers import SHELL_RESULT
from mvibe_proxy import econ
from test_econ import econ_config, econ_harness, eventually, post_chat, wired_app

PERF_RESULTS = 40
PERF_RESULT_BYTES = 6400           # 40 x 6400 = 256000 = 250 KiB of read content
PERF_BODY_BYTES = 250 * 1024
MARKER_PREFIX = '[mvibe econ] Summary of an earlier file read'
BUDGET_MS = 5.0                    # design: < 5 ms added request-path latency
MICRO_ITERATIONS = 100
FULLAPP_WARMUP = 5
FULLAPP_REQUESTS = 50
CONCURRENT_CLIENTS = 4
CONC_REQUESTS_PER_CLIENT = 50

perf_only = pytest.mark.skipif(
    not os.environ.get('MVIBE_ECON_PERF'),
    reason='set MVIBE_ECON_PERF=1 to run the econ request-path benchmarks (task 4.3)')


# --- synthetic 250 KiB benchmark body ---------------------------------------

PERF_DIR = 'src/perf'


def perf_path(index: int) -> str:
    return f'{PERF_DIR}/synthetic_module_{index:02d}.py'


def perf_tool_call_id(index: int) -> str:
    return f'call-perf-read-{index:02d}'


def perf_read_content(index: int, size: int = PERF_RESULT_BYTES) -> str:
    """Deterministic lorem-style read text of exactly `size` ASCII bytes."""
    lines = [f'Read file {perf_path(index)}, lines 1-9000']
    used = len(lines[0])
    symbol = 0
    while used < size - 1:
        room = size - used - 1
        line = f'def perf_symbol_{index:02d}_{symbol:04d}(value): return value + {symbol}'
        lines.append(line[:room])
        used += 1 + len(line[:room])
        symbol += 1
    content = '\n'.join(lines)
    content += ' ' * (size - len(content))
    assert len(content.encode('utf-8')) == size
    return content


def perf_openai_body() -> dict:
    """The benchmark chat-completions body: 40 distinct historical read
    results (the eligible set) plus an ineligible trailing group."""
    read_calls = [
        {'id': perf_tool_call_id(index), 'type': 'function',
         'function': {'name': 'read',
                      'arguments': json.dumps({'filePath': perf_path(index)})}}
        for index in range(PERF_RESULTS)]
    read_results = [
        {'role': 'tool', 'tool_call_id': perf_tool_call_id(index),
         'content': perf_read_content(index)}
        for index in range(PERF_RESULTS)]
    return {
        'model': 'mistral-large-4',
        'stream': False,
        'messages': [
            {'role': 'user', 'content': 'Sweep every synthetic perf module.'},
            {'role': 'assistant', 'content': '', 'tool_calls': read_calls},
            *read_results,
            {'role': 'user', 'content': 'Now the trailing module and the suite.'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'call-perf-trailing-read', 'type': 'function',
                 'function': {'name': 'read',
                              'arguments': json.dumps(
                                  {'filePath': f'{PERF_DIR}/synthetic_trailing.py'})}},
                {'id': 'call-perf-trailing-bash', 'type': 'function',
                 'function': {'name': 'bash',
                              'arguments': json.dumps({'command': 'pytest -q tests/'})}},
            ]},
            {'role': 'tool', 'tool_call_id': 'call-perf-trailing-read',
             'content': 'trailing read result, never eligible'},
            {'role': 'tool', 'tool_call_id': 'call-perf-trailing-bash',
             'content': SHELL_RESULT},
        ],
    }


def perf_raw() -> bytes:
    return json.dumps(perf_openai_body()).encode()


def test_perf_body_shape_40_eligible_results_over_250_kib():
    # The benchmark's precondition, checked deterministically: exactly 40
    # eligible results whose content sums to 250 KiB, in a ~250 KiB body.
    body = perf_openai_body()
    candidates = econ.select(body['messages'], econ_config())
    assert len(candidates) == PERF_RESULTS
    assert [candidate.index for candidate in candidates] == list(range(2, 2 + PERF_RESULTS))
    assert all(candidate.tool == 'read' for candidate in candidates)
    eligible_bytes = sum(len(candidate.content.encode('utf-8'))
                         for candidate in candidates)
    assert eligible_bytes == PERF_RESULTS * PERF_RESULT_BYTES == PERF_BODY_BYTES
    assert PERF_BODY_BYTES <= len(perf_raw()) < PERF_BODY_BYTES + 16 * 1024


# --- measurement plumbing ----------------------------------------------------

@pytest.fixture
def no_spawn(monkeypatch):
    """As test_econ.no_spawn: the injected-stream client must never spawn."""
    calls = []

    def deny(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('StdioMcpClient must never spawn a subprocess in tests')

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', deny)
    yield calls
    assert calls == []


def _print_env(label: str) -> None:
    cpu = (os.environ.get('PROCESSOR_IDENTIFIER')
           or platform.processor() or 'unknown')
    print(f'[{label}] python {platform.python_version()} '
          f'({platform.python_implementation()}) | {platform.platform()} | '
          f'cpu: {cpu} x{os.cpu_count()}')


def _stats_ms(samples_ns: list[int]) -> dict[str, float]:
    ordered = sorted(samples_ns)
    p95 = ordered[min(len(ordered) - 1, math.floor(0.95 * (len(ordered) - 1)))]
    return {'n': float(len(ordered)), 'mean_ms': statistics.fmean(ordered) / 1e6,
            'p95_ms': p95 / 1e6, 'min_ms': ordered[0] / 1e6,
            'max_ms': ordered[-1] / 1e6}


def _report(label: str, samples_ns: list[int]) -> dict[str, float]:
    stats = _stats_ms(samples_ns)
    print(f'[{label}] n={int(stats["n"])} mean={stats["mean_ms"]:.3f} ms '
          f'p95={stats["p95_ms"]:.3f} ms min={stats["min_ms"]:.3f} ms '
          f'max={stats["max_ms"]:.3f} ms')
    return stats


def _verdict(label: str, value_ms: float) -> None:
    verdict = 'PASS' if value_ms < BUDGET_MS else 'EXCEEDS (follow-up required)'
    print(f'[{label}] budget <{BUDGET_MS:g} ms (design): {value_ms:.3f} ms -> {verdict}')


def _memo_summaries(shunt) -> bool:
    return (len(shunt.memo) == PERF_RESULTS
            and all(outcome.kind == 'summary'
                    for outcome in shunt.memo._entries.values()))


async def warm_all_summaries(client: httpx.AsyncClient, shunt, raw: bytes) -> None:
    """One raw-relay POST, then wait until all 40 hashes hold rendered
    summaries — the steady state every timed request then exercises."""
    await post_chat(client, raw)
    assert await eventually(lambda: _memo_summaries(shunt), timeout_s=10.0), (
        f'warm-up incomplete: {len(shunt.memo)}/{PERF_RESULTS} memoized')


def _replaced_hist_contents(upstream_body: dict) -> list[str]:
    return [message['content'] for message in upstream_body['messages']
            if isinstance(message, dict) and message.get('role') == 'tool'
            and isinstance(message.get('tool_call_id'), str)
            and message['tool_call_id'].startswith('call-perf-read-')]


# --- 1. micro: apply() alone on the steady-state replacement path -----------

@perf_only
@pytest.mark.anyio
async def test_perf_micro_apply_steady_state_rewrite(no_spawn):
    _print_env('micro')
    config = econ_config()
    raw = perf_raw()
    async with wired_app(config=config) as (app, requests, _client, _fake):
        async with app.router.lifespan_context(app):
            shunt = app.state.econ
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local') as client:
                await warm_all_summaries(client, shunt, raw)
                # The attributed dict exactly as the hook receives it: the
                # first POST relays raw, so the upstream saw the attributed
                # body before any rewrite.
                attributed = json.loads(requests[0].content)
                assert len(econ.select(attributed['messages'], config)) == PERF_RESULTS
                baseline = copy.deepcopy(attributed)
                samples: list[int] = []
                for _ in range(MICRO_ITERATIONS):
                    payload = copy.deepcopy(baseline)  # outside the timing window
                    diagnostic = types.SimpleNamespace()
                    started = time.perf_counter_ns()
                    counters = shunt.apply(payload, diagnostic)
                    elapsed = time.perf_counter_ns() - started
                    assert counters is not None
                    assert (counters.eligible, counters.replaced,
                            counters.pending) == (PERF_RESULTS, PERF_RESULTS, 0)
                    assert diagnostic.econ is counters
                    samples.append(elapsed)
                rewritten = [message['content'] for message in payload['messages']
                             if isinstance(message, dict)
                             and message.get('tool_call_id') == perf_tool_call_id(0)]
                assert rewritten == [rewritten[0]]
                assert rewritten[0].startswith(MARKER_PREFIX)
    micro = _report('micro apply() enabled+warmed', samples)
    _verdict('micro apply() mean', micro['mean_ms'])
    _verdict('micro apply() p95', micro['p95_ms'])


# --- 2. full app: enabled+warmed vs disabled request wall-clock --------------

@perf_only
@pytest.mark.anyio
async def test_perf_full_app_enabled_vs_disabled_wall_clock(no_spawn):
    _print_env('full-app')
    raw = perf_raw()
    enabled: list[int] = []
    async with wired_app() as (app, requests, _client, _fake):
        async with app.router.lifespan_context(app):
            shunt = app.state.econ
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local') as client:
                await warm_all_summaries(client, shunt, raw)
                for _ in range(FULLAPP_WARMUP):
                    await post_chat(client, raw)
                replaced = _replaced_hist_contents(json.loads(requests[-1].content))
                assert len(replaced) == PERF_RESULTS
                assert all(content.startswith(MARKER_PREFIX) for content in replaced)
                for _ in range(FULLAPP_REQUESTS):
                    started = time.perf_counter_ns()
                    response = await client.post('/v1/chat/completions', content=raw)
                    enabled.append(time.perf_counter_ns() - started)
                    assert response.status_code == 200

    disabled: list[int] = []
    control_app, _session, control_requests = econ_harness()
    async with control_app.router.lifespan_context(control_app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(control_app),
                                     base_url='http://local') as client:
            for _ in range(FULLAPP_WARMUP):
                await post_chat(client, raw)
            relayed = _replaced_hist_contents(json.loads(control_requests[-1].content))
            assert len(relayed) == PERF_RESULTS
            assert relayed[0] == perf_read_content(0)  # raw relay, no rewrite
            for _ in range(FULLAPP_REQUESTS):
                started = time.perf_counter_ns()
                response = await client.post('/v1/chat/completions', content=raw)
                disabled.append(time.perf_counter_ns() - started)
                assert response.status_code == 200

    enabled_stats = _report('full-app enabled+warmed', enabled)
    disabled_stats = _report('full-app disabled', disabled)
    delta_mean = enabled_stats['mean_ms'] - disabled_stats['mean_ms']
    delta_p95 = enabled_stats['p95_ms'] - disabled_stats['p95_ms']
    print(f'[full-app] added latency enabled-vs-disabled: '
          f'mean {delta_mean:+.3f} ms, p95 {delta_p95:+.3f} ms')
    _verdict('full-app mean delta', delta_mean)


# --- 3. concurrency: four concurrent clients through the event loop ----------

async def _timed_stream(client: httpx.AsyncClient, raw: bytes, count: int) -> list[int]:
    samples: list[int] = []
    for _ in range(count):
        started = time.perf_counter_ns()
        response = await client.post('/v1/chat/completions', content=raw)
        elapsed = time.perf_counter_ns() - started
        assert response.status_code == 200
        samples.append(elapsed)
    return samples


@perf_only
@pytest.mark.anyio
async def test_perf_concurrent_four_clients_p95(no_spawn):
    _print_env('concurrency')
    raw = perf_raw()
    enabled: list[int] = []
    async with wired_app() as (app, requests, _client, _fake):
        async with app.router.lifespan_context(app):
            shunt = app.state.econ
            clients = [httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                         base_url='http://local')
                       for _ in range(CONCURRENT_CLIENTS)]
            try:
                await warm_all_summaries(clients[0], shunt, raw)
                for _ in range(FULLAPP_WARMUP):
                    await post_chat(clients[0], raw)
                assert all(content.startswith(MARKER_PREFIX)
                           for content in _replaced_hist_contents(
                               json.loads(requests[-1].content)))
                streams = await asyncio.gather(*(
                    _timed_stream(client, raw, CONC_REQUESTS_PER_CLIENT)
                    for client in clients))
                enabled = [sample for stream in streams for sample in stream]
            finally:
                for client in clients:
                    await client.aclose()

    disabled: list[int] = []
    control_app, _session, _control_requests = econ_harness()
    async with control_app.router.lifespan_context(control_app):
        clients = [httpx.AsyncClient(transport=httpx.ASGITransport(control_app),
                                     base_url='http://local')
                   for _ in range(CONCURRENT_CLIENTS)]
        try:
            for _ in range(FULLAPP_WARMUP):
                await post_chat(clients[0], raw)
            streams = await asyncio.gather(*(
                _timed_stream(client, raw, CONC_REQUESTS_PER_CLIENT)
                for client in clients))
            disabled = [sample for stream in streams for sample in stream]
        finally:
            for client in clients:
                await client.aclose()

    enabled_stats = _report(f'concurrency={CONCURRENT_CLIENTS} enabled+warmed', enabled)
    disabled_stats = _report(f'concurrency={CONCURRENT_CLIENTS} disabled', disabled)
    delta_mean = enabled_stats['mean_ms'] - disabled_stats['mean_ms']
    delta_p95 = enabled_stats['p95_ms'] - disabled_stats['p95_ms']
    print(f'[concurrency={CONCURRENT_CLIENTS}] per-request delta enabled-vs-disabled: '
          f'mean {delta_mean:+.3f} ms, p95 {delta_p95:+.3f} ms')
    _verdict(f'concurrency={CONCURRENT_CLIENTS} mean delta', delta_mean)
