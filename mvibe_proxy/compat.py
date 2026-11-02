"""Pure dialect translators for the Ollama- and Anthropic-compatible adapter routes.

Everything here is side-effect free: request bodies in, upstream OpenAI-shaped
bodies out; upstream responses in, adapter-shaped bodies/frames out. Request
payloads are never logged. Invalid adapter input raises CompatError, which the
routes map to a 400 in the adapter's error shape.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable


class CompatError(ValueError):
    """Invalid adapter request body; routes map this to a 400."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_str(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise CompatError(f'{key} must be a non-empty string')
    return value


_require_str = require_str


def _ollama_options(body: dict) -> dict:
    options = body.get('options')
    out: dict[str, Any] = {}
    if isinstance(options, dict):
        if isinstance(options.get('temperature'), (int, float)):
            out['temperature'] = options['temperature']
        if isinstance(options.get('top_p'), (int, float)):
            out['top_p'] = options['top_p']
        if isinstance(options.get('num_predict'), int):
            out['max_tokens'] = options['num_predict']
    return out


def _ollama_tools(body: dict) -> list | None:
    tools = body.get('tools')
    if tools is None:
        return None
    if not isinstance(tools, list):
        raise CompatError('tools must be an array')
    # Ollama function tools already match the upstream function-tool shape.
    return tools


def _ollama_messages(body: dict) -> list:
    messages = body.get('messages')
    if not isinstance(messages, list):
        raise CompatError('messages must be an array')
    out = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get('role'), str):
            raise CompatError('each message needs a role')
        content = message.get('content', '')
        if isinstance(content, list):
            # Content parts: keep text only; images are out of scope.
            texts = [part.get('text', '') for part in content
                     if isinstance(part, dict) and part.get('type') == 'text']
            if any(isinstance(part, dict) and part.get('type') not in ('text', None) for part in content):
                raise CompatError('only text content parts are supported')
            content = ''.join(texts)
        if not isinstance(content, str):
            raise CompatError('message content must be text')
        entry: dict[str, Any] = {'role': message['role'], 'content': content}
        if isinstance(message.get('tool_calls'), list):
            entry['tool_calls'] = message['tool_calls']
        out.append(entry)
    return out


# ---------- Ollama ----------

def ollama_version() -> dict:
    return {'version': '0.0.0-mvibe'}


def ollama_tags(upstream: dict) -> dict:
    models = []
    for entry in upstream.get('data') or []:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get('id')
        if not isinstance(model_id, str):
            continue
        models.append({'name': model_id, 'model': model_id,
                       'details': {'format': 'mistral', 'family': 'mistral',
                                   'families': ['mistral'], 'parameter_size': '',
                                   'quantization_level': ''}})
    return {'models': models}


def ollama_chat_request(body: dict) -> dict:
    out = {'model': _require_str(body, 'model'),
           'messages': _ollama_messages(body),
           'stream': bool(body.get('stream', False)),
           **_ollama_options(body)}
    tools = _ollama_tools(body)
    if tools is not None:
        out['tools'] = tools
    return out


def ollama_generate_request(body: dict) -> dict:
    messages = []
    system = body.get('system')
    if isinstance(system, str) and system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': _require_str(body, 'prompt')})
    out = {'model': _require_str(body, 'model'), 'messages': messages,
           'stream': bool(body.get('stream', False)), **_ollama_options(body)}
    tools = _ollama_tools(body)
    if tools is not None:
        out['tools'] = tools
    return out


def _upstream_choice(upstream: dict) -> dict:
    choices = upstream.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise CompatError('upstream response has no choices')
    return choices[0]


def _usage_fields(upstream: dict) -> dict:
    usage = upstream.get('usage') or {}
    out = {}
    if isinstance(usage.get('prompt_tokens'), int):
        out['prompt_eval_count'] = usage['prompt_tokens']
    if isinstance(usage.get('completion_tokens'), int):
        out['eval_count'] = usage['completion_tokens']
    return out


def ollama_chat_response(upstream: dict, model: str) -> dict:
    message = _upstream_choice(upstream).get('message') or {}
    out_message: dict[str, Any] = {'role': 'assistant', 'content': message.get('content') or ''}
    if message.get('tool_calls'):
        out_message['tool_calls'] = message['tool_calls']
    return {'model': model, 'created_at': _now(), 'message': out_message,
            'done': True, **_usage_fields(upstream)}


def ollama_generate_response(upstream: dict, model: str) -> dict:
    message = _upstream_choice(upstream).get('message') or {}
    return {'model': model, 'created_at': _now(), 'response': message.get('content') or '',
            'done': True, **_usage_fields(upstream)}


