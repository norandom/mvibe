"""Test-only stand-in for `mmhmem stats --json` (proxy-stats-propagation task 2.4).

tests/test_stats.py runs it as `<python> -I fake_mmhmem_stats.py stats --json`
in place of the configured mmhmem binary. FAKE_MMHMEM_MODE picks the
behavior; every mode first writes a sentinel line to stderr so the tests can
prove stderr never reaches a result. Stdlib only; it touches no files, no
network and no real mmhmem store. Never imported by production code.
"""
import json
import os
import sys
import time

STDERR_SENTINEL = 'STDERR-SENTINEL fake mmhmem diagnostics'
NOTICE = ('No token-savings statistics recorded yet. Use mmhmem from a harness (stdio '
          'or serve mode), then run `mmhmem stats` again.')

# The shape of mmhmem 0.5.2 `stats --json` (src/cli/stats.rs DayAggregate / Totals).
DAYS = [{
    'day': '2026-11-10',
    'project': 'mvibe',
    'harness': 'mvibe-proxy',
    'avoided_input_tokens_est': 1200,
    'frontier_prompt_tokens_client_reported': 5000,
    'frontier_completion_tokens_client_reported': 700,
    'client_reported_sources': ['mvibe-proxy mistral-large-latest'],
    'worker_prompt_tokens': 0,
    'worker_completion_tokens': 0,
    'reads': 3,
}]
TOTALS = {
    'avoided_input_tokens_est': 1200,
    'frontier_prompt_tokens_client_reported': 5000,
    'frontier_completion_tokens_client_reported': 700,
    'worker_prompt_tokens': 0,
    'worker_completion_tokens': 0,
    'reads': 3,
}


def _emit(data: bytes) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main(argv: list[str]) -> int:
    sys.stderr.write(STDERR_SENTINEL + '\n')
    sys.stderr.flush()
    if argv != ['stats', '--json']:
        return 64
    mode = os.environ.get('FAKE_MMHMEM_MODE', 'json')
    if mode == 'json':
        _emit(json.dumps({'days': DAYS, 'totals': TOTALS}).encode() + b'\n')
    elif mode == 'echo_data_dir':
        # Proves the caller's environment reaches the child.
        totals = {'data_dir': os.environ.get('AGENT_MEM_DATA_DIR')}
        _emit(json.dumps({'days': [], 'totals': totals}).encode())
    elif mode == 'notice':
        _emit(NOTICE.encode() + b'\n')
    elif mode == 'exit3':
        _emit(json.dumps({'days': DAYS, 'totals': TOTALS}).encode())
        return 3
    elif mode == 'sleep':
        _emit(b'{"days": [')
        time.sleep(60)
    elif mode == 'raw':
        # FAKE_MMHMEM_OUTPUT verbatim (garbage, wrong shapes).
        _emit(os.environ.get('FAKE_MMHMEM_OUTPUT', '').encode())
    elif mode == 'sized':
        # A valid document padded with trailing spaces to exactly FAKE_MMHMEM_SIZE bytes.
        document = json.dumps({'days': [], 'totals': {'reads': 1}}).encode()
        size = int(os.environ['FAKE_MMHMEM_SIZE'])
        _emit(document + b' ' * (size - len(document)))
    elif mode == 'flood':
        # Far beyond the caller's stdout cap, then a long sleep.
        block = b' ' * (1024 * 1024)
        try:
            _emit(b'{"days": [], "totals": {}}')
            for _ in range(64):
                _emit(block)
        except OSError:
            return 0  # the reader went away
        time.sleep(60)
    else:
        return 65
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
