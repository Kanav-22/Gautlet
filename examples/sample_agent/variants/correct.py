"""Correct sample-agent behavior."""

from __future__ import annotations

from gauntlet.adapters import JsonObject, ToolRegistry

from .._shared import lookup, output, result_value, save


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Look up one value and save it exactly once."""

    value = result_value(lookup(payload, tools))
    save(value, tools)
    return output(completed=True, value=value, saved=True)
