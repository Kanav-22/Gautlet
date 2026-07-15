"""Sample agent that performs one bounded recovery attempt."""

from __future__ import annotations

from pydantic import JsonValue

from gauntlet.adapters import JsonObject, ToolCallError, ToolRegistry

from .._shared import LEGACY_TASK, lookup, output, result_value, safe_failure, save, task_name
from .correct import run as run_correct


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

    task = task_name(payload)
    if task != LEGACY_TASK:
        if task == "bounded_failure":
            for _attempt in range(2):
                try:
                    lookup(payload, tools)
                except ToolCallError:
                    continue
                break
            return safe_failure()
        return run_correct(payload, tools=tools)

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
