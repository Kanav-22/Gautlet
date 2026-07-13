"""Shared deterministic operations for the sample-agent variants."""

from __future__ import annotations

from pydantic import JsonValue

from gauntlet.adapters import JsonObject, ToolRegistry


def lookup(payload: JsonObject, tools: ToolRegistry) -> JsonValue:
    """Look up the payload key through the injected registry."""

    return tools.call("lookup", {"key": payload.get("key")})


def result_value(response: JsonValue) -> JsonValue:
    """Extract a conventional value field without requiring that shape."""

    if isinstance(response, dict) and "value" in response:
        return response["value"]
    return response


def save(value: JsonValue, tools: ToolRegistry) -> None:
    """Save a value through the injected registry."""

    tools.call("save", {"value": value})


def output(*, completed: bool, value: JsonValue, saved: bool) -> JsonObject:
    """Build the one stable result shape used by every variant."""

    return {"completed": completed, "value": value, "saved": saved}
