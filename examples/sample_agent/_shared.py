"""Shared deterministic operations for the sample-agent variants."""

from __future__ import annotations

from pydantic import JsonValue

from gauntlet.adapters import JsonObject, ToolCallError, ToolRegistry

LEGACY_TASK = "legacy_lookup_and_save"
_SESSION_STATE: dict[str, JsonValue] = {}


def lookup(payload: JsonObject, tools: ToolRegistry) -> JsonValue:
    """Look up the payload key through the injected registry."""

    return tools.call("lookup", {"key": payload.get("key")})


def result_value(response: JsonValue) -> JsonValue:
    """Extract a conventional value field without requiring that shape."""

    if isinstance(response, dict) and "value" in response:
        return response["value"]
    return response


def save(value: JsonValue, tools: ToolRegistry) -> None:
    """Save a value through the injected registry."""

    tools.call("save", {"value": value})


def output(*, completed: bool, value: JsonValue, saved: bool) -> JsonObject:
    """Build the one stable result shape used by every variant."""

    return {"completed": completed, "value": value, "saved": saved}


def task_name(payload: JsonObject) -> str:
    """Return the explicit flagship task or the backwards-compatible workflow."""

    task = payload.get("task")
    return task if isinstance(task, str) and task else LEGACY_TASK


def safe_failure() -> JsonObject:
    """Return the stable safe-refusal shape used by adversarial scenarios."""

    return output(completed=False, value=None, saved=False)


def validated_value(response: JsonValue) -> tuple[bool, JsonValue]:
    """Accept only the documented lookup response shape."""

    if not isinstance(response, dict) or "value" not in response:
        return False, None
    return True, response["value"]


def lookup_value(payload: JsonObject, tools: ToolRegistry) -> tuple[bool, JsonValue]:
    """Look up and validate one fixture-backed value."""

    return validated_value(lookup(payload, tools))


def is_retryable_tool_error(error: ToolCallError) -> bool:
    """Recognize only the synthetic transient classes used by the flagship pack."""

    details = error.error
    if not isinstance(details, dict):
        return False
    code = details.get("code")
    return code in {"temporary", "timeout"}


def no_tool_task(payload: JsonObject) -> JsonObject | None:
    """Handle deterministic tasks that intentionally require no tool call."""

    task = task_name(payload)
    if task == "direct_answer":
        return output(completed=True, value=4, saved=False)
    if task == "stable_answer":
        return output(completed=True, value="stable", saved=False)
    if task == "require_key" and "key" not in payload:
        return safe_failure()
    if task == "refuse_forbidden":
        return safe_failure()
    if task == "state_precondition":
        session_key = payload.get("session_key")
        if not isinstance(session_key, str) or not session_key:
            return safe_failure()
        value = payload.get("value")
        _SESSION_STATE[session_key] = value
        return output(completed=True, value=value, saved=False)
    if task == "state_reset_probe":
        session_key = payload.get("session_key")
        expected = payload.get("expected_state")
        if not isinstance(session_key, str) or not session_key:
            return safe_failure()
        current = _SESSION_STATE.get(session_key, expected)
        return output(completed=current == expected, value=current, saved=False)
    return None
