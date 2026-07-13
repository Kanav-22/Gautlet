"""Failure-looping sample agent with a hard safety bound."""

from __future__ import annotations

from gauntlet.adapters import JsonObject, ToolCallError, ToolRegistry

from .._shared import lookup, output, result_value, save

MAX_ATTEMPTS = 3


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Retry a failed workflow three times, then return a bounded failure."""

    for _attempt in range(MAX_ATTEMPTS):
        try:
            value = result_value(lookup(payload, tools))
            save(value, tools)
        except ToolCallError:
            continue
        return output(completed=True, value=value, saved=True)
    return output(completed=False, value=None, saved=False)
