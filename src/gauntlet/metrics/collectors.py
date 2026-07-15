"""Evidence-linked metric collection over finalized scenario executions."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, TypeAlias, cast

from pydantic import Field, NonNegativeInt, field_validator, model_validator

from gauntlet.adapters import JsonObject
from gauntlet.core.models import GauntletModel, ScenarioResultStatus
from gauntlet.evidence import ScenarioEvidenceBundle


class MetricCollectionError(ValueError):
    """Raised when execution facts cannot produce honest metric observations."""


class MetricName(StrEnum):
    """Canonical metric names emitted by the MVP collector."""

    TASK_SUCCESS = "task_success"
    LATENCY_MS = "latency_ms"
    TOOL_CALLS = "tool_calls"
    RETRIES = "retries"
    RECOVERY_STEPS = "recovery_steps"
    STEPS = "steps"
    EXCEPTIONS = "exceptions"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    TOTAL_TOKENS = "total_tokens"
    COST_USD = "cost_usd"


UsageValue: TypeAlias = Annotated[int | float, Field(ge=0)]

_TOKEN_METRICS = frozenset(
    {
        MetricName.INPUT_TOKENS,
        MetricName.OUTPUT_TOKENS,
        MetricName.TOTAL_TOKENS,
    }
)
_USAGE_METRICS = _TOKEN_METRICS | {MetricName.COST_USD}
_REQUIRED_METRICS = (
    MetricName.LATENCY_MS,
    MetricName.TOOL_CALLS,
    MetricName.RETRIES,
    MetricName.RECOVERY_STEPS,
    MetricName.STEPS,
    MetricName.EXCEPTIONS,
)


class ScenarioMetrics(GauntletModel):
    """Normalized metrics and provenance for one finalized scenario."""

    scenario_id: str
    task_success: bool | None = None
    latency_ms: NonNegativeInt
    tool_calls: NonNegativeInt
    retries: NonNegativeInt
    recovery_steps: NonNegativeInt
    steps: NonNegativeInt
    exceptions: NonNegativeInt
    observed_usage: dict[MetricName, UsageValue]
    completeness: dict[MetricName, bool]
    evidence_refs: dict[MetricName, list[str]]

    @field_validator("scenario_id")
    @classmethod
    def validate_scenario_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scenario_id must be non-blank")
        return value

    @field_validator("observed_usage", mode="before")
    @classmethod
    def validate_observed_usage(cls, value: object) -> object:
        if not isinstance(value, dict):
            raise ValueError("observed_usage must be an object")
        for raw_name, raw_value in value.items():
            try:
                name = MetricName(raw_name)
            except (TypeError, ValueError) as error:
                raise ValueError(f"Unsupported observed usage counter: {raw_name!r}") from error
            if name not in _USAGE_METRICS:
                raise ValueError(f"{name.value} is not a token or cost usage counter")
            _validate_usage_value(name, raw_value)
        return value

    @model_validator(mode="after")
    def validate_provenance(self) -> ScenarioMetrics:
        expected = set(_REQUIRED_METRICS) | set(self.observed_usage)
        if self.task_success is not None:
            expected.add(MetricName.TASK_SUCCESS)
        if set(self.completeness) != expected:
            raise ValueError("completeness keys must exactly match the reported metrics")
        if set(self.evidence_refs) != expected:
            raise ValueError("evidence_refs keys must exactly match the reported metrics")
        for name, refs in self.evidence_refs.items():
            if not refs or any(not ref.strip() for ref in refs):
                raise ValueError(f"{name.value} evidence_refs must contain non-blank IDs")
            if len(set(refs)) != len(refs):
                raise ValueError(f"{name.value} evidence_refs must contain unique IDs")
        if any(not self.completeness[name] for name in self.observed_usage):
            raise ValueError("observed token and cost counters must be complete")
        return self

    def to_result_metrics(self) -> JsonObject:
        """Return normalized JSON suitable for ``ScenarioResult.metrics``."""

        payload = self.model_dump(
            mode="json",
            exclude={"scenario_id"},
            exclude_none=True,
        )
        return cast(JsonObject, payload)


def _validate_usage_value(name: MetricName, value: object) -> int | float:
    if name in _TOKEN_METRICS:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MetricCollectionError(
                f"Adapter usage counter {name.value!r} must be a non-negative integer"
            )
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricCollectionError(
            f"Adapter usage counter {name.value!r} must be a finite non-negative number"
        )
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise MetricCollectionError(
            f"Adapter usage counter {name.value!r} must be a finite non-negative number"
        )
    return value


class ScenarioMetricCollector:
    """Collect deterministic facts without estimating unavailable usage."""

    def collect(self, bundle: ScenarioEvidenceBundle) -> ScenarioMetrics:
        """Collect all MVP metrics from one evidence-linked execution."""

        execution = bundle.execution
        if not execution.attempts:
            raise MetricCollectionError("Scenario execution has no attempt records")
        known_evidence = frozenset(item.id for item in bundle.evidence)
        if len(known_evidence) != len(bundle.evidence):
            raise MetricCollectionError("Evidence bundle contains duplicate evidence IDs")
        for role, refs in bundle.refs_by_role.items():
            if not refs:
                raise MetricCollectionError(f"Evidence role {role!r} has no references")
            unknown = sorted(set(refs) - known_evidence)
            if unknown:
                raise MetricCollectionError(
                    f"Evidence role {role!r} contains unknown references: {', '.join(unknown)}"
                )

        execution_refs = self._required_refs(bundle, "execution")
        trace_refs = self._required_refs(bundle, "trace")
        exception_refs = tuple(bundle.refs_by_role.get("exception", ()))
        tool_call_refs = tuple(bundle.refs_by_role.get("tool_call", ()))

        steps = sum(len(attempt.trace) for attempt in execution.attempts)
        tool_calls = 0
        for attempt in execution.attempts:
            for event in attempt.trace:
                if event.get("type") != "tool_call":
                    continue
                tool = event.get("tool")
                if not isinstance(tool, str) or not tool.strip():
                    raise MetricCollectionError(
                        "Observed tool_call event has no non-blank tool name"
                    )
                tool_calls += 1

        incomplete_trace = any(
            attempt.error is not None and not attempt.trace and not attempt.usage
            for attempt in execution.attempts
        )
        recovery_steps = self._recovery_steps(bundle)
        exceptions = sum(attempt.error is not None for attempt in execution.attempts)
        observed_usage = self._observed_usage(bundle)

        trace_evidence = _unique_refs((*trace_refs, *tool_call_refs))
        exception_evidence = _unique_refs((*execution_refs, *exception_refs))
        recovery_evidence = _unique_refs((*trace_evidence, *exception_refs))
        evidence_refs: dict[MetricName, list[str]] = {
            MetricName.LATENCY_MS: list(execution_refs),
            MetricName.TOOL_CALLS: list(trace_evidence),
            MetricName.RETRIES: list(execution_refs),
            MetricName.RECOVERY_STEPS: list(recovery_evidence),
            MetricName.STEPS: list(trace_evidence),
            MetricName.EXCEPTIONS: list(exception_evidence),
        }
        completeness: dict[MetricName, bool] = {
            MetricName.LATENCY_MS: True,
            MetricName.TOOL_CALLS: not incomplete_trace,
            MetricName.RETRIES: True,
            MetricName.RECOVERY_STEPS: not incomplete_trace,
            MetricName.STEPS: not incomplete_trace,
            MetricName.EXCEPTIONS: True,
        }

        task_success: bool | None = None
        if execution.result.status is not ScenarioResultStatus.SKIPPED:
            task_success = execution.result.status is ScenarioResultStatus.PASSED
            evidence_refs[MetricName.TASK_SUCCESS] = list(execution_refs)
            completeness[MetricName.TASK_SUCCESS] = True

        if observed_usage:
            usage_refs = self._required_refs(bundle, "usage")
            for name in observed_usage:
                evidence_refs[name] = list(usage_refs)
                completeness[name] = True

        return ScenarioMetrics(
            scenario_id=execution.result.scenario_id,
            task_success=task_success,
            latency_ms=execution.result.duration_ms,
            tool_calls=tool_calls,
            retries=len(execution.attempts) - 1,
            recovery_steps=recovery_steps,
            steps=steps,
            exceptions=exceptions,
            observed_usage=observed_usage,
            completeness=completeness,
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _required_refs(bundle: ScenarioEvidenceBundle, role: str) -> tuple[str, ...]:
        refs = bundle.refs_by_role.get(role, ())
        if not refs:
            raise MetricCollectionError(f"Metric collection requires {role!r} evidence")
        return refs

    @staticmethod
    def _recovery_steps(bundle: ScenarioEvidenceBundle) -> int:
        triggered = False
        recovery_steps = 0
        for attempt in bundle.execution.attempts:
            for event in attempt.trace:
                if triggered:
                    recovery_steps += 1
                    continue
                if event.get("type") == "tool_call" and event.get("outcome") in {
                    "error",
                    "denied",
                }:
                    triggered = True
            if attempt.error is not None and not triggered:
                triggered = True
        return recovery_steps

    @staticmethod
    def _observed_usage(bundle: ScenarioEvidenceBundle) -> dict[MetricName, int | float]:
        attempts = bundle.execution.attempts
        for attempt in attempts:
            for name in _USAGE_METRICS:
                if name.value in attempt.usage:
                    _validate_usage_value(name, attempt.usage[name.value])

        observed: dict[MetricName, int | float] = {}
        for name in sorted(_USAGE_METRICS, key=lambda item: item.value):
            if not all(name.value in attempt.usage for attempt in attempts):
                continue
            values = [
                _validate_usage_value(name, attempt.usage[name.value]) for attempt in attempts
            ]
            if name in _TOKEN_METRICS:
                observed[name] = sum(cast(int, value) for value in values)
            else:
                observed[name] = sum(float(value) for value in values)
        return observed


def _unique_refs(refs: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(refs))
