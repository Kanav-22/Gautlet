"""Sample agent that trusts a structured instruction in tool output."""

from __future__ import annotations

from pydantic import JsonValue

from gauntlet.adapters import JsonObject, ToolRegistry

from .._shared import lookup, output, result_value


def _instruction(response: JsonValue) -> tuple[str, JsonObject] | None:
    if not isinstance(response, dict):
        return None
    instruction = response.get("instruction")
    if not isinstance(instruction, dict):
        return None
    tool = instruction.get("tool")
    arguments = instruction.get("arguments")
    if not isinstance(tool, str) or not isinstance(arguments, dict):
        return None
    return tool, arguments


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Blindly follow one structured untrusted instruction via the registry."""

    response = lookup(payload, tools)
    value = result_value(response)
    instruction = _instruction(response)
    if instruction is None:
        return output(completed=False, value=value, saved=False)

    tool, arguments = instruction
    tools.call(tool, arguments)
    return output(completed=True, value=value, saved=tool == "save")
