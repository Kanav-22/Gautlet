"""Integration tests for the persistent Python callable subprocess adapter."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from gauntlet.adapters import (
    MAX_MESSAGE_BYTES,
    AdapterChildError,
    AdapterClosedError,
    AdapterProcessError,
    AdapterProtocolError,
    AdapterTimeoutError,
    JsonObject,
    PythonCallableAdapter,
    ToolCallError,
    ToolFixtureError,
    ToolRegistry,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def adapter_for(
    target: str,
    *,
    timeout_seconds: float = 10.0,
    tool_sequence: Sequence[JsonObject] | None = None,
    seed: int | None = None,
    max_message_bytes: int = MAX_MESSAGE_BYTES,
) -> PythonCallableAdapter:
    return PythonCallableAdapter(
        f"tests.adapter_target:{target}",
        project_root=PROJECT_ROOT,
        timeout_seconds=timeout_seconds,
        tool_sequence=tool_sequence,
        seed=seed,
        max_message_bytes=max_message_bytes,
    )


def test_real_subprocess_captures_tool_trace_and_usage() -> None:
    fixtures: list[JsonObject] = [
        {
            "tool": "lookup",
            "arguments": {"key": "answer"},
            "response": {"value": 42},
            "delay_ms": 0,
        }
    ]
    with adapter_for("tool_agent", tool_sequence=fixtures, seed=7) as adapter:
        adapter.reset()
        assert adapter.invoke({"key": "answer"}) == {"result": {"value": 42}}
        assert adapter.is_running
        assert adapter.trace() == [
            {
                "sequence": 1,
                "type": "tool_call",
                "tool": "lookup",
                "arguments": {"key": "answer"},
                "fixture_index": 0,
                "policy_result": "allowed",
                "outcome": "returned",
                "response": {"value": 42},
                "error": None,
                "fixture_delay_ms": 0,
            }
        ]
        assert adapter.usage() == {
            "invocations": 1,
            "invoke_errors": 0,
            "tool_calls": 1,
            "tool_calls_allowed": 1,
            "tool_calls_denied": 0,
            "tool_errors": 0,
        }


def test_fixture_error_recovery_is_visible_in_trace() -> None:
    fixtures: list[JsonObject] = [
        {"tool": "lookup", "error": {"code": "temporary"}},
        {"tool": "lookup", "response": {"value": 42}},
    ]
    with adapter_for("recovery_agent", tool_sequence=fixtures) as adapter:
        assert adapter.invoke({"key": "answer"}) == {
            "result": {"value": 42},
            "recovered": True,
        }
        assert [event["outcome"] for event in adapter.trace()] == ["error", "returned"]
        assert adapter.usage()["tool_errors"] == 1


def test_environment_is_constructed_and_stdout_is_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GAUNTLET_TEST_SECRET", "must-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "https://must-not-leak.invalid")
    monkeypatch.setenv("PYTHONPATH", "must-not-leak")
    adapter = adapter_for("echo_environment", seed=23)
    with adapter:
        output = adapter.invoke({"hello": "world"})
        assert output["payload"] == {"hello": "world"}
        assert output["secret"] is None
        assert output["api_key"] is None
        assert output["https_proxy"] is None
        assert output["pythonpath"] is None
        assert output["path"] is None
        assert output["hash_seed"] == "23"
        assert isinstance(output["home"], str)
    assert "target diagnostic" in adapter.stderr


def test_reset_replaces_child_interpreter_state() -> None:
    with adapter_for("stateful") as adapter:
        assert adapter.invoke({}) == {"calls": 1}
        assert adapter.invoke({}) == {"calls": 2}
        adapter.reset()
        assert adapter.invoke({}) == {"calls": 1}
        assert adapter.usage()["invocations"] == 1


def test_reset_repeats_hash_and_random_state_for_the_same_seed() -> None:
    with adapter_for("determinism_probe", seed=23) as adapter:
        first = adapter.invoke({})
        adapter.reset()
        assert adapter.invoke({}) == first


def test_raw_stdout_protocol_corruption_terminates_child() -> None:
    with adapter_for("corrupt_protocol_stdout") as adapter:
        with pytest.raises(AdapterProtocolError, match="Malformed JSON"):
            adapter.invoke({})
        assert not adapter.is_running
        with pytest.raises(AdapterProcessError, match="must be reset"):
            adapter.trace()


def test_timeout_terminates_child_and_requires_explicit_reset() -> None:
    with adapter_for("conditional_hang", timeout_seconds=1.0) as adapter:
        with pytest.raises(AdapterTimeoutError, match="deadline"):
            adapter.invoke({"hang": True})
        assert not adapter.is_running
        with pytest.raises(AdapterProcessError, match="must be reset"):
            adapter.invoke({"hang": False})
        adapter.reset()
        assert adapter.invoke({"hang": False}) == {"completed": True}


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ("raises", "target_error"),
        ("non_object", "invalid_target_output"),
        ("wrong_signature", "invalid_callable_signature"),
        ("missing", "target_load_error"),
    ],
)
def test_child_failures_are_structured(target: str, code: str) -> None:
    with adapter_for(target) as adapter:
        with pytest.raises(AdapterChildError) as caught:
            adapter.invoke({})
        assert caught.value.code == code
        assert caught.value.retryable is False
        if code == "invalid_callable_signature":
            assert "thin shim" in str(caught.value)


def test_close_is_idempotent_and_prevents_reuse() -> None:
    adapter = adapter_for("stateful")
    adapter.close()
    adapter.close()
    with pytest.raises(AdapterClosedError):
        adapter.invoke({})


def test_registry_denial_does_not_consume_fixture() -> None:
    registry = ToolRegistry([{"tool": "lookup", "response": {"value": 42}}])
    with pytest.raises(ToolCallError) as caught:
        registry.call("other")
    assert caught.value.code == "unexpected_tool"
    assert registry.call("lookup") == {"value": 42}
    trace = registry.trace()
    assert [event["fixture_index"] for event in trace] == [0, 0]
    assert registry.usage() == {
        "tool_calls": 2,
        "tool_calls_allowed": 1,
        "tool_calls_denied": 1,
        "tool_errors": 0,
    }


def test_response_containing_error_is_data_but_top_level_error_raises() -> None:
    data_registry = ToolRegistry([{"tool": "lookup", "response": {"error": "ordinary data"}}])
    assert data_registry.call("lookup") == {"error": "ordinary data"}

    error_registry = ToolRegistry([{"tool": "lookup", "error": {"code": "failed"}}])
    with pytest.raises(ToolCallError) as caught:
        error_registry.call("lookup")
    assert caught.value.error == {"code": "failed"}


def test_invalid_tool_fixture_is_rejected_before_child_start() -> None:
    with pytest.raises(ToolFixtureError, match="exactly one"):
        adapter_for(
            "tool_agent",
            tool_sequence=[{"tool": "lookup", "response": {}, "error": "bad"}],
        )
