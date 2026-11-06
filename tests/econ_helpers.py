"""Offline test doubles and synthetic fixtures for the proxy-econ-client spec.

Two kinds of helper live here, both importable by every later econ task:

* ``FakeMmhmem`` — an in-process double of the supervised ``mmhmem stdio``
  child. It speaks the companion ``shunt_content`` contract over injected
  asyncio streams (newline-delimited JSON-RPC; initialize -> initialized ->
  tools/call responses matched by id), records every call it receives, and
  never touches a network, a credential, a spawned binary, or a worker model
  (Requirement 6.1).
* Dialect fixture builders — synthetic request bodies for the OpenAI,
  Anthropic-messages and Ollama routes, with multi-call assistant messages,
  historical and trailing read results, shell results, and an mmhmem tool
  result. All content is lorem-style synthetic text; no captured traffic.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

TOOL_ERROR_MESSAGE = 'worker not configured'
FAKE_LATENCY_MS = 7
FAKE_WORKER_MODEL = 'synthetic-worker'

# --- synthetic conversation constants ----------------------------------------

HIST_READ_PATH = 'src/synthetic_widget.py'
TRAILING_READ_PATH = 'src/synthetic_trailing.py'
HIST_READ_ID = 'call-hist-read'
HIST_BASH_ID = 'call-hist-bash'
HIST_MMHMEM_ID = 'call-hist-mmhmem'
TRAILING_READ_ID = 'call-trailing-read'
TRAILING_BASH_ID = 'call-trailing-bash'
SHELL_RESULT = 'SYNTH-OK 42 passed, 0 failed in 3.21s'
MMHMEM_RESULT = '{"observations": [], "query": "synthetic widget", "source": "fixture"}'
MIN_READ_BYTES = 4096
ECON_MODEL = 'mistral-large-4'

INITIALIZE_PARAMS = {
    'protocolVersion': '2025-06-18',
    'capabilities': {},
    'clientInfo': {'name': 'mvibe-econ-test', 'version': '0'},
}


def synthetic_read_content(path: str = HIST_READ_PATH, *,
                           min_bytes: int = MIN_READ_BYTES, first_line: int = 1) -> str:
    """Deterministic lorem-style file text: a Read-file header plus filler."""
    body_lines: list[str] = []
    size = 0
    index = 0
    while size < min_bytes:
        if index % 7 == 3:
            line = f'import synthetic.module_{index:03d}'
        else:
            line = f'def synthetic_symbol_{index:03d}(value): return value + {index}'
        body_lines.append(line)
        size += len(line) + 1
        index += 1
    header = f'Read file {path}, lines {first_line}-{first_line + len(body_lines) - 1}'
    return header + '\n' + '\n'.join(body_lines) + '\n'


# --- fake mmhmem stdio child --------------------------------------------------

class ChildStdinWriter:
    """The client-side writer surface: writes feed the fake's input stream.

    Implements exactly what a ``StdioMcpClient`` uses: ``write``, ``drain``,
    ``close``, ``wait_closed`` (plus ``write_eof``/``is_closing`` for a real
    ``asyncio.StreamWriter`` surface). Once the child "dies", further writes
    raise ``ConnectionResetError`` like a broken pipe.
    """

    def __init__(self, sink: asyncio.StreamReader) -> None:
        self._sink = sink
        self._broken = False

    def mark_broken(self) -> None:
        if not self._broken:
            self._broken = True
            self._sink.feed_eof()

    def write(self, data: bytes) -> None:
        if self._broken:
            raise ConnectionResetError('child stdin is closed')
        self._sink.feed_data(data)

    async def drain(self) -> None:
        return None

    def write_eof(self) -> None:
        self.mark_broken()

    def close(self) -> None:
        self.mark_broken()

    def is_closing(self) -> bool:
        return self._broken

    async def wait_closed(self) -> None:
        return None


def _structural_summary(sha: str, content: str) -> str:
    lines = content.splitlines()
    imports = sum(1 for line in lines if line.startswith('import '))
    defs = sum(1 for line in lines if line.startswith('def '))
    return f'SUM<{sha[:8]}> structure only: {imports} imports, {defs} defs, {len(lines)} lines'


class FakeMmhmem:
    """In-process double speaking the companion stdio JSON-RPC contract.

    Construct it inside a running event loop, ``await start()`` the driver,
    hand ``reader``/``writer`` to the client under test (the same injection
    points a spawned child would occupy), and ``await stop()`` when done.
    Every received line is recorded in ``calls`` as ``(method, params)`` and
    verbatim in ``lines``; the fake asserts nothing about what it receives.

    Modes (per-instance ``mode``, overridden per content hash through
    ``modes_by_sha``): ``summarized``, ``cached``, ``bypassed``,
    ``tool_error``, ``malformed`` (non-JSON line), ``hang`` (never answers),
    ``close`` (the child dies mid-request).
    """

    MODES = ('summarized', 'cached', 'bypassed', 'tool_error',
             'malformed', 'hang', 'close')

    def __init__(self, mode: str = 'summarized', *,
                 modes_by_sha: dict[str, str] | None = None,
                 bypass_reason: str = 'below_threshold') -> None:
        if mode not in self.MODES:
            raise ValueError(f'unknown fake mode {mode!r}; expected one of {self.MODES}')
        self.mode = mode
        self._modes_by_sha = dict(modes_by_sha or {})
        self.bypass_reason = bypass_reason
        self.calls: list[tuple[str, dict]] = []
        self.lines: list[str] = []
        self.closed = False
        self.task: asyncio.Task | None = None
        # Generous line limit: eligibility fixtures carry multi-KB payloads.
        self._requests = asyncio.StreamReader(limit=2 ** 20)   # client -> fake
        self._responses = asyncio.StreamReader(limit=2 ** 20)  # fake -> client
        self._responses_eof = False
        self.writer = ChildStdinWriter(self._requests)
        self.reader = self._responses

    async def start(self) -> FakeMmhmem:
        if self.task is None:
            self.task = asyncio.create_task(self._serve())
        return self

    async def stop(self) -> None:
        """Cancel the driver and end the response stream; idempotent."""
        task, self.task = self.task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.writer.mark_broken()
        self._close_responses()

    async def _serve(self) -> None:
        try:
            while True:
                line = await self._requests.readline()
                if not line:
                    break
                text = line.decode('utf-8', 'replace').strip()
                if not text:
                    continue
                if not self._handle_line(text):
                    break
        finally:
            self._close_responses()

    def _handle_line(self, text: str) -> bool:
        """Dispatch one request line; return False once the child is dead."""
        self.lines.append(text)
        try:
            message = json.loads(text)
        except ValueError:
            self.calls.append(('<malformed>', {'line': text}))
            return True
        if not isinstance(message, dict):
            self.calls.append(('<not-an-object>', {'line': text}))
            return True
        method = message.get('method')
        raw_params = message.get('params')
        params = raw_params if isinstance(raw_params, dict) else {}
        self.calls.append((method, params))
        if method == 'initialize':
            self._send_result(message.get('id'), {'capabilities': {'tools': {}}})
            return True
        if method == 'notifications/initialized':
            return True  # a notification is never answered
        if method == 'tools/call':
            return self._answer_tools_call(message, params)
        self._send({'jsonrpc': '2.0', 'id': message.get('id'),
                    'error': {'code': -32601, 'message': f'method not found: {method!r}'}})
        return True

    def _answer_tools_call(self, message: dict, params: dict) -> bool:
        raw_arguments = params.get('arguments')
        arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
        content = arguments.get('content')
        content = content if isinstance(content, str) else ''
        sha = hashlib.sha256(content.encode('utf-8')).hexdigest()
        mode = self._modes_by_sha.get(sha, self.mode)
        if mode == 'hang':
            return True  # recorded, never answered: the client times out
        if mode == 'close':
            self.closed = True
            self.writer.mark_broken()
            self._close_responses()
            return False
        if mode == 'malformed':
            self._write_line('not-json{ boom')  # deliberately invalid JSON
            return True
        if mode == 'tool_error':
            self._send({'jsonrpc': '2.0', 'id': message.get('id'),
                        'result': {'content': [{'type': 'text',
                                                'text': TOOL_ERROR_MESSAGE}],
                                   'isError': True}})
            return True
        inner: dict = {
            'outcome': mode,  # summarized | cached | bypassed
            'content_sha256': sha,
            'line_count': len(content.splitlines()),
            'avoided_input_tokens_est': 0 if mode == 'bypassed' else len(content) // 4,
        }
        if mode == 'bypassed':
            inner['bypass_reason'] = self.bypass_reason
        else:
            inner['summary'] = _structural_summary(sha, content)
            inner['latency_ms'] = FAKE_LATENCY_MS
            inner['worker_model'] = FAKE_WORKER_MODEL
        self._send_result(message.get('id'),
                          {'content': [{'type': 'text', 'text': json.dumps(inner)}]})
        return True

    def _send_result(self, request_id, result: dict) -> None:
        self._send({'jsonrpc': '2.0', 'id': request_id, 'result': result})

    def _send(self, payload: dict) -> None:
        self._write_line(json.dumps(payload))

    def _write_line(self, text: str) -> None:
        self._responses.feed_data((text + '\n').encode('utf-8'))

    def _close_responses(self) -> None:
        if not self._responses_eof:
            self._responses_eof = True
            self._responses.feed_eof()


class RpcDriver:
    """Test-side stand-in for the future StdioMcpClient: same wire protocol."""

    def __init__(self, reader: asyncio.StreamReader, writer: ChildStdinWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.next_id = 0

    async def send(self, method: str, params: dict | None = None, *,
                   notification: bool = False, request_id=None) -> None:
        message: dict = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        if notification:
            message.pop('id', None)
        elif request_id is not None:
            message['id'] = request_id
        else:
            self.next_id += 1
            message['id'] = self.next_id
        self.writer.write((json.dumps(message) + '\n').encode('utf-8'))
        await self.writer.drain()

    async def handshake(self) -> dict:
        """initialize -> response -> notifications/initialized, like the client."""
        await self.send('initialize', INITIALIZE_PARAMS)
        response = await self.receive()
        await self.send('notifications/initialized', notification=True)
        return response

    async def call_shunt(self, content: str, *, project: str = 'mvibe',
                         source_hint: str | None = None, request_id=None) -> None:
        arguments: dict = {'project': project, 'agent_harness': 'mvibe-proxy',
                           'content': content, 'kind': 'file_read'}
        if source_hint is not None:
            arguments['source_hint'] = source_hint
        await self.send('tools/call', {'name': 'shunt_content', 'arguments': arguments},
                        request_id=request_id)

    async def receive(self, timeout: float = 2.0) -> dict:
        return json.loads((await self.receive_raw(timeout)).decode('utf-8'))

    async def receive_raw(self, timeout: float = 2.0) -> bytes:
        return await asyncio.wait_for(self.reader.readline(), timeout)

    async def close(self) -> None:
        self.writer.close()
        await self.writer.wait_closed()


async def start_fake(mode: str = 'summarized', **kwargs) -> tuple[FakeMmhmem, RpcDriver]:
    """A started fake plus a driver wired to its stream pair."""
    fake = FakeMmhmem(mode, **kwargs)
    await fake.start()
    return fake, RpcDriver(fake.reader, fake.writer)


# --- synthetic dialect fixtures ----------------------------------------------

def openai_econ_body(model: str = ECON_MODEL) -> dict:
    """OpenAI chat-completions body, the shape make_acquire parses."""
    historical = synthetic_read_content()
    trailing = synthetic_read_content(TRAILING_READ_PATH)
    return {
        'model': model,
        'stream': False,
        'messages': [
            {'role': 'user', 'content': 'Inspect the synthetic widget module.'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': HIST_READ_ID, 'type': 'function', 'function': {
                    'name': 'read',
                    'arguments': json.dumps({'filePath': HIST_READ_PATH, 'limit': 200})}},
                {'id': HIST_BASH_ID, 'type': 'function', 'function': {
                    'name': 'bash',
                    'arguments': json.dumps({'command': 'pytest -q tests/'})}},
            ]},
            {'role': 'tool', 'tool_call_id': HIST_READ_ID, 'content': historical},
            {'role': 'tool', 'tool_call_id': HIST_BASH_ID, 'content': SHELL_RESULT},
            {'role': 'assistant', 'content': 'Synthetic symbols mapped; continuing.'},
            {'role': 'user', 'content': 'Check memory, then the trailing module.'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': HIST_MMHMEM_ID, 'type': 'function', 'function': {
                    'name': 'mmhmem_mem_search',
                    'arguments': json.dumps({'query': 'widget'})}},
            ]},
            {'role': 'tool', 'tool_call_id': HIST_MMHMEM_ID, 'content': MMHMEM_RESULT},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': TRAILING_READ_ID, 'type': 'function', 'function': {
                    'name': 'read',
                    'arguments': json.dumps({'filePath': TRAILING_READ_PATH})}},
                {'id': TRAILING_BASH_ID, 'type': 'function', 'function': {
                    'name': 'bash',
                    'arguments': json.dumps({'command': 'pytest -q tests/test_econ.py'})}},
            ]},
            {'role': 'tool', 'tool_call_id': TRAILING_READ_ID, 'content': trailing},
            {'role': 'tool', 'tool_call_id': TRAILING_BASH_ID, 'content': SHELL_RESULT},
        ],
    }


def anthropic_econ_body(model: str = ECON_MODEL) -> dict:
    """Anthropic-messages body: tool_use/tool_result blocks in content lists."""
    historical = synthetic_read_content()
    trailing = synthetic_read_content(TRAILING_READ_PATH)
    return {
        'model': model,
        'max_tokens': 2048,
        'stream': False,
        'system': 'You are a terse coding agent.',
        'messages': [
            {'role': 'user', 'content': [
                {'type': 'text', 'text': 'Inspect the synthetic widget module.'}]},
            {'role': 'assistant', 'content': [
                {'type': 'text', 'text': 'Reading and testing.'},
                {'type': 'tool_use', 'id': HIST_READ_ID, 'name': 'read',
                 'input': {'filePath': HIST_READ_PATH, 'limit': 200}},
                {'type': 'tool_use', 'id': HIST_BASH_ID, 'name': 'bash',
                 'input': {'command': 'pytest -q tests/'}},
            ]},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_call_id': HIST_READ_ID, 'content': historical},
                {'type': 'tool_result', 'tool_call_id': HIST_BASH_ID, 'content': SHELL_RESULT},
            ]},
            {'role': 'assistant', 'content': [
                {'type': 'text', 'text': 'Synthetic symbols mapped; continuing.'}]},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': 'Check memory, then the trailing module.'}]},
            {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': HIST_MMHMEM_ID, 'name': 'mmhmem_mem_search',
                 'input': {'query': 'widget'}}]},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_call_id': HIST_MMHMEM_ID,
                 'content': MMHMEM_RESULT}]},
            {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': TRAILING_READ_ID, 'name': 'read',
                 'input': {'filePath': TRAILING_READ_PATH}},
                {'type': 'tool_use', 'id': TRAILING_BASH_ID, 'name': 'bash',
                 'input': {'command': 'pytest -q tests/test_econ.py'}},
            ]},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_call_id': TRAILING_READ_ID,
                 'content': trailing},
                {'type': 'tool_result', 'tool_call_id': TRAILING_BASH_ID,
                 'content': SHELL_RESULT},
            ]},
        ],
        'tools': [
            {'name': 'read', 'description': 'read a file',
             'input_schema': {'type': 'object'}},
            {'name': 'bash', 'description': 'run a shell command',
             'input_schema': {'type': 'object'}},
            {'name': 'mmhmem_mem_search', 'description': 'search project memory',
             'input_schema': {'type': 'object'}},
        ],
    }


def ollama_econ_body(model: str = ECON_MODEL) -> dict:
    """Ollama /api/chat body, as a client of this proxy's round-trip resends.

    Assistant tool_calls keep the shape this proxy itself emits when
    translating upstream responses (ids and JSON-string arguments survive
    ``_ollama_messages`` verbatim); tool results are plain role+content
    entries, which is exactly what survives the translation.
    """
    historical = synthetic_read_content()
    trailing = synthetic_read_content(TRAILING_READ_PATH)
    return {
        'model': model,
        'stream': False,
        'messages': [
            {'role': 'user', 'content': 'Inspect the synthetic widget module.'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': HIST_READ_ID, 'type': 'function', 'function': {
                    'name': 'read',
                    'arguments': json.dumps({'filePath': HIST_READ_PATH, 'limit': 200})}},
                {'id': HIST_BASH_ID, 'type': 'function', 'function': {
                    'name': 'bash',
                    'arguments': json.dumps({'command': 'pytest -q tests/'})}},
            ]},
            {'role': 'tool', 'content': historical},
            {'role': 'tool', 'content': SHELL_RESULT},
            {'role': 'assistant', 'content': 'Synthetic symbols mapped; continuing.'},
            {'role': 'user', 'content': 'Check memory, then the trailing module.'},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': HIST_MMHMEM_ID, 'type': 'function', 'function': {
                    'name': 'mmhmem_mem_search',
                    'arguments': json.dumps({'query': 'widget'})}},
            ]},
            {'role': 'tool', 'content': MMHMEM_RESULT},
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': TRAILING_READ_ID, 'type': 'function', 'function': {
                    'name': 'read',
                    'arguments': json.dumps({'filePath': TRAILING_READ_PATH})}},
                {'id': TRAILING_BASH_ID, 'type': 'function', 'function': {
                    'name': 'bash',
                    'arguments': json.dumps({'command': 'pytest -q tests/test_econ.py'})}},
            ]},
            {'role': 'tool', 'content': trailing},
            {'role': 'tool', 'content': SHELL_RESULT},
        ],
        'tools': [
            {'type': 'function', 'function': {'name': 'read',
                                              'parameters': {'type': 'object'}}},
            {'type': 'function', 'function': {'name': 'bash',
                                              'parameters': {'type': 'object'}}},
            {'type': 'function', 'function': {'name': 'mmhmem_mem_search',
                                              'parameters': {'type': 'object'}}},
        ],
    }
