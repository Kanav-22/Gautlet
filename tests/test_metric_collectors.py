"""Tests for evidence-linked, non-fabricating metric collection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gauntlet.adapters import JsonObject
from gauntlet.config.models import NetworkPolicy
from gauntlet.core.models import Evidence, EvidenceType, ScenarioResult, ScenarioResultStatus
from gauntlet.evidence import ScenarioEvidenceBundle
from gauntlet.execution import AttemptRecord, ScenarioExecution, ScenarioLifecycleState
from gauntlet.metrics import MetricCollectionError, MetricName, ScenarioMetricCollector

NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=UTC)


def trace_event(tool: str, outcome: str = "returned") -> JsonObject:
    return {
        "type": "tool_call",
        "tool": tool,
        "outcome": outcome,
        "arguments": {},
    }


def attempt(
    number: int,
    *,
    trace: tuple[JsonObject, ...] = (),
    usage: JsonObject | None = None,
    error: JsonObject | None = None,
) -> AttemptRecord:
    return AttemptRecord(
        attempt_number=number,
        started_at=NOW,
        finished_at=NOW,
        duration_ms=10,
        output={"completed": error is None},
        error=error,
        trace=trace,
        usage=usage or {},
        stderr="",
        isolation_level="subprocess",
        timed_out=error is not None and error.get("type") == "timeout",
        retryable=error is not None,
    )


def make_bundle(
    attempts: tuple[AttemptRecord, ...],
    *,
    status: ScenarioResultStatus = ScenarioResultStatus.PASSED,
    duration_ms: int = 25,
    unknown_ref: bool = False,
) -> ScenarioEvidenceBundle:
    terminal = {
        ScenarioResultStatus.PASSED: ScenarioLifecycleState.PASSED,
        ScenarioResultStatus.FAILED: ScenarioLifecycleState.FAILED,
        ScenarioResultStatus.ERROR: ScenarioLifecycleState.ERROR,
        ScenarioResultStatus.TIMED_OUT: ScenarioLifecycleState.TIMED_OUT,
        ScenarioResultStatus.SKIPPED: ScenarioLifecycleState.FINALIZED,
    }[status]
    roles: dict[str, tuple[str, ...]] = {
        "execution": ("evidence-execution",),
        "trace": ("evidence-trace",),
        "tool_call": ("evidence-tool",),
        "exception": ("evidence-exception",),
        "usage": ("evidence-usage",),
    }
    evidence = tuple(
        Evidence(
            id=ref,
            type=EvidenceType.METRIC,
            path=f"evidence/{ref}.json",
            content_hash=f"sha256:{ref}",
            redacted=False,
            metadata={},
        )
        for ref in (
            "evidence-execution",
            "evidence-trace",
            "evidence-tool",
            "evidence-exception",
            "evidence-usage",
        )
    )
    if unknown_ref:
        roles["trace"] = ("evidence-unknown",)
    result = ScenarioResult(
        scenario_id="metric.scenario",
        status=status,
        started_at=NOW,
        finished_at=NOW,
        duration_ms=duration_ms,
        output={"completed": status is ScenarioResultStatus.PASSED},
        error=None if status is ScenarioResultStatus.PASSED else {"type": status.value},
        metrics={},
        evidence_refs=[item.id for item in evidence],
        findings=[],
    )
    lifecycle = (
        ScenarioLifecycleState.LOADED,
        ScenarioLifecycleState.VALIDATED,
        ScenarioLifecycleState.PREPARED,
        ScenarioLifecycleState.RUNNING,
        terminal,
        ScenarioLifecycleState.FINALIZED,
    )
    return ScenarioEvidenceBundle(
        execution=ScenarioExecution(
            result=result,
            lifecycle=lifecycle,
            attempts=attempts,
            seed=42,
            network_policy=NetworkPolicy.DISABLED,
        ),
        evidence=evidence,
        refs_by_role=roles,
    )


def test_collects_all_attempts_and_exact_observed_usage() -> None:
    bundle = make_bundle(
        (
            attempt(
                1,
                trace=(trace_event("lookup", "error"),),
                usage={"input_tokens": 3, "output_tokens": 2, "cost_usd": 0.01},
                error={"type": "adapter_child_error"},
            ),
            attempt(
                2,
                trace=(trace_event("lookup"), trace_event("save")),
                usage={"input_tokens": 5, "output_tokens": 4, "cost_usd": 0.02},
            ),
        ),
        duration_ms=91,
    )

    metrics = ScenarioMetricCollector().collect(bundle)

    assert metrics.task_success is True
    assert metrics.latency_ms == 91
    assert metrics.tool_calls == 3
    assert metrics.retries == 1
    assert metrics.recovery_steps == 2
    assert metrics.steps == 3
    assert metrics.exceptions == 1
    assert metrics.observed_usage == {
        MetricName.COST_USD: 0.03,
        MetricName.INPUT_TOKENS: 8,
        MetricName.OUTPUT_TOKENS: 6,
    }
    assert MetricName.TOTAL_TOKENS not in metrics.observed_usage
    assert all(metrics.completeness.values())
    known = {item.id for item in bundle.evidence}
    assert all(set(refs) <= known for refs in metrics.evidence_refs.values())


def test_assertion_demoted_failure_is_not_task_success() -> None:
    metrics = ScenarioMetricCollector().collect(
        make_bundle((attempt(1),), status=ScenarioResultStatus.FAILED)
    )

    assert metrics.task_success is False


def test_skipped_scenario_omits_task_success() -> None:
    metrics = ScenarioMetricCollector().collect(
        make_bundle((attempt(1),), status=ScenarioResultStatus.SKIPPED)
    )

    assert metrics.task_success is None
    assert MetricName.TASK_SUCCESS not in metrics.evidence_refs
    assert "task_success" not in metrics.to_result_metrics()


def test_builtin_usage_never_fabricates_tokens_or_cost() -> None:
    metrics = ScenarioMetricCollector().collect(
        make_bundle((attempt(1, usage={"invocations": 1, "tool_calls": 0}),))
    )

    assert metrics.observed_usage == {}
    serialized = metrics.to_result_metrics()
    assert serialized["observed_usage"] == {}
    assert "input_tokens" not in serialized
    assert "cost_usd" not in serialized


def test_partial_usage_counter_is_omitted_instead_of_underreported() -> None:
    metrics = ScenarioMetricCollector().collect(
        make_bundle(
            (
                attempt(1, usage={"input_tokens": 7}),
                attempt(2, usage={"invocations": 1}),
            )
        )
    )

    assert MetricName.INPUT_TOKENS not in metrics.observed_usage


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("input_tokens", True),
        ("input_tokens", 1.5),
        ("output_tokens", -1),
        ("cost_usd", float("nan")),
        ("cost_usd", float("inf")),
        ("cost_usd", -0.1),
    ],
)
def test_invalid_reported_usage_fails_closed(name: str, value: object) -> None:
    with pytest.raises(MetricCollectionError, match="usage counter"):
        ScenarioMetricCollector().collect(
            make_bundle((attempt(1, usage={name: value}),))  # type: ignore[dict-item]
        )


def test_timeout_without_trace_is_incomplete_not_efficient_zero() -> None:
    metrics = ScenarioMetricCollector().collect(
        make_bundle(
            (attempt(1, error={"type": "timeout"}),),
            status=ScenarioResultStatus.TIMED_OUT,
        )
    )

    assert metrics.tool_calls == metrics.steps == metrics.recovery_steps == 0
    assert metrics.completeness[MetricName.TOOL_CALLS] is False
    assert metrics.completeness[MetricName.STEPS] is False
    assert metrics.completeness[MetricName.RECOVERY_STEPS] is False
    assert metrics.task_success is False


def test_tool_error_does_not_count_as_attempt_exception() -> None:
    metrics = ScenarioMetricCollector().collect(
        make_bundle((attempt(1, trace=(trace_event("lookup", "error"),)),))
    )

    assert metrics.exceptions == 0
    assert metrics.recovery_steps == 0


def test_unknown_evidence_reference_is_rejected() -> None:
    with pytest.raises(MetricCollectionError, match="unknown references"):
        ScenarioMetricCollector().collect(make_bundle((attempt(1),), unknown_ref=True))
