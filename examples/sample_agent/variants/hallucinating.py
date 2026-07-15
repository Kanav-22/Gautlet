"""Sample agent that falsely claims a save occurred."""

from __future__ import annotations

from gauntlet.adapters import JsonObject, ToolRegistry

from .._shared import LEGACY_TASK, lookup, no_tool_task, output, result_value, task_name


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Skip required persistence and claim success on tool-backed tasks."""

    task = task_name(payload)
    if task != LEGACY_TASK and task in {
        "direct_answer",
        "state_precondition",
        "state_reset_probe",
        "stable_answer",
        "refuse_forbidden",
    }:
        without_tools = no_tool_task(payload)
        assert without_tools is not None
        return without_tools

    value = result_value(lookup(payload, tools))
    return output(completed=True, value=value, saved=True)
