"""Tests for the bounded scenario lifecycle executor."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TypeAlias

import pytest

from gauntlet.adapters import (
    AdapterChildError,
    AdapterProtocolError,
    AdapterTimeoutError,
    JsonObject,
)
from gauntlet.config.models import NetworkPolicy
from gauntlet.core.models import Scenario, ScenarioResultStatus
from gauntlet.execution import (
    ExecutionPolicyError,
    PythonCallableAdapterFactory,
    ScenarioAttemptContext,
    ScenarioExecutor,
    ScenarioLifecycleState,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
Outcome: TypeAlias = JsonObject | BaseException


def make_scenario(
    *,
    scenario_id: str = "test.scenario",
    payload: JsonObject | None = None,
    fixtures: JsonObject | None = None,
    policy: JsonObject | None = None,
) -> Scenario:
    return Scenario(
        id=scenario_id,
        title="Scenario",
        description="Executor test scenario",
        category="correctness",
        difficulty=1,
        tags=[],
        required_capabilities=["invoke"],
        input=payload or {},
        fixtures=fixtures or {"tool_sequence": []},
        execution_policy=policy or {},
        assertions=[],
        metrics=[],
    )


class ScriptedAdapter:
    """Small adapter double with explicit lifecycle counters."""

    isolation_level = "test"

    def __init__(
        self,
        outcome: Outcome,
        *,
        trace: Sequence[JsonObject] = (),
        close_error: BaseException | None = None,
    ) -> None:
        self.outcome = outcome
        self.events = [dict(event) for event in trace]
        self.close_error = close_error
        self.reset_calls = 0
        self.close_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1

    def invoke(self, payload: JsonObject) -> JsonObject:
        del payload
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return dict(self.outcome)

    def trace(self) -> list[JsonObject]:
        return [dict(event) for event in self.events]

    def usage(self) -> JsonObject:
        return {"tool_calls": len(self.events)}

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class ScriptedFactory:
    def __init__(self, outcomes: Sequence[Outcome]) -> None:
        self.outcomes = list(outcomes)
        self.contexts: list[ScenarioAttemptContext] = []
        self.adapters: list[ScriptedAdapter] = []

    def __call__(self, context: ScenarioAttemptContext) -> ScriptedAdapter:
        self.contexts.append(context)
        adapter = ScriptedAdapter(self.outcomes[len(self.adapters)])
        self.adapters.append(adapter)
        return adapter


def test_success_follows_full_lifecycle() -> None:
    factory = ScriptedFactory([{"completed": True}])

    execution = ScenarioExecutor(factory, seed=42).execute(make_scenario())

    assert execution.result.status is ScenarioResultStatus.PASSED
    assert execution.result.output == {"completed": True}
    assert execution.result.error is None
    assert execution.lifecycle == (
        ScenarioLifecycleState.LOADED,
        ScenarioLifecycleState.VALIDATED,
        ScenarioLifecycleState.PREPARED,
        ScenarioLifecycleState.RUNNING,
        ScenarioLifecycleState.PASSED,
        ScenarioLifecycleState.FINALIZED,
    )
    assert execution.seed == 42
    assert execution.network_policy is NetworkPolicy.DISABLED
    assert factory.adapters[0].reset_calls == 1
    assert factory.adapters[0].close_calls == 1


def test_retryable_child_error_uses_fresh_adapter_and_rewinds_fixtures() -> None:
    retryable = AdapterChildError(
        "temporary",
        "try again",
        details={"kind": "fixture"},
        retryable=True,
    )
    fixtures: JsonObject = {"tool_sequence": [{"tool": "lookup", "response": {"value": 42}}]}
    factory = ScriptedFactory([retryable, {"completed": True}])

    execution = ScenarioExecutor(factory, seed=7).execute(
        make_scenario(fixtures=fixtures, policy={"max_retries": 1})
    )

    assert execution.result.status is ScenarioResultStatus.PASSED
    assert execution.result.metrics["attempts"] == 2
    assert len(factory.contexts) == 2
    assert factory.contexts[0].seed == factory.contexts[1].seed == 7
    assert factory.contexts[0].tool_sequence == factory.contexts[1].tool_sequence
    assert factory.contexts[0].tool_sequence is not factory.contexts[1].tool_sequence
    assert all(adapter.close_calls == 1 for adapter in factory.adapters)


def test_non_retryable_error_is_contained_without_retry() -> None:
    failure = AdapterChildError(
        "invalid",
        "not retryable",
        details={},
        retryable=False,
    )
    factory = ScriptedFactory([failure])

    execution = ScenarioExecutor(factory).execute(make_scenario(policy={"max_retries": 3}))

    assert execution.result.status is ScenarioResultStatus.ERROR
    assert execution.result.error is not None
    assert execution.result.error["type"] == "adapter_child_error"
    assert len(factory.adapters) == 1
    assert factory.adapters[0].close_calls == 1


@pytest.mark.parametrize(
    "failure",
    [
        AdapterTimeoutError("invoke", 0.1, ""),
        AdapterProtocolError("malicious protocol line"),
    ],
)
def test_timeout_and_protocol_failure_are_not_retried(failure: BaseException) -> None:
    factory = ScriptedFactory([failure])

    execution = ScenarioExecutor(factory).execute(make_scenario(policy={"max_retries": 2}))

    expected = (
        ScenarioResultStatus.TIMED_OUT
        if isinstance(failure, AdapterTimeoutError)
        else ScenarioResultStatus.ERROR
    )
    assert execution.result.status is expected
    assert len(factory.adapters) == 1
    assert factory.adapters[0].close_calls == 1


def test_cleanup_failure_prevents_false_pass() -> None:
    adapter = ScriptedAdapter({"completed": True}, close_error=RuntimeError("cleanup failed"))

    execution = ScenarioExecutor(lambda context: adapter).execute(make_scenario())

    assert execution.result.status is ScenarioResultStatus.ERROR
    assert execution.result.output is None
    assert execution.result.error is not None
    assert execution.result.error["type"] == "cleanup_error"
    assert adapter.close_calls == 1


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ({"timeout_seconds": 0}, "finite positive"),
        ({"timeout_seconds": True}, "finite positive"),
        ({"max_retries": -1}, "non-negative integer"),
        ({"max_retries": True}, "non-negative integer"),
        ({"seed": True}, "integer or null"),
        ({"network": "maybe"}, "enabled or disabled"),
    ],
)
def test_invalid_policy_is_rejected_before_adapter_creation(
    policy: JsonObject,
    message: str,
) -> None:
    factory = ScriptedFactory([])

    with pytest.raises(ExecutionPolicyError, match=message):
        ScenarioExecutor(factory).execute(make_scenario(policy=policy))

    assert factory.contexts == []


def test_real_timeout_is_parent_enforced_and_hanging_child_is_reaped() -> None:
    factory = PythonCallableAdapterFactory(
        "tests.adapter_target:conditional_hang",
        project_root=PROJECT_ROOT,
    )

    before = time.monotonic()
    execution = ScenarioExecutor(factory, timeout_seconds=0.2).execute(
        make_scenario(payload={"hang": True})
    )

    assert execution.result.status is ScenarioResultStatus.TIMED_OUT
    assert execution.result.output is None
    assert time.monotonic() - before < 5
    assert execution.attempts[0].timed_out is True


def test_network_disabled_child_excludes_parent_network_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "https://must-not-leak.invalid")
    monkeypatch.setenv("GAUNTLET_TEST_SECRET", "must-not-leak")
    factory = PythonCallableAdapterFactory(
        "tests.adapter_target:echo_environment",
        project_root=PROJECT_ROOT,
    )

    execution = ScenarioExecutor(factory, network_policy=NetworkPolicy.DISABLED).execute(
        make_scenario(payload={"hello": "world"})
    )

    assert execution.result.status is ScenarioResultStatus.PASSED
    assert execution.result.output is not None
    assert execution.result.output["api_key"] is None
    assert execution.result.output["https_proxy"] is None
    assert execution.result.output["secret"] is None
    assert execution.network_policy is NetworkPolicy.DISABLED


def test_malicious_stdout_cannot_corrupt_result() -> None:
    factory = PythonCallableAdapterFactory(
        "tests.adapter_target:corrupt_protocol_stdout",
        project_root=PROJECT_ROOT,
    )

    execution = ScenarioExecutor(factory).execute(make_scenario())

    assert execution.result.status is ScenarioResultStatus.ERROR
    assert execution.result.output is None
    assert execution.result.error is not None
    assert execution.result.error["type"] == "adapter_error"
    assert execution.result.error["adapter_error_type"] == "AdapterProtocolError"


class ConcurrencyProbe:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


class ConcurrentAdapter(ScriptedAdapter):
    def __init__(self, scenario_id: str, probe: ConcurrencyProbe) -> None:
        super().__init__({"scenario_id": scenario_id})
        self.probe = probe

    def invoke(self, payload: JsonObject) -> JsonObject:
        del payload
        self.probe.enter()
        try:
            time.sleep(0.05)
            return dict(self.outcome)  # type: ignore[arg-type]
        finally:
            self.probe.leave()


def test_bounded_concurrency_preserves_input_order() -> None:
    probe = ConcurrencyProbe()

    def factory(context: ScenarioAttemptContext) -> ConcurrentAdapter:
        return ConcurrentAdapter(context.scenario.id, probe)

    scenarios = [make_scenario(scenario_id=f"scenario-{index}") for index in range(6)]

    executions = ScenarioExecutor(factory, max_concurrency=2).execute_many(scenarios)

    assert probe.maximum == 2
    assert [execution.result.scenario_id for execution in executions] == [
        scenario.id for scenario in scenarios
    ]
    assert [execution.result.output for execution in executions] == [
        {"scenario_id": scenario.id} for scenario in scenarios
    ]


def test_empty_batch_does_not_create_adapters() -> None:
    factory = ScriptedFactory([])

    assert ScenarioExecutor(factory, max_concurrency=2).execute_many([]) == ()
    assert factory.contexts == []
