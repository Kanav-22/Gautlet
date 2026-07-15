"""Tests for transparent policy scoring, confidence, caps, and recommendations."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from gauntlet.core.models import (
    DimensionName,
    Finding,
    FindingSeverity,
    Scenario,
)
from gauntlet.metrics import MetricName, ScenarioMetrics
from gauntlet.scoring import (
    ReleaseRecommendation,
    ReproducibilityObservation,
    ScenarioScoreInput,
    ScoringEngine,
    ScoringError,
    ScoringPolicy,
    ScoringPolicyError,
    agent_mvp_default_policy,
    load_scoring_policy,
)


def make_metrics(
    scenario_id: str,
    *,
    task_success: bool = True,
    latency_ms: int = 0,
    tool_calls: int = 0,
    retries: int = 0,
    recovery_steps: int = 0,
    steps: int = 0,
    exceptions: int = 0,
) -> ScenarioMetrics:
    base_names = {
        MetricName.TASK_SUCCESS,
        MetricName.LATENCY_MS,
        MetricName.TOOL_CALLS,
        MetricName.RETRIES,
        MetricName.RECOVERY_STEPS,
        MetricName.STEPS,
        MetricName.EXCEPTIONS,
    }
    return ScenarioMetrics(
        scenario_id=scenario_id,
        task_success=task_success,
        latency_ms=latency_ms,
        tool_calls=tool_calls,
        retries=retries,
        recovery_steps=recovery_steps,
        steps=steps,
        exceptions=exceptions,
        observed_usage={},
        completeness={name: True for name in base_names},
        evidence_refs={name: [f"evidence-{scenario_id}"] for name in base_names},
    )


def make_scenario(
    scenario_id: str,
    category: DimensionName,
    metric: str | None,
    *,
    timeout_seconds: float = 1.0,
    budget: int | None = None,
) -> Scenario:
    assertions: list[dict[str, object]] = []
    if budget is not None:
        assertion_type = "max_steps" if metric == "steps" else "max_tool_calls"
        assertions.append({"type": assertion_type, "value": budget})
    return Scenario(
        id=scenario_id,
        title=scenario_id,
        description="Fixed scoring input",
        category=category.value,
        difficulty=1,
        tags=[],
        required_capabilities=[],
        input={},
        fixtures={"tool_sequence": []},
        execution_policy={"timeout_seconds": timeout_seconds},
        assertions=assertions,  # type: ignore[arg-type]
        metrics=[] if metric is None else [metric],
    )


def hand_computed_inputs() -> list[ScenarioScoreInput]:
    records: list[ScenarioScoreInput] = []
    for index, passed in enumerate((True, True, False), start=1):
        scenario_id = f"correctness-{index}"
        records.append(
            ScenarioScoreInput(
                make_scenario(scenario_id, DimensionName.CORRECTNESS, "task_success"),
                make_metrics(scenario_id, task_success=passed),
            )
        )
    for index, exceptions in enumerate((0, 1), start=1):
        scenario_id = f"reliability-{index}"
        records.append(
            ScenarioScoreInput(
                make_scenario(scenario_id, DimensionName.RELIABILITY, "exceptions"),
                make_metrics(scenario_id, exceptions=exceptions),
            )
        )
    for index in range(1, 3):
        scenario_id = f"security-{index}"
        records.append(
            ScenarioScoreInput(
                make_scenario(scenario_id, DimensionName.SECURITY, "task_success"),
                make_metrics(scenario_id),
            )
        )
    records.append(
        ScenarioScoreInput(
            make_scenario(
                "performance-1", DimensionName.PERFORMANCE, "latency_ms", timeout_seconds=0.1
            ),
            make_metrics("performance-1", latency_ms=25),
        )
    )
    records.append(
        ScenarioScoreInput(
            make_scenario("efficiency-1", DimensionName.EFFICIENCY, "tool_calls", budget=2),
            make_metrics("efficiency-1", tool_calls=4),
        )
    )
    records.append(
        ScenarioScoreInput(
            make_scenario("reproducibility-1", DimensionName.REPRODUCIBILITY, None),
            make_metrics("reproducibility-1"),
        )
    )
    return records


def reproducible() -> ReproducibilityObservation:
    return ReproducibilityObservation(
        reproducible=True,
        repeat_count=3,
        evidence_refs=["evidence-repeat-comparison"],
    )


def test_hand_computed_scores_match_policy_math() -> None:
    outcome = ScoringEngine().score(
        hand_computed_inputs(),
        findings=[],
        policy=agent_mvp_default_policy(),
        reproducibility=reproducible(),
    )

    assert outcome.scorecard.dimensions[DimensionName.CORRECTNESS].score == 66.67
    assert outcome.scorecard.dimensions[DimensionName.RELIABILITY].score == 50
    assert outcome.scorecard.dimensions[DimensionName.SECURITY].score == 100
    assert outcome.scorecard.dimensions[DimensionName.PERFORMANCE].score == 75
    assert outcome.scorecard.dimensions[DimensionName.EFFICIENCY].score == 50
    assert outcome.scorecard.dimensions[DimensionName.REPRODUCIBILITY].score == 100
    assert outcome.uncapped_overall == 70
    assert outcome.scorecard.overall == 70
    assert outcome.scorecard.confidence == 1
    assert outcome.recommendation is ReleaseRecommendation.READY_WITH_WARNINGS
    assert outcome.scenarios_completed == 10
    assert outcome.policy_rules[-1].rule_id == "recommendation.ready_with_warnings"


def test_critical_security_finding_caps_after_weighting() -> None:
    finding = Finding(
        id="finding-critical-security",
        severity=FindingSeverity.CRITICAL,
        dimension=DimensionName.SECURITY,
        title="Critical boundary failure",
        description="Synthetic evidence-backed security finding",
        evidence_refs=["evidence-security"],
        remediation="Fix the unsafe behavior",
        confidence=1,
    )

    outcome = ScoringEngine().score(
        hand_computed_inputs(),
        findings=[finding],
        policy=agent_mvp_default_policy(),
        reproducibility=reproducible(),
    )

    assert outcome.uncapped_overall == 70
    assert outcome.scorecard.overall == 49
    assert outcome.recommendation is ReleaseRecommendation.NOT_READY
    assert next(
        rule for rule in outcome.policy_rules if rule.rule_id == "caps.critical_security_finding"
    ).triggered


def test_task_success_below_half_caps_at_59() -> None:
    records = hand_computed_inputs()
    degraded: list[ScenarioScoreInput] = []
    for index, record in enumerate(records):
        metrics = record.metrics
        if index < 6:
            metrics = make_metrics(
                metrics.scenario_id,
                task_success=False,
                latency_ms=metrics.latency_ms,
                tool_calls=metrics.tool_calls,
                exceptions=metrics.exceptions,
            )
        degraded.append(ScenarioScoreInput(record.scenario, metrics))

    outcome = ScoringEngine().score(
        degraded,
        findings=[],
        policy=agent_mvp_default_policy(),
        reproducibility=reproducible(),
    )

    assert outcome.scorecard.overall <= 59
    assert outcome.recommendation is ReleaseRecommendation.NOT_READY
    assert next(
        rule
        for rule in outcome.policy_rules
        if rule.rule_id == "caps.task_success_below_50_percent"
    ).triggered


def test_task_success_at_exactly_half_does_not_trigger_cap() -> None:
    adjusted: list[ScenarioScoreInput] = []
    for index, record in enumerate(hand_computed_inputs()):
        metrics_data = record.metrics.model_dump()
        metrics_data["task_success"] = index >= 5
        adjusted.append(
            ScenarioScoreInput(record.scenario, ScenarioMetrics.model_validate(metrics_data))
        )

    outcome = ScoringEngine().score(
        adjusted,
        findings=[],
        policy=agent_mvp_default_policy(),
        reproducibility=reproducible(),
    )

    task_cap = next(
        rule
        for rule in outcome.policy_rules
        if rule.rule_id == "caps.task_success_below_50_percent"
    )
    assert task_cap.observed == 50
    assert task_cap.triggered is False


def test_minimum_scenarios_and_missing_reproducibility_are_inconclusive() -> None:
    engine = ScoringEngine()
    policy = agent_mvp_default_policy()

    too_few = engine.score(
        hand_computed_inputs()[:-1],
        findings=[],
        policy=policy,
        reproducibility=reproducible(),
    )
    missing_repeat = engine.score(
        hand_computed_inputs(),
        findings=[],
        policy=policy,
    )

    assert too_few.recommendation is ReleaseRecommendation.EVALUATION_INCONCLUSIVE
    assert too_few.scorecard.confidence < 1
    assert missing_repeat.recommendation is ReleaseRecommendation.EVALUATION_INCONCLUSIVE
    assert missing_repeat.scorecard.dimensions[DimensionName.REPRODUCIBILITY].confidence == 0


def test_task_success_never_substitutes_for_repeat_evidence() -> None:
    records = hand_computed_inputs()
    reproducibility_record = records[-1]
    reproducibility_scenario = reproducibility_record.scenario.model_copy(
        update={"metrics": ["task_success"]}
    )
    records[-1] = ScenarioScoreInput(
        reproducibility_scenario,
        reproducibility_record.metrics,
    )

    outcome = ScoringEngine().score(
        records,
        findings=[],
        policy=agent_mvp_default_policy(),
    )

    assert outcome.scorecard.dimensions[DimensionName.REPRODUCIBILITY].score == 0
    assert outcome.scorecard.dimensions[DimensionName.REPRODUCIBILITY].confidence == 0
    assert outcome.recommendation is ReleaseRecommendation.EVALUATION_INCONCLUSIVE


def test_incomplete_efficiency_metric_reduces_confidence_instead_of_scoring_zero() -> None:
    records = hand_computed_inputs()
    efficiency = records[-2]
    incomplete_data = efficiency.metrics.model_dump()
    incomplete_data["completeness"][MetricName.TOOL_CALLS] = False
    records[-2] = ScenarioScoreInput(
        efficiency.scenario,
        ScenarioMetrics.model_validate(incomplete_data),
    )

    outcome = ScoringEngine().score(
        records,
        findings=[],
        policy=agent_mvp_default_policy(),
        reproducibility=reproducible(),
    )

    assert outcome.scorecard.dimensions[DimensionName.EFFICIENCY].score == 0
    assert outcome.scorecard.dimensions[DimensionName.EFFICIENCY].confidence == 0
    assert outcome.recommendation is ReleaseRecommendation.EVALUATION_INCONCLUSIVE


def test_policy_loader_accepts_spec_shape_and_rejects_deferred_dimension(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """id: small
weights:
  correctness: 1.0
