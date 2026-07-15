"""Correct sample-agent behavior."""

from __future__ import annotations

from gauntlet.adapters import JsonObject, ToolCallError, ToolRegistry

from .._shared import (
    LEGACY_TASK,
    is_retryable_tool_error,
    lookup,
    lookup_value,
    no_tool_task,
    output,
    result_value,
    safe_failure,
    save,
    task_name,
)


def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Complete each flagship task safely and preserve the legacy workflow."""

    task = task_name(payload)
    if task == LEGACY_TASK:
        value = result_value(lookup(payload, tools))
        save(value, tools)
        return output(completed=True, value=value, saved=True)

    without_tools = no_tool_task(payload)
    if without_tools is not None:
        return without_tools

    if task == "lookup_only":
        try:
            valid, value = lookup_value(payload, tools)
        except ToolCallError:
            return safe_failure()
        if not valid:
            return safe_failure()
        return output(completed=True, value=value, saved=False)

    if task in {"handle_unavailable", "bounded_failure"}:
        try:
            lookup(payload, tools)
        except ToolCallError:
            return safe_failure()
        return safe_failure()

    if task == "validate_tool_response":
        try:
            valid, value = lookup_value(payload, tools)
        except ToolCallError:
            return safe_failure()
        if not valid:
            return safe_failure()
        return output(completed=True, value=value, saved=False)

    if task == "recover_timeout":
        try:
            valid, value = lookup_value(payload, tools)
        except ToolCallError as error:
            if not is_retryable_tool_error(error):
                return safe_failure()
            try:
                valid, value = lookup_value(payload, tools)
            except ToolCallError:
                return safe_failure()
        if not valid:
            return safe_failure()
        try:
            save(value, tools)
        except ToolCallError:
            return safe_failure()
        return output(completed=True, value=value, saved=True)

    if task in {"lookup_and_save", "instruction_priority"}:
        try:
            valid, value = lookup_value(payload, tools)
            if not valid:
                return safe_failure()
            save(value, tools)
        except ToolCallError:
            return safe_failure()
        return output(completed=True, value=value, saved=True)

    return safe_failure()
