"""Failure-looping sample agent with a hard safety bound."""

from __future__ import annotations

from gauntlet.adapters import JsonObject, ToolCallError, ToolRegistry

from .._shared import (
    LEGACY_TASK,
    lookup,
    no_tool_task,
    output,
    result_value,
    safe_failure,
    save,
    task_name,
    validated_value,
)

MAX_ATTEMPTS = 3


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Retry a failed workflow three times, then return a bounded failure."""

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

    for _attempt in range(MAX_ATTEMPTS):
        try:
            response = lookup(payload, tools)
            if task == LEGACY_TASK:
                value = result_value(response)
            else:
                valid, value = validated_value(response)
                if not valid:
                    if task == "validate_tool_response":
                        save(response, tools)
                        continue
                    return safe_failure()
            if task == "lookup_only":
                return output(completed=True, value=value, saved=False)
            save(value, tools)
        except ToolCallError:
            continue
        return output(completed=True, value=value, saved=True)
    return safe_failure()
