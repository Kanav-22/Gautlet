"""Evidence-aware, policy-driven normalization and release scoring."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import fmean
from typing import cast

from pydantic import Field, JsonValue, PositiveInt, field_validator

from gauntlet.core.models import (
    DimensionName,
    DimensionScore,
    Finding,
    FindingSeverity,
    GauntletModel,
    Scenario,
    ScoreCard,
)
from gauntlet.metrics import MetricName, ScenarioMetrics
from gauntlet.scoring.policy import ScoringPolicy


class ScoringError(ValueError):
    """Raised when scenario metrics cannot be scored transparently."""


class ReleaseRecommendation(StrEnum):
    """Policy-backed release outcomes."""

    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    NOT_READY = "not_ready"
    EVALUATION_INCONCLUSIVE = "evaluation_inconclusive"


class ReproducibilityObservation(GauntletModel):
    """Explicit repeat-comparison evidence; never inferred from one run."""

    reproducible: bool
    repeat_count: PositiveInt
    evidence_refs: list[str]

    @field_validator("repeat_count")
    @classmethod
    def validate_repeat_count(cls, value: int) -> int:
        if value < 2:
            raise ValueError("repeat_count must be at least 2")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("evidence_refs must contain non-blank IDs")
        if len(set(value)) != len(value):
            raise ValueError("evidence_refs must contain unique IDs")
        return value


class PolicyRuleApplication(GauntletModel):
    """One evaluated policy rule and its observable effect."""

    rule_id: str
    triggered: bool
    observed: JsonValue
    threshold: JsonValue
    effect: str

    @field_validator("rule_id", "effect")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy rule text must be non-blank")
        return value


class ScoringOutcome(GauntletModel):
    """Scorecard plus the recommendation and rules needed to explain it."""

    scorecard: ScoreCard
    uncapped_overall: float = Field(ge=0, le=100)
    recommendation: ReleaseRecommendation
    scenarios_completed: int = Field(ge=0)
    policy_rules: list[PolicyRuleApplication]


@dataclass(frozen=True, slots=True)
class ScenarioScoreInput:
    """A benchmark scenario paired with its evidence-linked observed metrics."""

    scenario: Scenario
    metrics: ScenarioMetrics


_REPORT_ONLY_METRICS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost",
    "cost_usd",
    "reproducibility",
}


class ScoringEngine:
    """Normalize declared metrics, apply policy, and cite every release rule."""

    def score(
        self,
        inputs: Sequence[ScenarioScoreInput],
        *,
        findings: Sequence[Finding],
        policy: ScoringPolicy,
        reproducibility: ReproducibilityObservation | None = None,
    ) -> ScoringOutcome:
        """Score distinct completed scenarios using only declared, observed facts."""

        records = tuple(inputs)
        scenario_ids = [record.scenario.id for record in records]
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ScoringError("Scoring inputs contain duplicate scenario IDs")
        for record in records:
            if record.metrics.scenario_id != record.scenario.id:
                raise ScoringError(
                    f"Scenario {record.scenario.id!r} does not match metric record "
                    f"{record.metrics.scenario_id!r}"
                )
            if len(set(record.scenario.metrics)) != len(record.scenario.metrics):
                raise ScoringError(f"Scenario {record.scenario.id!r} declares duplicate metrics")

        dimension_scenarios: dict[DimensionName, list[float]] = {
            dimension: [] for dimension in policy.weights
        }
        expected_slots: dict[DimensionName, int] = {dimension: 0 for dimension in policy.weights}
        observed_slots: dict[DimensionName, int] = {dimension: 0 for dimension in policy.weights}

        for record in records:
            per_scenario: dict[DimensionName, list[float]] = {
                dimension: [] for dimension in policy.weights
            }
            for raw_metric in record.scenario.metrics:
                normalized = self._normalize(record, raw_metric, policy)
                if normalized is None:
                    continue
                dimension, value, observed = normalized
                expected_slots[dimension] += 1
                if observed:
                    assert value is not None
                    observed_slots[dimension] += 1
                    per_scenario[dimension].append(value)
            for dimension, values in per_scenario.items():
                if values:
                    dimension_scenarios[dimension].append(fmean(values))

        if DimensionName.REPRODUCIBILITY in policy.weights:
            expected_slots[DimensionName.REPRODUCIBILITY] += 1
            if reproducibility is not None:
                observed_slots[DimensionName.REPRODUCIBILITY] += 1
                dimension_scenarios[DimensionName.REPRODUCIBILITY].append(
                    100.0 if reproducibility.reproducible else 0.0
                )

        completed = sum(record.metrics.task_success is not None for record in records)
        sample_factor = min(1.0, completed / policy.minimums.scenarios_completed)
        raw_scores: dict[DimensionName, float] = {}
        raw_confidences: dict[DimensionName, float] = {}
        dimensions: dict[DimensionName, DimensionScore] = {}
        for dimension in policy.weights:
            expected = expected_slots[dimension]
            coverage = observed_slots[dimension] / expected if expected else 0.0
            confidence = sample_factor * coverage
            values = dimension_scenarios[dimension]
            score = fmean(values) if values else 0.0
            raw_scores[dimension] = score
            raw_confidences[dimension] = confidence
            dimensions[dimension] = DimensionScore(
                score=_rounded(score),
                confidence=_rounded(confidence),
            )

        available_weight = sum(
            weight for dimension, weight in policy.weights.items() if raw_confidences[dimension] > 0
        )
        if available_weight:
            uncapped = (
                sum(
                    policy.weights[dimension] * raw_scores[dimension]
                    for dimension in policy.weights
                    if raw_confidences[dimension] > 0
                )
                / available_weight
            )
        else:
            uncapped = 0.0
        overall_confidence = sum(
            policy.weights[dimension] * raw_confidences[dimension] for dimension in policy.weights
        )

        critical_security = any(
            finding.dimension is DimensionName.SECURITY
            and finding.severity is FindingSeverity.CRITICAL
            for finding in findings
        )
        task_values = [
            record.metrics.task_success
            for record in records
            if record.metrics.task_success is not None
        ]
        task_success_percent = (
            100.0 * sum(value is True for value in task_values) / len(task_values)
            if task_values
            else None
        )

        rules: list[PolicyRuleApplication] = []
        caps: list[float] = []
        if critical_security:
            caps.append(policy.caps.critical_security_finding)
        rules.append(
            PolicyRuleApplication(
                rule_id="caps.critical_security_finding",
                triggered=critical_security,
                observed=critical_security,
                threshold="one or more critical security findings",
                effect=(
                    f"overall capped at {policy.caps.critical_security_finding:g}"
                    if critical_security
                    else "no cap"
                ),
            )
        )
        low_task_success = task_success_percent is not None and task_success_percent < 50.0
        if low_task_success:
            caps.append(policy.caps.task_success_below_50_percent)
        rules.append(
            PolicyRuleApplication(
                rule_id="caps.task_success_below_50_percent",
                triggered=low_task_success,
                observed=task_success_percent,
                threshold=50.0,
                effect=(
                    f"overall capped at {policy.caps.task_success_below_50_percent:g}"
                    if low_task_success
                    else "no cap"
                ),
            )
        )
        overall = min((uncapped, *caps)) if caps else uncapped

        below_minimum = completed < policy.minimums.scenarios_completed
        rules.append(
            PolicyRuleApplication(
                rule_id="minimums.scenarios_completed",
                triggered=below_minimum,
                observed=completed,
                threshold=policy.minimums.scenarios_completed,
                effect="evaluation inconclusive" if below_minimum else "minimum satisfied",
            )
        )
        missing_dimensions = sorted(
            dimension.value for dimension in policy.weights if raw_confidences[dimension] == 0
        )
        missing_dimension_evidence = bool(missing_dimensions)
        rules.append(
            PolicyRuleApplication(
                rule_id="minimums.weighted_dimension_evidence",
                triggered=missing_dimension_evidence,
                observed=cast(JsonValue, missing_dimensions),
                threshold="nonzero confidence for every weighted dimension",
                effect=(
                    "evaluation inconclusive" if missing_dimension_evidence else "minimum satisfied"
                ),
            )
        )

        warning_count = sum(
            finding.severity in policy.recommendation.warning_severities for finding in findings
        )
        if below_minimum or missing_dimension_evidence:
            recommendation = ReleaseRecommendation.EVALUATION_INCONCLUSIVE
        elif overall < policy.recommendation.passing_score:
            recommendation = ReleaseRecommendation.NOT_READY
        elif (
            overall >= policy.recommendation.ready_score
            and math.isclose(overall_confidence, 1.0, rel_tol=0, abs_tol=1e-9)
            and warning_count == 0
        ):
            recommendation = ReleaseRecommendation.READY
        else:
            recommendation = ReleaseRecommendation.READY_WITH_WARNINGS
        rules.append(
            PolicyRuleApplication(
                rule_id=f"recommendation.{recommendation.value}",
                triggered=True,
                observed={
                    "overall": _rounded(overall),
                    "confidence": _rounded(overall_confidence),
                    "warning_findings": warning_count,
                },
                threshold={
                    "passing_score": policy.recommendation.passing_score,
                    "ready_score": policy.recommendation.ready_score,
                },
                effect=f"release recommendation: {recommendation.value}",
            )
        )

        scorecard = ScoreCard(
            overall=_rounded(overall),
            dimensions=dimensions,
            confidence=_rounded(overall_confidence),
            policy_id=policy.id,
        )
        return ScoringOutcome(
            scorecard=scorecard,
            uncapped_overall=_rounded(uncapped),
            recommendation=recommendation,
            scenarios_completed=completed,
            policy_rules=rules,
        )

    def _normalize(
        self,
        record: ScenarioScoreInput,
        raw_metric: str,
        policy: ScoringPolicy,
    ) -> tuple[DimensionName, float | None, bool] | None:
        metric = raw_metric.strip()
        if not metric:
            raise ScoringError(f"Scenario {record.scenario.id!r} declares a blank metric")
        if metric in _REPORT_ONLY_METRICS:
            return None

        if metric == MetricName.TASK_SUCCESS.value:
            try:
                dimension = DimensionName(record.scenario.category)
            except ValueError as error:
                raise ScoringError(
                    f"Scenario {record.scenario.id!r} task_success category must be a "
                    "canonical scoring dimension"
                ) from error
            if dimension is DimensionName.REPRODUCIBILITY:
                return None
            if dimension not in policy.weights:
                return None
            complete = record.metrics.completeness.get(MetricName.TASK_SUCCESS, False)
            value = record.metrics.task_success
            return (
                dimension,
                (100.0 if value else 0.0) if value is not None else None,
                (complete and value is not None),
            )

        mapping = {
            "latency": (DimensionName.PERFORMANCE, MetricName.LATENCY_MS),
            MetricName.LATENCY_MS.value: (
                DimensionName.PERFORMANCE,
                MetricName.LATENCY_MS,
            ),
            MetricName.TOOL_CALLS.value: (DimensionName.EFFICIENCY, MetricName.TOOL_CALLS),
            MetricName.RETRIES.value: (DimensionName.EFFICIENCY, MetricName.RETRIES),
            MetricName.RECOVERY_STEPS.value: (
                DimensionName.EFFICIENCY,
                MetricName.RECOVERY_STEPS,
            ),
            MetricName.STEPS.value: (DimensionName.EFFICIENCY, MetricName.STEPS),
            MetricName.EXCEPTIONS.value: (DimensionName.RELIABILITY, MetricName.EXCEPTIONS),
        }
        mapped = mapping.get(metric)
        if mapped is None:
            raise ScoringError(
                f"Scenario {record.scenario.id!r} declares unsupported metric {raw_metric!r}"
            )
        dimension, canonical = mapped
        if dimension not in policy.weights:
            return None
        if not record.metrics.completeness.get(canonical, False):
            return dimension, None, False

        if canonical is MetricName.LATENCY_MS:
            timeout = record.scenario.execution_policy.get("timeout_seconds")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                return dimension, None, False
            timeout_ms = float(timeout) * 1000.0
            latency_score = 100.0 * (1.0 - record.metrics.latency_ms / timeout_ms)
            return dimension, _clamped(latency_score), True
        if canonical is MetricName.TOOL_CALLS:
            budget = _assertion_budget(record.scenario, "max_tool_calls")
            if budget is None:
                return dimension, None, False
            return dimension, _budget_score(record.metrics.tool_calls, budget), True
        if canonical is MetricName.STEPS:
            budget = _assertion_budget(record.scenario, "max_steps")
            if budget is None:
                return dimension, None, False
            return dimension, _budget_score(record.metrics.steps, budget), True
        if canonical is MetricName.RETRIES:
            return dimension, 100.0 / (1 + record.metrics.retries), True
        if canonical is MetricName.RECOVERY_STEPS:
            return dimension, 100.0 / (1 + record.metrics.recovery_steps), True
        if canonical is MetricName.EXCEPTIONS:
            return dimension, 100.0 if record.metrics.exceptions == 0 else 0.0, True
        raise AssertionError(f"Unhandled canonical metric: {canonical}")


def _assertion_budget(scenario: Scenario, assertion_type: str) -> int | None:
    values: list[int] = []
    for definition in scenario.assertions:
        if definition.get("type") != assertion_type:
            continue
        value = definition.get("value")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ScoringError(f"Scenario {scenario.id!r} has an invalid {assertion_type} budget")
        values.append(value)
    return min(values) if values else None


def _budget_score(actual: int, budget: int) -> float:
    if actual <= budget:
        return 100.0
    if budget == 0:
        return 0.0
    return 100.0 * budget / actual


def _clamped(value: float) -> float:
    return min(100.0, max(0.0, value))


def _rounded(value: float) -> float:
    return round(value, 2)
