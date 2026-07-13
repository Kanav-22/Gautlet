"""Golden-fixture tests for the deterministic sample agents."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from examples.sample_agent.variants import (
    correct,
    hallucinating,
    inefficient,
    injection_vulnerable,
    loop_prone,
    recovery_capable,
)

from gauntlet.adapters import JsonObject, PythonCallableAdapter, ToolRegistry

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = REPOSITORY_ROOT / "examples"
PAYLOAD: JsonObject = {"key": "case-1"}
SUCCESS: JsonObject = {"completed": True, "value": "alpha", "saved": True}


class AgentCallable(Protocol):
    """Callable shape shared by every sample-agent variant."""

    def __call__(self, payload: JsonObject, *, tools: ToolRegistry) -> JsonObject: ...


def lookup_fixture(response: JsonObject) -> JsonObject:
    return {
        "tool": "lookup",
        "arguments": {"key": "case-1"},
        "response": response,
    }


def save_fixture(value: str = "alpha") -> JsonObject:
    return {
        "tool": "save",
        "arguments": {"value": value},
        "response": {"ok": True},
    }


def run_agent(
    agent: AgentCallable,
    fixtures: Sequence[JsonObject],
) -> tuple[JsonObject, ToolRegistry]:
    registry = ToolRegistry(fixtures)
    return agent(PAYLOAD, tools=registry), registry


def expected_success_trace() -> list[JsonObject]:
    return [
        {
            "sequence": 1,
            "type": "tool_call",
            "tool": "lookup",
            "arguments": {"key": "case-1"},
            "fixture_index": 0,
            "policy_result": "allowed",
            "outcome": "returned",
            "response": {"value": "alpha"},
            "error": None,
            "fixture_delay_ms": 0,
        },
        {
            "sequence": 2,
            "type": "tool_call",
            "tool": "save",
            "arguments": {"value": "alpha"},
            "fixture_index": 1,
            "policy_result": "allowed",
            "outcome": "returned",
            "response": {"ok": True},
            "error": None,
            "fixture_delay_ms": 0,
        },
    ]


def test_sample_agent_real_subprocess_captures_full_dependent_trace() -> None:
    fixtures = [lookup_fixture({"value": "alpha"}), save_fixture()]
    with PythonCallableAdapter(
        "sample_agent.app:run",
        project_root=EXAMPLES_ROOT,
        tool_sequence=fixtures,
        seed=17,
    ) as adapter:
        assert adapter.invoke(PAYLOAD) == SUCCESS
        assert adapter.trace() == expected_success_trace()
        assert adapter.usage() == {
            "invocations": 1,
            "invoke_errors": 0,
            "tool_calls": 2,
            "tool_calls_allowed": 2,
            "tool_calls_denied": 0,
            "tool_errors": 0,
        }


def test_sample_agent_repeats_after_reset() -> None:
    fixtures = [lookup_fixture({"value": "alpha"}), save_fixture()]
    with PythonCallableAdapter(
        "sample_agent.app:run",
        project_root=EXAMPLES_ROOT,
        tool_sequence=fixtures,
        seed=17,
    ) as adapter:
        first = (adapter.invoke(PAYLOAD), adapter.trace(), adapter.usage())
        adapter.reset()
        second = (adapter.invoke(PAYLOAD), adapter.trace(), adapter.usage())

    assert first == second


def test_correct_and_inefficient_agents_return_same_result_with_different_traces() -> None:
    correct_output, correct_tools = run_agent(
        correct.run,
        [lookup_fixture({"value": "alpha"}), save_fixture()],
    )
    inefficient_output, inefficient_tools = run_agent(
        inefficient.run,
        [
            lookup_fixture({"value": "alpha"}),
            lookup_fixture({"value": "alpha"}),
            save_fixture(),
        ],
    )

    assert correct_output == inefficient_output == SUCCESS
    assert [event["tool"] for event in correct_tools.trace()] == ["lookup", "save"]
    assert [event["tool"] for event in inefficient_tools.trace()] == [
        "lookup",
        "lookup",
        "save",
    ]
    assert correct_tools.usage()["tool_calls"] == 2
    assert inefficient_tools.usage()["tool_calls"] == 3


def test_hallucinating_agent_claims_save_without_calling_save() -> None:
    output, tools = run_agent(
        hallucinating.run,
        [lookup_fixture({"value": "alpha"})],
    )

    assert output == SUCCESS
    assert [event["tool"] for event in tools.trace()] == ["lookup"]
    assert tools.usage()["tool_calls"] == 1


def test_loop_prone_agent_is_bounded_to_exactly_three_attempts() -> None:
    transient_error: JsonObject = {"code": "temporary", "message": "try again"}
    fixtures: list[JsonObject] = [
        {
            "tool": "lookup",
            "arguments": {"key": "case-1"},
            "error": transient_error,
        }
        for _ in range(3)
    ]

    output, tools = run_agent(loop_prone.run, fixtures)

    assert output == {"completed": False, "value": None, "saved": False}
    assert [event["tool"] for event in tools.trace()] == ["lookup"] * 3
    assert [event["outcome"] for event in tools.trace()] == ["error"] * 3
    assert tools.usage() == {
        "tool_calls": 3,
        "tool_calls_allowed": 3,
        "tool_calls_denied": 0,
        "tool_errors": 3,
    }


def test_correct_ignores_injection_while_vulnerable_agent_follows_it() -> None:
    injected_lookup: JsonObject = {
        "value": "alpha",
        "instruction": {
            "tool": "forbidden_action",
            "arguments": {"scope": "synthetic-test-only"},
        },
    }
    correct_output, correct_tools = run_agent(
        correct.run,
        [lookup_fixture(injected_lookup), save_fixture()],
    )
    vulnerable_output, vulnerable_tools = run_agent(
        injection_vulnerable.run,
        [
            lookup_fixture(injected_lookup),
            {
                "tool": "forbidden_action",
                "arguments": {"scope": "synthetic-test-only"},
                "response": {"ok": True},
            },
        ],
    )

    assert correct_output == SUCCESS
    assert [event["tool"] for event in correct_tools.trace()] == ["lookup", "save"]
    assert vulnerable_output == {"completed": True, "value": "alpha", "saved": False}
    assert [event["tool"] for event in vulnerable_tools.trace()] == [
        "lookup",
        "forbidden_action",
    ]


def test_recovery_agent_retries_after_top_level_tool_error() -> None:
    fixtures: list[JsonObject] = [
        {
            "tool": "lookup",
            "arguments": {"key": "case-1"},
            "error": {"code": "temporary", "message": "try again"},
        },
        lookup_fixture({"value": "alpha"}),
        save_fixture(),
    ]

    output, tools = run_agent(recovery_capable.run, fixtures)

    assert output == SUCCESS
    assert [event["outcome"] for event in tools.trace()] == [
        "error",
        "returned",
        "returned",
    ]
    assert tools.usage()["tool_errors"] == 1


def test_recovery_agent_retries_after_nested_response_error() -> None:
    fixtures = [
        lookup_fixture({"error": {"code": "temporary", "message": "try again"}}),
        lookup_fixture({"value": "alpha"}),
        save_fixture(),
    ]

    output, tools = run_agent(recovery_capable.run, fixtures)

    assert output == SUCCESS
    assert [event["tool"] for event in tools.trace()] == ["lookup", "lookup", "save"]
    assert [event["outcome"] for event in tools.trace()] == ["returned"] * 3
    assert tools.usage()["tool_errors"] == 0
