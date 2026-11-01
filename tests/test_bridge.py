"""Both runtimes consume exactly these synthetic v1 contract vectors."""
import json
from pathlib import Path

import pytest
from mvibe_auth._bridge_protocol import parse_request, parse_response

VECTORS = json.loads((Path(__file__).parent / "fixtures/bridge-v1.json").read_text())

def frame(value):
    return (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")

@pytest.mark.parametrize("vector", VECTORS["valid"], ids=lambda v: v["name"])
def test_valid_wire_vector(vector):
    data = frame(vector["value"])
    parsed = parse_request(data) if vector["kind"] == "request" else parse_response(data, vector["method"])
    assert parsed.model_dump(exclude_none=True) == vector["value"]

@pytest.mark.parametrize("vector", VECTORS["invalid"], ids=lambda v: v["name"])
def test_invalid_wire_vector(vector):
    with pytest.raises(ValueError) as exc:
        if vector["kind"] == "request":
            parse_request(frame(vector["value"]))
        else:
            parse_response(frame(vector["value"]), vector["method"])
    assert "synthetic-secret" not in str(exc.value)

@pytest.mark.parametrize("data", [b"{}", b"{}\n{}\n", b"\xff\n", b"[]\n", b"{\n", b"NaN\n", b" " * 1048576 + b"\n"])
def test_invalid_frames(data):
    with pytest.raises(ValueError):
        parse_request(data)

def test_byte_limit_is_utf8_and_includes_delimiter():
    value = VECTORS["valid"][0]["value"].copy()
    value["id"] = "a"
    empty_size = len(frame(value)) - 1
    value["id"] = "a" * (1048576 - empty_size)
    assert len(frame(value)) == 1048576
    assert parse_request(frame(value)).id == value["id"]
    with pytest.raises(ValueError):
        parse_request(frame(value) + b"\n")
    value["id"] = "é" * 524288
    with pytest.raises(ValueError):
        parse_request(frame(value))

def test_missing_and_unreadable_are_distinct_safe_errors():
    outcomes = [parse_response(frame(v["value"]), "resolve").error for v in VECTORS["valid"] if v["name"] in {"store absent", "store unreadable"}]
    assert [e.code for e in outcomes] == ["missing", "transient"]
    assert [e.retryable for e in outcomes] == [False, True]

def test_success_is_correlated_with_method():
    result = next(v["value"] for v in VECTORS["valid"] if v["kind"] == "response" and v["method"] == "hello")
    with pytest.raises(ValueError):
        parse_response(frame(result), "resolve")

def test_sensitive_result_fields_are_not_in_model_repr():
    for vector in VECTORS["valid"]:
        if vector["kind"] != "response" or not vector["value"].get("ok"):
            continue
        parsed = parse_response(frame(vector["value"]), vector["method"])
        assert "Bearer synthetic-only" not in repr(parsed)
        assert "authorize?state=synthetic" not in repr(parsed)

def test_validation_errors_have_typed_safe_reasons():
    from mvibe_auth._bridge_protocol import ProtocolError
    for value, code in [({"version": 2}, "unsupported_version"), ({"version": 1, "method": "unknown"}, "unknown_method")]:
        with pytest.raises(ProtocolError) as exc:
            parse_request(frame(value))
        assert exc.value.code == code
    with pytest.raises(ProtocolError) as exc:
        parse_request(b" " * 1048577)
    assert exc.value.code == "frame_too_large"

@pytest.mark.parametrize("kind", ["request", "response"])
def test_oversized_integer_is_a_safe_protocol_error(kind):
    from mvibe_auth._bridge_protocol import ProtocolError
    prefix = b'{"version":1,"id":"synthetic-id",'
    body = b'"method":"hello","params":{},' if kind == "request" else b'"ok":false,"error":{"code":"missing","message":"safe","retryable":false},'
    raw = prefix + body + b'"extra":' + b'9' * 5000 + b'}\n'
    with pytest.raises(ProtocolError) as exc:
        if kind == "request":
            parse_request(raw)
        else:
            parse_response(raw, "hello")
    assert exc.value.code == "invalid_frame"
    assert str(exc.value) == "Invalid bridge frame."
