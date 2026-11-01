"""Pure Vibe 3.0.4 completion attribution, shared by both adapters.

Derived from vibe/core/telemetry/build_metadata.py and
vibe/app_server/_completion_attribution.py (see THIRD_PARTY_NOTICES).
Unknown account/launch information is omitted, never synthesized.
"""
from __future__ import annotations

import json
import platform
import sys
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mvibe_auth import __version__

ContextString = Annotated[str, Field(min_length=1, max_length=256)]


class RequestContext(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, hide_input_in_errors=True)
    session_id: ContextString | None = None
    message_id: ContextString | None = None
    parent_session_id: ContextString | None = None
    client_name: ContextString | None = None
    client_version: ContextString | None = None
    agent_entrypoint: Literal['cli', 'acp', 'desktop', 'programmatic', 'unknown'] | None = None
    agent_version: ContextString | None = None
    purpose: Literal['agent', 'compaction', 'classify', 'title', 'generate'] = 'agent'
    iteration: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator('*', mode='before')
    @classmethod
    def omit_unavailable_fields(cls, value):
        if value is None:
            raise ValueError('Omit unavailable context fields.')
        return value


def get_platform_id() -> str:
    return {'win32': 'windows', 'darwin': 'darwin', 'linux': 'linux'}.get(sys.platform, sys.platform)


def get_platform_version() -> str | None:
    match get_platform_id():
        case 'darwin':
            value = platform.mac_ver()[0] or platform.release()
        case 'windows':
            value = platform.version() or platform.release()
        case 'linux':
            try:
                release = platform.freedesktop_os_release()
            except OSError:
                release = {}
            value = release.get('VERSION_ID') or release.get('VERSION') or platform.release()
        case _:
            value = platform.release() or platform.version()
    return value or None


def build_request_metadata(context: RequestContext | None = None, *, user_plan: str | None = None) -> dict[str, str]:
    context = context or RequestContext()
    call_type = ('smart_approve' if context.purpose == 'classify' else
                 'title_generation' if context.purpose == 'title' else
                 'main_call' if context.purpose == 'agent' and context.iteration == 0 else
                 'secondary_call')
    values = context.model_dump(exclude_none=True, exclude={'purpose', 'iteration'})
    values.update(os=get_platform_id(), arch=platform.machine().lower(),
                  os_version=get_platform_version(), version=__version__,
                  call_type=call_type, call_source='vibe_code', host_kind='local')
    if user_plan is not None:
        values['user_plan'] = user_plan
    return {key: str(value) for key, value in values.items() if value is not None}


def attach_request_metadata(body: bytes, context: RequestContext | None = None, *, user_plan: str | None = None) -> bytes:
    """Merge owned attribution only for JSON objects; leave other bytes intact."""
    try:
        payload = json.loads(body, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return body
    if not isinstance(payload, dict):
        return body
    metadata = payload.get('metadata', {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        return body
    # Account attribution is server-owned, including its unavailable state.
    metadata.pop('user_plan', None)
    if context is None:
        messages = payload.get('messages', [])
        iteration = 0
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    if message.get('role') == 'user':
                        iteration = 0
                    elif message.get('role') in {'assistant', 'tool'}:
                        iteration = 1
        context = RequestContext(iteration=iteration)
    payload['metadata'] = {**metadata, **build_request_metadata(context, user_plan=user_plan)}
    return json.dumps(payload, separators=(',', ':')).encode('utf-8')


def _reject_constant(value: str) -> None:
    raise ValueError('Invalid JSON constant.')