def ollama_show(upstream_models: dict, name: str) -> dict:
    for entry in upstream_models.get('data') or []:
        if isinstance(entry, dict) and entry.get('id') == name:
            return {'modelfile': '', 'parameters': '', 'template': '',
                    'details': {'format': 'mistral', 'family': 'mistral',
                                'families': ['mistral'], 'parameter_size': '',
                                'quantization_level': ''},
                    'model_info': {'general.architecture': 'mistral'},
                    'capabilities': entry.get('capabilities') or []}
    raise CompatError(f'model {name!r} not found')


def ollama_embed_request(body: dict) -> dict:
    model = _require_str(body, 'model')
    value = body.get('input')
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CompatError('input must be a string or array of strings')
    return {'model': model, 'input': value}


def ollama_embed_response(upstream: dict) -> dict:
    data = upstream.get('data') or []
    return {'embeddings': [entry.get('embedding') for entry in data
                           if isinstance(entry, dict)]}


class OllamaStreamTranslator:
    """Upstream SSE -> Ollama NDJSON. feed(bytes)->bytes, finish()->final frame."""

    def __init__(self, model: str, *, kind: str = 'chat') -> None:
        self.model = model
        self.kind = kind
        self._buffer = ''
        self._usage: dict[str, Any] = {}
        self._final_sent = False

    def feed(self, chunk: bytes) -> bytes:
        self._buffer += chunk.decode('utf-8', 'replace')
        out = []
        while '\n\n' in self._buffer:
            frame, self._buffer = self._buffer.split('\n\n', 1)
            out.append(self._translate_frame(frame))
        return ''.join(out).encode()

    def _translate_frame(self, frame: str) -> str:
        for line in frame.splitlines():
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                return self._final()
            try:
                obj = json.loads(payload)
            except ValueError:
                return ''
            delta = ((obj.get('choices') or [{}])[0].get('delta') or {})
            if isinstance(obj.get('usage'), dict):
                self._usage = obj['usage']
            message: dict[str, Any] = {'role': 'assistant', 'content': delta.get('content') or ''}
            if delta.get('tool_calls'):
                message['tool_calls'] = delta['tool_calls']
            if self.kind == 'generate':
                body = {'model': self.model, 'created_at': _now(),
                        'response': delta.get('content') or '', 'done': False}
            else:
                body = {'model': self.model, 'created_at': _now(),
                        'message': message, 'done': False}
            return json.dumps(body) + '\n'
        return ''

    def _final(self) -> str:
        if self._final_sent:
            return ''
        self._final_sent = True
        if self.kind == 'generate':
            body: dict[str, Any] = {'model': self.model, 'created_at': _now(),
                                    'response': '', 'done': True}
        else:
            body = {'model': self.model, 'created_at': _now(),
                    'message': {'role': 'assistant', 'content': ''}, 'done': True}
        if isinstance(self._usage, dict):
            if isinstance(self._usage.get('prompt_tokens'), int):
                body['prompt_eval_count'] = self._usage['prompt_tokens']
            if isinstance(self._usage.get('completion_tokens'), int):
                body['eval_count'] = self._usage['completion_tokens']
        return json.dumps(body) + '\n'

    def finish(self) -> bytes:
        # Flush a trailing frame without a blank-line separator, then the final frame.
        pending = self._buffer
        self._buffer = ''
        out = self._translate_frame(pending) if pending.strip() else ''
        return (out + self._final()).encode()


# ---------- Anthropic ----------

def _anthropic_blocks_to_messages(messages: list) -> list:
    out = []
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in ('user', 'assistant'):
            raise CompatError('messages need role user or assistant')
        content = message.get('content', '')
        if isinstance(content, str):
            out.append({'role': message['role'], 'content': content})
            continue
        if not isinstance(content, list):
            raise CompatError('content must be a string or block array')
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                raise CompatError('content blocks must be objects')
            kind = block.get('type')
            if kind == 'text' and isinstance(block.get('text'), str):
                text_parts.append(block['text'])
            elif kind == 'tool_use':
                tool_calls.append({'id': block.get('id'), 'type': 'function',
                                   'function': {'name': block.get('name'),
                                                'arguments': json.dumps(block.get('input') or {})}})
            elif kind == 'tool_result':
                tool_results.append({'role': 'tool', 'tool_call_id': block.get('tool_call_id'),
                                     'content': block.get('content') if isinstance(block.get('content'), str)
                                     else json.dumps(block.get('content'))})
            else:
                raise CompatError(f'unsupported content block type {kind!r}')
        if text_parts:
            out.append({'role': message['role'], 'content': ''.join(text_parts)})
        if tool_calls:
            out.append({'role': 'assistant', 'content': ''.join(text_parts) or '',
                        'tool_calls': tool_calls})
        out.extend(tool_results)
    return out


