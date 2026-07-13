"""Versioned JSON Lines protocol for subprocess system adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

from pydantic import JsonValue

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024

JsonObject: TypeAlias = dict[str, JsonValue]
MessageType: TypeAlias = Literal["request", "response", "error"]


class ProtocolMessageError(ValueError):
    """Raised when a wire message violates the adapter protocol."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RequestMessage:
    """A validated request sent from a parent adapter to its child."""

    operation: str
    correlation_id: str
    payload: JsonObject


@dataclass(frozen=True)
class ErrorInfo:
    """A structured error returned across the process boundary."""

    code: str
    message: str
    details: JsonObject
    retryable: bool


@dataclass(frozen=True)
class ReplyMessage:
    """A validated response or error returned by an adapter child."""

    type: Literal["response", "error"]
    operation: str
    correlation_id: str
    payload: JsonObject | None = None
    error: ErrorInfo | None = None


class SystemAdapter(Protocol):
    """Framework-neutral lifecycle exposed by a system-under-test adapter."""

    def reset(self) -> None: ...

    def invoke(self, payload: JsonObject) -> JsonObject: ...

    def trace(self) -> list[JsonObject]: ...

    def usage(self) -> JsonObject: ...

    def close(self) -> None: ...


def request_message(operation: str, correlation_id: str, payload: JsonObject) -> JsonObject:
    """Build a request envelope."""

    _validate_text("operation", operation)
    _validate_text("correlation_id", correlation_id)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": "request",
        "operation": operation,
        "correlation_id": correlation_id,
        "payload": payload,
    }


def response_message(operation: str, correlation_id: str, payload: JsonObject) -> JsonObject:
    """Build a successful response envelope."""

    _validate_text("operation", operation)
    _validate_text("correlation_id", correlation_id)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": "response",
        "operation": operation,
        "correlation_id": correlation_id,
        "payload": payload,
    }


def error_message(
    operation: str,
    correlation_id: str,
    code: str,
    message: str,
    *,
    details: JsonObject | None = None,
    retryable: bool = False,
) -> JsonObject:
    """Build a structured error envelope."""

    _validate_text("operation", operation)
    _validate_text("correlation_id", correlation_id)
    _validate_text("error.code", code)
    _validate_text("error.message", message)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "type": "error",
        "operation": operation,
        "correlation_id": correlation_id,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "retryable": retryable,
        },
    }


def encode_message(message: JsonObject, *, max_bytes: int = MAX_MESSAGE_BYTES) -> bytes:
    """Serialize one bounded UTF-8 JSONL message."""

    try:
        encoded = json.dumps(
            message,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolMessageError("non_json_message", f"Message is not valid JSON: {exc}") from exc
    if len(encoded) > max_bytes:
        raise ProtocolMessageError(
            "message_too_large", f"Message is {len(encoded)} bytes; limit is {max_bytes} bytes"
        )
    return encoded + b"\n"


def decode_request(raw_line: bytes, *, max_bytes: int = MAX_MESSAGE_BYTES) -> RequestMessage:
    """Parse and strictly validate a request line."""

    message = _decode_object(raw_line, max_bytes=max_bytes)
    _require_exact_keys(
        message,
        {"protocol_version", "type", "operation", "correlation_id", "payload"},
    )
    _validate_version(message.get("protocol_version"))
    if message.get("type") != "request":
        raise ProtocolMessageError("invalid_message_type", "Expected message type 'request'")
    operation = _required_text(message, "operation")
    correlation_id = _required_text(message, "correlation_id")
    payload = _required_object(message, "payload")
    return RequestMessage(operation=operation, correlation_id=correlation_id, payload=payload)


def decode_reply(raw_line: bytes, *, max_bytes: int = MAX_MESSAGE_BYTES) -> ReplyMessage:
    """Parse and strictly validate a response or error line."""

    message = _decode_object(raw_line, max_bytes=max_bytes)
    _validate_version(message.get("protocol_version"))
    message_type = message.get("type")
    if message_type == "response":
        _require_exact_keys(
            message,
            {"protocol_version", "type", "operation", "correlation_id", "payload"},
        )
        return ReplyMessage(
            type="response",
            operation=_required_text(message, "operation"),
            correlation_id=_required_text(message, "correlation_id"),
            payload=_required_object(message, "payload"),
        )
    if message_type == "error":
        _require_exact_keys(
            message,
            {"protocol_version", "type", "operation", "correlation_id", "error"},
        )
        raw_error = _required_object(message, "error")
        _require_exact_keys(raw_error, {"code", "message", "details", "retryable"})
        retryable = raw_error.get("retryable")
        if not isinstance(retryable, bool):
            raise ProtocolMessageError("invalid_envelope", "error.retryable must be a boolean")
        return ReplyMessage(
            type="error",
            operation=_required_text(message, "operation"),
            correlation_id=_required_text(message, "correlation_id"),
            error=ErrorInfo(
                code=_required_text(raw_error, "code"),
                message=_required_text(raw_error, "message"),
                details=_required_object(raw_error, "details"),
                retryable=retryable,
            ),
        )
    raise ProtocolMessageError(
        "invalid_message_type", "Expected message type 'response' or 'error'"
    )


def _decode_object(raw_line: bytes, *, max_bytes: int) -> JsonObject:
    if len(raw_line) > max_bytes + 1:
        raise ProtocolMessageError(
            "message_too_large", f"Message exceeds the {max_bytes}-byte limit"
        )
    if not raw_line.endswith(b"\n"):
        raise ProtocolMessageError("incomplete_message", "Protocol message is missing a newline")
    encoded = raw_line[:-1]
    if len(encoded) > max_bytes:
        raise ProtocolMessageError(
            "message_too_large", f"Message exceeds the {max_bytes}-byte limit"
        )
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolMessageError("invalid_utf8", "Protocol message is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except ProtocolMessageError:
        raise
    except json.JSONDecodeError as exc:
        raise ProtocolMessageError("malformed_json", f"Malformed JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ProtocolMessageError("invalid_envelope", "Protocol message must be a JSON object")
    return value


def _object_without_duplicates(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolMessageError("duplicate_key", f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> JsonValue:
    raise ProtocolMessageError("invalid_json_number", f"Invalid JSON number: {value}")


def _validate_version(value: JsonValue | None) -> None:
    if isinstance(value, bool) or value != PROTOCOL_VERSION:
        raise ProtocolMessageError(
            "unsupported_protocol_version",
            f"Expected protocol version {PROTOCOL_VERSION}, received {value!r}",
        )


def _require_exact_keys(value: JsonObject, expected: set[str]) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        parts: list[str] = []
        if missing:
            parts.append(f"missing {missing}")
        if extra:
            parts.append(f"unexpected {extra}")
        raise ProtocolMessageError("invalid_envelope", "; ".join(parts))


def _required_text(value: JsonObject, field: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str):
        raise ProtocolMessageError("invalid_envelope", f"{field} must be a string")
    _validate_text(field, raw)
    return raw


def _validate_text(field: str, value: str) -> None:
    if not value.strip():
        raise ProtocolMessageError("invalid_envelope", f"{field} must not be blank")


def _required_object(value: JsonObject, field: str) -> JsonObject:
    raw = value.get(field)
    if not isinstance(raw, dict):
        raise ProtocolMessageError("invalid_envelope", f"{field} must be a JSON object")
    return raw
