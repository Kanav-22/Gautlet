"""Deterministic injected tool registry backed by scenario fixtures."""

from __future__ import annotations

import copy
import time
from collections.abc import Mapping, Sequence
from typing import TypeAlias

from pydantic import JsonValue

JsonObject: TypeAlias = dict[str, JsonValue]


class ToolFixtureError(ValueError):
    """Raised when a tool-sequence fixture is invalid."""


class ToolCallError(RuntimeError):
    """Raised when a stub tool returns an error or a call is denied."""

    def __init__(self, code: str, message: str, *, error: JsonValue = None) -> None:
        super().__init__(message)
        self.code = code
        self.error = copy.deepcopy(error)


class ToolRegistry:
    """FIFO registry that exposes only harness-controlled stub tools."""

    def __init__(self, tool_sequence: Sequence[JsonObject] | None = None) -> None:
        self._fixtures: list[JsonObject] = []
        self._next_fixture = 0
        self._trace: list[JsonObject] = []
        self.reset(tool_sequence or [])

    def reset(self, tool_sequence: Sequence[JsonObject]) -> None:
        """Validate, copy, and rewind the ordered fixture sequence."""

        fixtures = [
            _validate_fixture(index, fixture) for index, fixture in enumerate(tool_sequence)
        ]
        self._fixtures = fixtures
        self._next_fixture = 0
        self._trace = []

    def call(self, name: str, arguments: Mapping[str, JsonValue] | None = None) -> JsonValue:
        """Call the next allowed stub tool and record a deterministic trace event."""

        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name must be a non-blank string")
        if arguments is not None and not isinstance(arguments, Mapping):
            raise ValueError("Tool arguments must be a JSON object")
        actual_arguments = copy.deepcopy(dict(arguments or {}))

        sequence = len(self._trace) + 1
        if self._next_fixture >= len(self._fixtures):
            message = f"No fixture remains for tool call {name!r}"
            self._trace.append(
                _trace_event(
                    sequence=sequence,
                    tool=name,
                    arguments=actual_arguments,
                    fixture_index=None,
                    policy_result="denied",
                    outcome="denied",
                    error={"code": "fixture_exhausted", "message": message},
                )
            )
            raise ToolCallError("fixture_exhausted", message)

        fixture = self._fixtures[self._next_fixture]
        expected_tool = fixture["tool"]
        if name != expected_tool:
            message = f"Expected tool {expected_tool!r}, received {name!r}"
            self._trace.append(
                _trace_event(
                    sequence=sequence,
                    tool=name,
                    arguments=actual_arguments,
                    fixture_index=self._next_fixture,
                    policy_result="denied",
                    outcome="denied",
                    error={
                        "code": "unexpected_tool",
                        "message": message,
                        "expected_tool": expected_tool,
                    },
                )
            )
            raise ToolCallError("unexpected_tool", message)

        expected_arguments = fixture.get("arguments")
        if expected_arguments is not None and actual_arguments != expected_arguments:
            message = f"Arguments for tool {name!r} do not match the fixture"
            self._trace.append(
                _trace_event(
                    sequence=sequence,
                    tool=name,
                    arguments=actual_arguments,
                    fixture_index=self._next_fixture,
                    policy_result="denied",
                    outcome="denied",
                    error={
                        "code": "unexpected_arguments",
                        "message": message,
                        "expected_arguments": copy.deepcopy(expected_arguments),
                    },
                )
            )
            raise ToolCallError("unexpected_arguments", message)

        fixture_index = self._next_fixture
        self._next_fixture += 1
        delay_ms = fixture.get("delay_ms", 0)
        assert isinstance(delay_ms, int)
        if delay_ms:
            time.sleep(delay_ms / 1000)

        if "error" in fixture:
            error = copy.deepcopy(fixture["error"])
            self._trace.append(
                _trace_event(
                    sequence=sequence,
                    tool=name,
                    arguments=actual_arguments,
                    fixture_index=fixture_index,
                    policy_result="allowed",
                    outcome="error",
                    error=error,
                    fixture_delay_ms=delay_ms,
                )
            )
            raise ToolCallError("tool_error", f"Stub tool {name!r} returned an error", error=error)

        response = copy.deepcopy(fixture["response"])
        self._trace.append(
            _trace_event(
                sequence=sequence,
                tool=name,
                arguments=actual_arguments,
                fixture_index=fixture_index,
                policy_result="allowed",
                outcome="returned",
                response=response,
                fixture_delay_ms=delay_ms,
            )
        )
        return response

    def trace(self) -> list[JsonObject]:
        """Return a copy of all attempted calls in request order."""

        return copy.deepcopy(self._trace)

    def usage(self) -> JsonObject:
        """Return only counters directly observed by this registry."""

        allowed = sum(event.get("policy_result") == "allowed" for event in self._trace)
        errors = sum(event.get("outcome") == "error" for event in self._trace)
        return {
            "tool_calls": len(self._trace),
            "tool_calls_allowed": allowed,
            "tool_calls_denied": len(self._trace) - allowed,
            "tool_errors": errors,
        }


def _validate_fixture(index: int, fixture: JsonObject) -> JsonObject:
    if not isinstance(fixture, dict):
        raise ToolFixtureError(f"tool_sequence[{index}] must be a JSON object")
    allowed_keys = {"tool", "arguments", "response", "error", "delay_ms"}
    extra = sorted(set(fixture) - allowed_keys)
    if extra:
        raise ToolFixtureError(f"tool_sequence[{index}] has unexpected fields: {extra}")
    tool = fixture.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise ToolFixtureError(f"tool_sequence[{index}].tool must be a non-blank string")
    arguments = fixture.get("arguments")
    if arguments is not None and not isinstance(arguments, dict):
        raise ToolFixtureError(f"tool_sequence[{index}].arguments must be a JSON object")
    outcomes = int("response" in fixture) + int("error" in fixture)
    if outcomes != 1:
        raise ToolFixtureError(
            f"tool_sequence[{index}] must contain exactly one of response or error"
        )
    delay_ms = fixture.get("delay_ms", 0)
    if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or delay_ms < 0:
        raise ToolFixtureError(f"tool_sequence[{index}].delay_ms must be a non-negative integer")
    return copy.deepcopy(fixture)


def _trace_event(
    *,
    sequence: int,
    tool: str,
    arguments: JsonObject,
    fixture_index: int | None,
    policy_result: str,
    outcome: str,
    response: JsonValue = None,
    error: JsonValue = None,
    fixture_delay_ms: int = 0,
) -> JsonObject:
    """Build the one stable event shape used by every tool-call outcome."""

    return {
        "sequence": sequence,
        "type": "tool_call",
        "tool": tool,
        "arguments": arguments,
        "fixture_index": fixture_index,
        "policy_result": policy_result,
        "outcome": outcome,
        "response": response,
        "error": error,
        "fixture_delay_ms": fixture_delay_ms,
    }
