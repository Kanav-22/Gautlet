"""Inefficient but functionally correct sample-agent behavior."""

from __future__ import annotations

from gauntlet.adapters import JsonObject, ToolRegistry

from .._shared import lookup, output, result_value, save


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Repeat the same lookup before saving the second result."""

    lookup(payload, tools)
    value = result_value(lookup(payload, tools))
    save(value, tools)
    return output(completed=True, value=value, saved=True)
