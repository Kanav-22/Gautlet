"""Child-process worker for the Python callable adapter.

Standard output is reserved for protocol messages. Target imports, prints, and
tracebacks are redirected to standard error before application code is loaded.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
import random
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NoReturn, Protocol, cast

from pydantic import JsonValue

from gauntlet.adapters.protocol import (
    MAX_MESSAGE_BYTES,
    JsonObject,
    ProtocolMessageError,
    RequestMessage,
    decode_request,
    encode_message,
    error_message,
    response_message,
)
from gauntlet.adapters.tools import ToolCallError, ToolFixtureError, ToolRegistry


class TargetCallable(Protocol):
    """Required construction boundary for a Python callable system under test."""

    def __call__(self, payload: JsonObject, *, tools: ToolRegistry) -> JsonObject: ...


class ChildOperationError(RuntimeError):
    """An operation error safe to serialize to the parent."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: JsonObject | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.retryable = retryable


@dataclass
class WorkerState:
    """Mutable state owned exclusively by one adapter child."""

    target: TargetCallable | None
    target_load_error: ChildOperationError | None
    tools: ToolRegistry
    invocations: int = 0
    invoke_errors: int = 0

    def handle(self, request: RequestMessage) -> JsonObject:
        if request.operation == "reset":
            return self._reset(request.payload)
        if request.operation == "invoke":
            return self._invoke(request.payload)
        if request.operation == "trace":
            _require_empty_payload(request.operation, request.payload)
            return {"events": cast(JsonValue, self.tools.trace())}
        if request.operation == "usage":
            _require_empty_payload(request.operation, request.payload)
            return {
                "invocations": self.invocations,
                "invoke_errors": self.invoke_errors,
                **self.tools.usage(),
            }
        raise ChildOperationError(
            "unknown_operation", f"Unsupported adapter operation: {request.operation!r}"
        )

    def _reset(self, payload: JsonObject) -> JsonObject:
        extra = sorted(set(payload) - {"seed", "tool_sequence"})
        if extra:
            raise ChildOperationError("invalid_reset_payload", f"Unexpected reset fields: {extra}")
        seed = payload.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ChildOperationError("invalid_reset_payload", "seed must be an integer or null")
        raw_sequence = payload.get("tool_sequence", [])
        if not isinstance(raw_sequence, list) or not all(
            isinstance(item, dict) for item in raw_sequence
        ):
            raise ChildOperationError(
                "invalid_reset_payload", "tool_sequence must be a list of JSON objects"
            )
        try:
            self.tools.reset(cast(list[JsonObject], raw_sequence))
        except ToolFixtureError as exc:
            raise ChildOperationError("invalid_tool_fixture", str(exc)) from exc
        random.seed(0 if seed is None else seed)
        self.invocations = 0
        self.invoke_errors = 0
        return {"reset": True}

    def _invoke(self, payload: JsonObject) -> JsonObject:
        if self.target_load_error is not None:
            self.invoke_errors += 1
            raise self.target_load_error
        assert self.target is not None
        self.invocations += 1
        try:
            output = self.target(payload, tools=self.tools)
        except ToolCallError as exc:
            self.invoke_errors += 1
            raise ChildOperationError(
                "unhandled_tool_error",
                str(exc),
                details={"tool_error_code": exc.code, "error": exc.error},
            ) from exc
        except Exception as exc:
            self.invoke_errors += 1
            traceback.print_exc(file=sys.stderr)
            raise ChildOperationError(
                "target_error",
                f"{type(exc).__name__}: {exc}",
                details={"exception_type": type(exc).__name__},
            ) from exc
        if not isinstance(output, dict):
            self.invoke_errors += 1
            raise ChildOperationError(
                "invalid_target_output", "Python callable must return a JSON object"
            )
        return output


def _require_empty_payload(operation: str, payload: JsonObject) -> None:
    if payload:
        raise ChildOperationError(
            "invalid_operation_payload", f"{operation} does not accept payload fields"
        )


def _load_target(
    target_spec: str, project_root: Path
) -> tuple[TargetCallable | None, ChildOperationError | None]:
    try:
        module_name, separator, attribute_path = target_spec.partition(":")
        if not separator or not module_name.strip() or not attribute_path.strip():
            raise ValueError("Target must use the form 'module:callable'")
        os.chdir(project_root)
        sys.path.insert(0, str(project_root))
        value: object = importlib.import_module(module_name)
        for attribute in attribute_path.split("."):
            value = getattr(value, attribute)
        if not callable(value):
            raise TypeError(f"Target {target_spec!r} is not callable")
        try:
            inspect.signature(value).bind({}, tools=ToolRegistry())
        except (TypeError, ValueError) as exc:
            return None, ChildOperationError(
                "invalid_callable_signature",
                "Python callable must accept run(payload, *, tools); expose a thin shim "
                "that receives the injected ToolRegistry",
                details={"target": target_spec, "signature_error": str(exc)},
            )
        return cast(TargetCallable, value), None
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        return None, ChildOperationError(
            "target_load_error",
            f"Unable to load Python callable {target_spec!r}: {type(exc).__name__}: {exc}",
            details={"exception_type": type(exc).__name__},
        )


def _write_message(protocol_output: BinaryIO, message: JsonObject) -> None:
    protocol_output.write(encode_message(message))
    protocol_output.flush()


def _protocol_failure(
    protocol_output: BinaryIO,
    operation: str,
    correlation_id: str,
    exc: ProtocolMessageError,
) -> None:
    _write_message(
        protocol_output,
        error_message(operation, correlation_id, exc.code, str(exc)),
    )


def _serve(target_spec: str, project_root: Path) -> int:
    protocol_output = cast(BinaryIO, sys.stdout.buffer)
    sys.stdout = sys.stderr
    target, target_error = _load_target(target_spec, project_root)
    state = WorkerState(
        target=target,
        target_load_error=target_error,
        tools=ToolRegistry(),
    )

    while True:
        raw_line = sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 2)
        if not raw_line:
            return 0
        try:
            request = decode_request(raw_line)
        except ProtocolMessageError as exc:
            _protocol_failure(protocol_output, "protocol", "protocol-error", exc)
            if exc.code in {"message_too_large", "incomplete_message"}:
                return 2
            continue

        try:
            payload = state.handle(request)
            message = response_message(request.operation, request.correlation_id, payload)
            _write_message(protocol_output, message)
        except ChildOperationError as exc:
            _write_message(
                protocol_output,
                error_message(
                    request.operation,
                    request.correlation_id,
                    exc.code,
                    str(exc),
                    details=exc.details,
                    retryable=exc.retryable,
                ),
            )
        except ProtocolMessageError as exc:
            _protocol_failure(protocol_output, request.operation, request.correlation_id, exc)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GAUNTLET Python callable adapter worker")
    parser.add_argument("--target", required=True)
    parser.add_argument("--project-root", required=True, type=Path)
    return parser.parse_args()


def main() -> NoReturn:
    """Run the child protocol loop."""

    args = _parse_args()
    raise SystemExit(_serve(args.target, args.project_root.resolve()))


if __name__ == "__main__":
    main()