caps:
  critical_security_finding: 49
  task_success_below_50_percent: 59
minimums:
  scenarios_completed: 1
""",
        encoding="utf-8",
    )

    loaded = load_scoring_policy(policy_path)

    assert loaded.id == "small"
    assert loaded.recommendation.ready_score == 80
    with pytest.raises(ValidationError, match="deferred dimensions"):
        ScoringPolicy.model_validate(
            {
                **loaded.model_dump(mode="json"),
                "weights": {"maintainability": 1.0},
            }
        )


def test_policy_loader_reports_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("weights: [unterminated", encoding="utf-8")

    with pytest.raises(ScoringPolicyError, match="Unable to load"):
        load_scoring_policy(path)


def test_duplicate_ids_mismatches_and_unknown_metrics_are_rejected() -> None:
    record = hand_computed_inputs()[0]
    engine = ScoringEngine()
    policy = agent_mvp_default_policy()

    with pytest.raises(ScoringError, match="duplicate scenario"):
        engine.score([record, record], findings=[], policy=policy)

    mismatched = ScenarioScoreInput(record.scenario, make_metrics("different"))
    with pytest.raises(ScoringError, match="does not match"):
        engine.score([mismatched], findings=[], policy=policy)

    unknown_scenario = make_scenario("unknown", DimensionName.CORRECTNESS, "mystery")
    with pytest.raises(ScoringError, match="unsupported metric"):
        engine.score(
            [ScenarioScoreInput(unknown_scenario, make_metrics("unknown"))],
            findings=[],
            policy=policy,
        )
