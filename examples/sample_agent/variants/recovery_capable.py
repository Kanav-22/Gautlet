"""Sample agent that performs one bounded recovery attempt."""

from __future__ import annotations

from pydantic import JsonValue

from gauntlet.adapters import JsonObject, ToolCallError, ToolRegistry

from .._shared import lookup, output, result_value, save


def _lookup_attempt(payload: JsonObject, tools: ToolRegistry) -> tuple[bool, JsonValue]:
    try:
        response = lookup(payload, tools)
    except ToolCallError:
        return False, None
    if isinstance(response, dict) and "error" in response:
        return False, None
    return True, response


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Retry one failed or error-shaped lookup, then save a valid value."""

    succeeded, response = _lookup_attempt(payload, tools)
    if not succeeded:
        succeeded, response = _lookup_attempt(payload, tools)
    if not succeeded:
        return output(completed=False, value=None, saved=False)

    value = result_value(response)
    try:
        save(value, tools)
    except ToolCallError:
        return output(completed=False, value=value, saved=False)
    return output(completed=True, value=value, saved=True)