def anthropic_messages_request(body: dict) -> dict:
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages:
        raise CompatError('messages must be a non-empty array')
    converted = _anthropic_blocks_to_messages(messages)
    system = body.get('system')
    if isinstance(system, str) and system:
        converted.insert(0, {'role': 'system', 'content': system})
    elif isinstance(system, list):
        text = ''.join(block.get('text', '') for block in system
                       if isinstance(block, dict) and block.get('type') == 'text')
        if text:
            converted.insert(0, {'role': 'system', 'content': text})
    out: dict[str, Any] = {'model': _require_str(body, 'model'), 'messages': converted,
                           'max_tokens': body.get('max_tokens') if isinstance(body.get('max_tokens'), int) else 1024,
                           'stream': bool(body.get('stream', False))}
    tools = body.get('tools')
    if tools is not None:
        if not isinstance(tools, list):
            raise CompatError('tools must be an array')
        converted_tools = []
        for tool in tools:
            if not isinstance(tool, dict) or not isinstance(tool.get('name'), str):
                raise CompatError('each tool needs a name')
            converted_tools.append({'type': 'function', 'function': {
                'name': tool['name'], 'description': tool.get('description') or '',
                'parameters': tool.get('input_schema') or {'type': 'object'}}})
        out['tools'] = converted_tools
    if 'tool_choice' in body:
        out['tool_choice'] = body['tool_choice']
    return out


_STOP_REASONS = {'stop': 'end_turn', 'length': 'max_tokens', 'tool_calls': 'tool_use'}


def anthropic_messages_response(upstream: dict) -> dict:
    choice = _upstream_choice(upstream)
    message = choice.get('message') or {}
    content: list[dict] = []
    text = message.get('content')
    if isinstance(text, str) and text:
        content.append({'type': 'text', 'text': text})
    for call in message.get('tool_calls') or []:
        function = call.get('function') or {}
        try:
            arguments = json.loads(function.get('arguments') or '{}')
        except ValueError:
            arguments = {}
        content.append({'type': 'tool_use', 'id': call.get('id'), 'name': function.get('name'),
                        'input': arguments})
    usage = upstream.get('usage') or {}
    return {'id': upstream.get('id') or 'msg_mvibe', 'type': 'message', 'role': 'assistant',
            'content': content, 'model': upstream.get('model') or '',
            'stop_reason': _STOP_REASONS.get(choice.get('finish_reason'), 'end_turn'),
            'stop_sequence': None,
            'usage': {'input_tokens': usage.get('prompt_tokens', 0),
                      'output_tokens': usage.get('completion_tokens', 0)}}


def anthropic_models_list(upstream: dict) -> dict:
    data = []
    for entry in upstream.get('data') or []:
        if isinstance(entry, dict) and isinstance(entry.get('id'), str):
            data.append({'id': entry['id'], 'type': 'model',
                         'display_name': entry.get('id'), 'created_at': _now()})
    return {'data': data, 'has_more': False,
            'first_id': data[0]['id'] if data else None,
            'last_id': data[-1]['id'] if data else None}


