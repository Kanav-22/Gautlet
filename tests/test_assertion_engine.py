"""Tests for all nine evidence-linked deterministic assertion types."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from gauntlet.adapters import JsonObject
from gauntlet.config.models import NetworkPolicy
from gauntlet.core.models import (
    Evidence,
    EvidenceType,
    Scenario,
    ScenarioResult,
    ScenarioResultStatus,
)
from gauntlet.evidence import ScenarioEvidenceBundle
from gauntlet.execution import (
    AssertionConfigurationError,
    AssertionEngine,
    AssertionEvaluationError,
    AssertionResult,
    AssertionType,
    AttemptRecord,
    ScenarioExecution,
    ScenarioLifecycleState,
)

NOW = datetime(2026, 7, 14, 2, 3, 4, tzinfo=UTC)


def tool_event(
    tool: str,
    *,
    fixture_index: int = 0,
    policy_result: str = "allowed",
    outcome: str = "returned",
) -> JsonObject:
    return {
        "type": "tool_call",
        "tool": tool,
        "arguments": {},
        "fixture_index": fixture_index,
        "policy_result": policy_result,
        "outcome": outcome,
    }


def make_bundle(
    assertions: list[JsonObject],
    *,
    output: JsonObject | None = None,
    trace: tuple[JsonObject, ...] = (),
    fixtures: JsonObject | None = None,
    status: ScenarioResultStatus = ScenarioResultStatus.PASSED,
    omit_role: str | None = None,
    unknown_ref_role: str | None = None,
) -> tuple[Scenario, ScenarioEvidenceBundle]:
    scenario = Scenario(
        id="test.assertions",
        title="Assertions",
        description="Exercise deterministic assertions.",
        category="correctness",
        difficulty=1,
        tags=[],
        required_capabilities=["invoke", "trace_tool_calls"],
        input={},
        fixtures=fixtures or {"tool_sequence": []},
        execution_policy={},
        assertions=assertions,
        metrics=[],
    )
    error: JsonObject | None = (
        {"type": "timeout"} if status is ScenarioResultStatus.TIMED_OUT else None
    )
    attempt = AttemptRecord(
        attempt_number=1,
        started_at=NOW,
        finished_at=NOW,
        duration_ms=10,
        output=output,
        error=error,
        trace=trace,
        usage={"tool_calls": sum(event.get("type") == "tool_call" for event in trace)},
        stderr="",
        isolation_level="subprocess",
        timed_out=status is ScenarioResultStatus.TIMED_OUT,
        retryable=False,
    )
    terminal = {
        ScenarioResultStatus.PASSED: ScenarioLifecycleState.PASSED,
        ScenarioResultStatus.FAILED: ScenarioLifecycleState.FAILED,
        ScenarioResultStatus.ERROR: ScenarioLifecycleState.ERROR,
        ScenarioResultStatus.TIMED_OUT: ScenarioLifecycleState.TIMED_OUT,
    }[status]
    roles = ["output", "trace", "fixtures", "execution"]
    if status is ScenarioResultStatus.TIMED_OUT:
        roles.append("exception")
    refs_by_role: dict[str, tuple[str, ...]] = {}
    evidence: list[Evidence] = []
    for role in roles:
        if role == omit_role:
            continue
        evidence_id = f"evidence-{role}"
        refs_by_role[role] = ("evidence-unknown" if role == unknown_ref_role else evidence_id,)
        evidence.append(
            Evidence(
                id=evidence_id,
                type=EvidenceType.ARTIFACT,
                path=f"evidence/{role}.json",
                content_hash=f"sha256:{role}",
                redacted=False,
                metadata={"role": role},
            )
        )
    result = ScenarioResult(
        scenario_id=scenario.id,
        status=status,
        started_at=NOW,
        finished_at=NOW,
        duration_ms=10,
        output=output,
        error=error,
        metrics={},
        evidence_refs=[item.id for item in evidence],
        findings=[],
    )
    execution = ScenarioExecution(
        result=result,
        lifecycle=(
            ScenarioLifecycleState.LOADED,
            ScenarioLifecycleState.VALIDATED,
            ScenarioLifecycleState.PREPARED,
            ScenarioLifecycleState.RUNNING,
            terminal,
            ScenarioLifecycleState.FINALIZED,
        ),
        attempts=(attempt,),
        seed=42,
        network_policy=NetworkPolicy.DISABLED,
    )
    return scenario, ScenarioEvidenceBundle(
        execution=execution,
        evidence=tuple(evidence),
        refs_by_role=refs_by_role,
    )


def test_all_nine_assertions_pass_in_order_with_known_evidence_refs() -> None:
    assertions: list[JsonObject] = [
        {"type": "tool_called", "tool": "lookup"},
        {"type": "max_tool_calls", "value": 1},
        {"type": "output_contains", "value": 42},
        {"type": "output_field_equals", "field": "completed", "value": True},
        {
            "type": "schema_valid",
            "schema": {
                "type": "object",
                "required": ["completed", "value"],
                "properties": {
                    "completed": {"const": True},
                    "value": {"type": "integer"},
                },
            },
        },
        {"type": "no_forbidden_calls", "tools": ["delete"]},
        {"type": "max_steps", "value": 1},
        {"type": "no_hallucinated_success"},
        {"type": "completed_before_timeout"},
    ]
    scenario, bundle = make_bundle(
        assertions,
        output={"completed": True, "value": 42},
        trace=(tool_event("lookup"),),
        fixtures={"tool_sequence": [{"tool": "lookup", "response": {"value": 42}}]},
    )

    evaluation = AssertionEngine().evaluate(scenario, bundle)

    assert [result.type for result in evaluation.results] == list(AssertionType)
    assert all(result.passed for result in evaluation.results)
    known_ids = {item.id for item in bundle.evidence}
    assert all(result.evidence_refs for result in evaluation.results)
    assert all(set(result.evidence_refs) <= known_ids for result in evaluation.results)
    assert evaluation.execution.result.status is ScenarioResultStatus.PASSED


@pytest.mark.parametrize(
    ("assertion", "output", "trace"),
    [
        ({"type": "tool_called", "tool": "save"}, {}, (tool_event("lookup"),)),
        ({"type": "max_tool_calls", "value": 0}, {}, (tool_event("lookup"),)),
        ({"type": "output_contains", "value": True}, {"value": 1}, ()),
        ({"type": "output_field_equals", "field": "missing", "value": None}, {}, ()),
        (
            {"type": "schema_valid", "schema": {"type": "object", "required": ["answer"]}},
            {},
            (),
        ),
        (
            {"type": "no_forbidden_calls", "tools": ["delete"]},
            {},
            (tool_event("delete", policy_result="denied"),),
        ),
        ({"type": "max_steps", "value": 0}, {}, ({"type": "thought"},)),
    ],
)
def test_expected_mismatches_fail_without_becoming_engine_errors(
    assertion: JsonObject,
    output: JsonObject,
    trace: tuple[JsonObject, ...],
) -> None:
    scenario, bundle = make_bundle([assertion], output=output, trace=trace)

    evaluation = AssertionEngine().evaluate(scenario, bundle)

    assert evaluation.results[0].passed is False
    assert evaluation.execution.result.status is ScenarioResultStatus.FAILED
    assert evaluation.execution.lifecycle[-2] is ScenarioLifecycleState.FAILED


def test_no_hallucinated_success_requires_fixture_consumption() -> None:
    scenario, bundle = make_bundle(
        [{"type": "no_hallucinated_success"}],
        output={"completed": True},
        trace=(tool_event("lookup"),),
        fixtures={
            "tool_sequence": [
                {"tool": "lookup", "response": {"value": 42}},
                {"tool": "save", "response": {"ok": True}},
            ]
        },
    )

    result = AssertionEngine().evaluate(scenario, bundle).results[0]

    assert result.passed is False
    assert result.details == {"consumed_fixtures": 1, "expected_fixtures": 2}
    assert len(result.evidence_refs) == 3


def test_output_contains_searches_values_and_strings_not_keys() -> None:
    passing, passing_bundle = make_bundle(
        [{"type": "output_contains", "value": "needle"}],
        output={"message": "a needle in text"},
    )
    failing, failing_bundle = make_bundle(
        [{"type": "output_contains", "value": "needle"}],
        output={"needle": "not in values"},
    )

    assert AssertionEngine().evaluate(passing, passing_bundle).results[0].passed is True
    assert AssertionEngine().evaluate(failing, failing_bundle).results[0].passed is False


def test_schema_valid_supports_local_refs_and_rejects_external_refs() -> None:
    local, local_bundle = make_bundle(
        [
            {
                "type": "schema_valid",
                "schema": {
                    "$defs": {"answer": {"type": "integer"}},
                    "type": "object",
                    "properties": {"answer": {"$ref": "#/$defs/answer"}},
                },
            }
        ],
        output={"answer": 42},
    )
    external, external_bundle = make_bundle(
        [{"type": "schema_valid", "schema": {"$ref": "https://example.invalid/schema"}}],
        output={},
    )

    assert AssertionEngine().evaluate(local, local_bundle).results[0].passed is True
    with pytest.raises(AssertionConfigurationError, match="non-local"):
        AssertionEngine().evaluate(external, external_bundle)


@pytest.mark.parametrize(
    "assertion",
    [
        {"type": "unknown"},
        {"type": "tool_called", "tool": ""},
        {"type": "max_tool_calls", "value": True},
        {"type": "max_steps", "value": -1},
        {"type": "output_field_equals", "field": " ", "value": 1},
        {"type": "no_forbidden_calls", "tools": ["delete", "delete"]},
        {"type": "completed_before_timeout", "extra": True},
        {"type": "schema_valid", "schema": {"type": "not-a-json-schema-type"}},
    ],
)
def test_invalid_config_fails_before_evaluation(assertion: JsonObject) -> None:
    scenario, bundle = make_bundle([assertion], output={})

    with pytest.raises(AssertionConfigurationError):
        AssertionEngine().evaluate(scenario, bundle)


def test_timeout_stays_timed_out_and_links_exception_evidence() -> None:
    scenario, bundle = make_bundle(
        [{"type": "completed_before_timeout"}],
        status=ScenarioResultStatus.TIMED_OUT,
    )

    evaluation = AssertionEngine().evaluate(scenario, bundle)

    assert evaluation.results[0].passed is False
    assert set(evaluation.results[0].evidence_refs) == {
        "evidence-execution",
        "evidence-exception",
    }
    assert evaluation.execution.result.status is ScenarioResultStatus.TIMED_OUT


@pytest.mark.parametrize(
    ("omit_role", "assertion"),
    [
        ("trace", {"type": "tool_called", "tool": "lookup"}),
        ("output", {"type": "output_contains", "value": 42}),
        ("execution", {"type": "completed_before_timeout"}),
    ],
)
def test_missing_required_evidence_is_an_evaluator_error(
    omit_role: str,
    assertion: JsonObject,
) -> None:
    scenario, bundle = make_bundle([assertion], output={}, omit_role=omit_role)

    with pytest.raises(AssertionEvaluationError, match="missing"):
        AssertionEngine().evaluate(scenario, bundle)


def test_unknown_evidence_reference_is_rejected() -> None:
    scenario, bundle = make_bundle(
        [{"type": "tool_called", "tool": "lookup"}],
        trace=(tool_event("lookup"),),
        unknown_ref_role="trace",
    )

    with pytest.raises(AssertionEvaluationError, match="unknown evidence"):
        AssertionEngine().evaluate(scenario, bundle)


def test_assertion_result_requires_nonempty_unique_evidence_refs() -> None:
    with pytest.raises(ValidationError):
        AssertionResult(
            assertion_index=0,
            type=AssertionType.MAX_STEPS,
            passed=True,
            message="ok",
            details={},
            evidence_refs=[],
        )
    with pytest.raises(ValidationError):
        AssertionResult(
            assertion_index=0,
            type=AssertionType.MAX_STEPS,
            passed=True,
            message="ok",
            details={},
            evidence_refs=["same", "same"],
        )
