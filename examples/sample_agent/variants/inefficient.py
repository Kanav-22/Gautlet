"""Inefficient but functionally correct sample-agent behavior."""

from __future__ import annotations

from gauntlet.adapters import JsonObject, ToolRegistry

from .._shared import LEGACY_TASK, lookup, no_tool_task, output, result_value, task_name
from .correct import run as run_correct


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Perform one unnecessary lookup on applicable flagship tool paths."""

    task = task_name(payload)
    if task == LEGACY_TASK:
        lookup(payload, tools)
        value = result_value(lookup(payload, tools))
        tools.call("save", {"value": value})
        return output(completed=True, value=value, saved=True)

    if task in {"state_precondition", "state_reset_probe", "stable_answer", "refuse_forbidden"}:
        without_tools = no_tool_task(payload)
        assert without_tools is not None
        return without_tools

    # The no-fixture direct/missing-information cases expose unnecessary tool use
    # as a denied trace event. Tool-backed cases consume or mismatch the next
    # fixture before the correct workflow begins.
    lookup(payload, tools)
    return run_correct(payload, tools=tools)