class AnthropicStreamTranslator:
    """Upstream SSE -> Anthropic SSE events. feed(bytes)->bytes, finish()->tail."""

    def __init__(self, model: str) -> None:
        self.model = model
        self._buffer = ''
        self._started = False
        self._block_open = False
        self._block_index = -1
        self._usage: dict[str, Any] = {}
        self._stop_reason = 'end_turn'
        self._done_sent = False

    def _event(self, event: str, data: dict) -> str:
        return f'event: {event}\ndata: {json.dumps(data)}\n\n'

    def feed(self, chunk: bytes) -> bytes:
        self._buffer += chunk.decode('utf-8', 'replace')
        out = []
        while '\n\n' in self._buffer:
            frame, self._buffer = self._buffer.split('\n\n', 1)
            out.append(self._translate_frame(frame))
        return ''.join(out).encode()

    def _translate_frame(self, frame: str) -> str:
        for line in frame.splitlines():
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                if self._done_sent:
                    return ''
                self._done_sent = True
                return self._close_blocks() + self._message_delta() + self._event('message_stop', {'type': 'message_stop'})
            try:
                obj = json.loads(payload)
            except ValueError:
                return ''
            choice = (obj.get('choices') or [{}])[0]
            delta = choice.get('delta') or {}
            if isinstance(obj.get('usage'), dict):
                self._usage = obj['usage']
            out = ''
            if not self._started:
                self._started = True
                out += self._event('message_start', {'type': 'message_start', 'message': {
                    'id': obj.get('id') or 'msg_mvibe', 'type': 'message', 'role': 'assistant',
                    'content': [], 'model': self.model, 'stop_reason': None, 'stop_sequence': None,
                    'usage': {'input_tokens': self._usage.get('prompt_tokens', 0), 'output_tokens': 0}}})
            content = delta.get('content')
            if isinstance(content, str) and content:
                if not self._block_open:
                    self._block_index += 1
                    out += self._event('content_block_start', {'type': 'content_block_start', 'index': self._block_index,
                                                               'content_block': {'type': 'text', 'text': ''}})
                    self._block_open = True
                out += self._event('content_block_delta', {'type': 'content_block_delta', 'index': self._block_index,
                                                           'delta': {'type': 'text_delta', 'text': content}})
            for call in delta.get('tool_calls') or []:
                index = call.get('index', 0)
                function = call.get('function') or {}
                if not self._block_open or index != self._block_index:
                    out += self._close_blocks()
                    self._block_index = index
                    out += self._event('content_block_start', {'type': 'content_block_start', 'index': index,
                                                               'content_block': {'type': 'tool_use', 'id': call.get('id'),
                                                                                 'name': function.get('name') or '', 'input': {}}})
                    self._block_open = True
                if function.get('arguments'):
                    out += self._event('content_block_delta', {'type': 'content_block_delta', 'index': index,
                                                               'delta': {'type': 'input_json_delta', 'partial_json': function['arguments']}})
            if choice.get('finish_reason'):
                self._stop_reason = _STOP_REASONS.get(choice['finish_reason'], 'end_turn')
            return out
        return ''

    def _close_blocks(self) -> str:
        if self._block_open:
            self._block_open = False
            return self._event('content_block_stop', {'type': 'content_block_stop', 'index': self._block_index})
        return ''

    def _message_delta(self) -> str:
        return self._event('message_delta', {'type': 'message_delta',
                                             'delta': {'stop_reason': self._stop_reason, 'stop_sequence': None},
                                             'usage': {'output_tokens': self._usage.get('completion_tokens', 0)}})

    def finish(self) -> bytes:
        pending = self._buffer
        self._buffer = ''
        out = self._translate_frame(pending) if pending.strip() and not self._done_sent else ''
        if self._done_sent:
            return out.encode()
        if not self._started:
            self._started = True
            out += self._event('message_start', {'type': 'message_start', 'message': {
                'id': 'msg_mvibe', 'type': 'message', 'role': 'assistant', 'content': [],
                'model': self.model, 'stop_reason': None, 'stop_sequence': None,
                'usage': {'input_tokens': 0, 'output_tokens': 0}}})
        self._done_sent = True
        out += self._close_blocks() + self._message_delta() + self._event('message_stop', {'type': 'message_stop'})
        return out.encode()


# ---------- shared transforms and errors ----------

class BufferJson:
    """Non-streaming transform: buffer the whole upstream body, translate once."""

    def __init__(self, fn: Callable[[dict], dict], dialect: str = 'ollama', error_status: int = 400) -> None:
        self.fn = fn
        self.dialect = dialect
        self.error_status = error_status
        self._buffer = b''

    def feed(self, chunk: bytes) -> bytes:
        self._buffer += chunk
        return b''

    def finish(self) -> bytes:
        try:
            obj = json.loads(self._buffer or b'{}')
            if isinstance(obj, dict) and 'error' in obj and 'choices' not in obj and 'data' not in obj:
                _, payload = adapter_error(self.dialect, 502, upstream_error_hint(502))
                return json.dumps(payload).encode()
            return json.dumps(self.fn(obj)).encode()
        except CompatError as error:
            _, payload = adapter_error(self.dialect, self.error_status, str(error))
            return json.dumps(payload).encode()
        except Exception:
            _, payload = adapter_error(self.dialect, 502, 'Upstream returned an untranslatable body')
            return json.dumps(payload).encode()


_ERROR_TYPES = {400: 'invalid_request_error', 401: 'authentication_error', 403: 'permission_error',
                404: 'not_found_error', 429: 'rate_limit_error', 500: 'api_error'}


def adapter_error(dialect: str, status: int, message: str) -> tuple[int, dict]:
    """Adapter-shaped error body; curated message only, never upstream payloads."""
    if dialect == 'ollama':
        return status, {'error': message}
    return status, {'type': 'error', 'error': {
        'type': _ERROR_TYPES.get(status, 'api_error'), 'message': message}}


def upstream_error_hint(status: int) -> str:
    return {
        400: 'Check tool schema and request fields against the selected upstream model capabilities.',
        401: 'Run proxy.py --login; verify Vibe authentication.',
        403: 'Check Vibe tenant and model access; the agent owns tool permissions.',
        422: 'Check tool schema and reasoning history serialization against the selected model; update the provider before an explicit retry.',
        429: 'Respect upstream rate_limit guidance before an explicit retry.',
    }.get(status, 'Check upstream availability before an explicit retry; no inference was replayed.')
