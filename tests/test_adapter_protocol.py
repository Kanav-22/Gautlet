"""Unit tests for the subprocess wire protocol."""

import pytest

from gauntlet.adapters.protocol import (
    PROTOCOL_VERSION,
    JsonObject,
    ProtocolMessageError,
    decode_reply,
    decode_request,
    encode_message,
    error_message,
    request_message,
    response_message,
)


def test_request_and_response_round_trip() -> None:
    request = decode_request(encode_message(request_message("invoke", "17", {"value": 42})))
    assert request.operation == "invoke"
    assert request.correlation_id == "17"
    assert request.payload == {"value": 42}

    response = decode_reply(encode_message(response_message("invoke", "17", {"answer": "ok"})))
    assert response.type == "response"
    assert response.payload == {"answer": "ok"}


def test_protocol_round_trips_unicode_without_ascii_escaping() -> None:
    payload: JsonObject = {"message": "namaste \u0928\u092e\u0938\u094d\u0924\u0947 \U0001f44b"}
    encoded = encode_message(request_message("invoke", "unicode", payload))

    assert "\u0928\u092e\u0938\u094d\u0924\u0947".encode() in encoded
    assert decode_request(encoded).payload == payload


def test_structured_error_round_trip_includes_retryability() -> None:
    reply = decode_reply(
        encode_message(
            error_message(
                "invoke",
                "9",
                "temporary",
                "try later",
                details={"attempt": 1},
                retryable=True,
            )
        )
    )
    assert reply.error is not None
    assert reply.error.code == "temporary"
    assert reply.error.details == {"attempt": 1}
    assert reply.error.retryable is True


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (b"not-json\n", "malformed_json"),
        (b'{"protocol_version":1,"protocol_version":1}\n', "duplicate_key"),
        (
            b'{"protocol_version":2,"type":"request","operation":"invoke",'
            b'"correlation_id":"1","payload":{}}\n',
            "unsupported_protocol_version",
        ),
        (
            b'{"protocol_version":1,"type":"event","operation":"invoke",'
            b'"correlation_id":"1","payload":{}}\n',
            "invalid_message_type",
        ),
        (b'{"value":NaN}\n', "invalid_json_number"),
    ],
)
def test_invalid_request_fails_predictably(line: bytes, code: str) -> None:
    with pytest.raises(ProtocolMessageError) as caught:
        decode_request(line)
    assert caught.value.code == code


def test_message_size_is_parent_enforced() -> None:
    with pytest.raises(ProtocolMessageError, match="limit") as caught:
        encode_message(request_message("invoke", "1", {"value": "x" * 200}), max_bytes=100)
    assert caught.value.code == "message_too_large"


def test_protocol_version_is_stable() -> None:
    assert PROTOCOL_VERSION == 1
