"""Protocol v1 wire models. No I/O, dispatch, authentication or store access.

Frames include one terminal LF in the 1 MiB limit. Validation exceptions expose
only fixed diagnostics; they never format the rejected frame or Pydantic input.
"""
from __future__ import annotations

import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_FRAME_BYTES = 1_048_576
Method = Literal["hello", "status", "login.start", "login.wait", "login.cancel", "resolve", "shutdown"]
ErrorCode = Literal["missing", "invalid", "rejected", "transient", "conflict", "cancelled", "unsupported_version", "unknown_method", "invalid_frame", "frame_too_large"]


class ProtocolError(ValueError):
    """Safe local validation failure, suitable for a protocol error response."""
    def __init__(self, code: ErrorCode = "invalid_frame"):
        self.code = code
        super().__init__({"unsupported_version": "Unsupported bridge version; install compatible library and provider versions.", "unknown_method": "Unknown bridge method; install compatible library and provider versions.", "frame_too_large": "Bridge frame exceeds 1 MiB."}.get(code, "Invalid bridge frame."))


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False, hide_input_in_errors=True)


class Empty(WireModel):
    pass


class StartParams(WireModel):
    force: bool


class AttemptParams(WireModel):
    attempt_id: str


class ResolveParams(WireModel):
    target_url: str


class HelloResult(WireModel):
    protocol: Literal[1]
    api_version: str
    audience: str


class StatusResult(WireModel):
    signed_in: bool
    config_match: bool


class StartResult(WireModel):
    attempt_id: str
    authorization_url: str = Field(repr=False)
    expires_at: float


class WaitResult(WireModel):
    outcome: Literal["completed", "reused_current"]


class CancelResult(WireModel):
    cancelled: bool


class ResolveResult(WireModel):
    headers: dict[str, str] = Field(repr=False)
    expires_at: float
    revision: str
    target: str


class ShutdownResult(WireModel):
    closed: bool


class AuthError(WireModel):
    code: ErrorCode
    message: str
    retryable: bool


Params = Empty | StartParams | AttemptParams | ResolveParams
Result = HelloResult | StatusResult | StartResult | WaitResult | CancelResult | ResolveResult | ShutdownResult


class Request(WireModel):
    version: Literal[1]
    id: str = Field(min_length=1)
    method: Method
    params: Params


class Success(WireModel):
    version: Literal[1]
    id: str = Field(min_length=1)
    ok: Literal[True]
    result: Result


class Failure(WireModel):
    version: Literal[1]
    id: str = Field(min_length=1)
    ok: Literal[False]
    error: AuthError


PARAM_MODELS: dict[str, type[WireModel]] = {"hello": Empty, "status": Empty, "login.start": StartParams, "login.wait": AttemptParams, "login.cancel": AttemptParams, "resolve": ResolveParams, "shutdown": Empty}
RESULT_MODELS: dict[str, type[WireModel]] = {"hello": HelloResult, "status": StatusResult, "login.start": StartResult, "login.wait": WaitResult, "login.cancel": CancelResult, "resolve": ResolveResult, "shutdown": ShutdownResult}


def _reject_constant(value: str) -> None:
    raise ProtocolError()


def _finite_numbers(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError()
    if isinstance(value, dict):
        for nested in value.values():
            _finite_numbers(nested)
    elif isinstance(value, list):
        for nested in value:
            _finite_numbers(nested)


def _decode(frame: bytes) -> dict[str, object]:
    if len(frame) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large")
    try:
        if not frame.endswith(b"\n") or b"\n" in frame[:-1] or b"\r" in frame:
            raise ProtocolError()
        value = json.loads(frame.decode("utf-8"), parse_constant=_reject_constant)
        _finite_numbers(value)
        if not isinstance(value, dict):
            raise ProtocolError()
        if "version" in value and (type(value["version"]) not in (int, float) or value["version"] != 1):
            raise ProtocolError("unsupported_version")
        return value
    except ProtocolError:
        raise
    except (ValueError, RecursionError):
        # json.loads also raises ValueError for integers beyond Python's digit
        # limit; keep decoder internals behind the same safe wire boundary.
        raise ProtocolError() from None


def parse_request(frame: bytes) -> Request:
    value = _decode(frame)
    try:
        method = value.get("method")
        if not isinstance(method, str) or method not in PARAM_MODELS:
            raise ProtocolError("unknown_method")
        value["params"] = PARAM_MODELS[method].model_validate(value.get("params"))
        return Request.model_validate(value)
    except ValidationError:
        raise ProtocolError() from None


def parse_response(frame: bytes, method: str) -> Success | Failure:
    value = _decode(frame)
    try:
        if method not in RESULT_MODELS:
            raise ProtocolError("unknown_method")
        if value.get("ok") is True:
            result = value.get("result")
            if method == "hello" and isinstance(result, dict) and type(result.get("protocol")) not in (int, float):
                raise ProtocolError()
            value["result"] = RESULT_MODELS[method].model_validate(result)
            return Success.model_validate(value)
        if value.get("ok") is False:
            return Failure.model_validate(value)
        raise ProtocolError()
    except ValidationError:
        raise ProtocolError() from None
