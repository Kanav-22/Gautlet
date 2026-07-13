"""Sample agent that falsely claims a save occurred."""

from __future__ import annotations

from gauntlet.adapters import JsonObject, ToolRegistry

from .._shared import lookup, output, result_value


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Look up a value, skip the save call, and claim success anyway."""

    value = result_value(lookup(payload, tools))
    return output(completed=True, value=value, saved=True)
