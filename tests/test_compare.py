"""Tests for context-aware run comparison and the compare CLI."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue
from typer.testing import CliRunner

from gauntlet.cli import app
from gauntlet.config import resolve_config
from gauntlet.core.models import (
    DimensionName,
    DimensionScore,
    EvaluationRun,
    EvaluationRunStatus,
    ScenarioResult,
    ScenarioResultStatus,
    ScoreCard,
)
from gauntlet.evidence import RunArtifactStore
from gauntlet.reporting import (
    BenchmarkProvenance,
    ComparisonArtifactError,
    ComparisonInputError,
    ContextChangeKind,
    ExecutionMode,
    RegressionAssessment,
    RunComparisonService,
    RunSummary,
)
from gauntlet.scoring import ReleaseRecommendation

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def result(
    scenario_id: str,
    status: ScenarioResultStatus,
    latency_ms: int,
    *,
    cost_usd: float | None,
) -> ScenarioResult:
    observed_usage: dict[str, JsonValue] = {} if cost_usd is None else {"cost_usd": cost_usd}
    return ScenarioResult(
        scenario_id=scenario_id,
        status=status,
        started_at=NOW,
        finished_at=NOW,
        duration_ms=latency_ms,
        output={"completed": status is ScenarioResultStatus.PASSED},
        error=None if status is ScenarioResultStatus.PASSED else {"type": status.value},
        metrics={
            "task_success": status is ScenarioResultStatus.PASSED,
            "latency_ms": latency_ms,
            "tool_calls": 1,
            "retries": 0,
            "steps": 1,
            "observed_usage": observed_usage,
        },
        evidence_refs=[f"evidence-{scenario_id}"],
        findings=[],
    )


def write_run(
    store: RunArtifactStore,
    *,
    score: float,
    results: tuple[ScenarioResult, ...],
    config_fingerprint: str = "sha256:config-a",
    benchmark_version: str = "0.4.0",
    environment_fingerprint: str = "env-a",
    execution_mode: ExecutionMode = ExecutionMode.DETERMINISTIC_FIXTURE,
) -> str:
    config = resolve_config(
        project_config={
            "project": {"name": "compare-agent"},
            "adapter": {"type": "python_callable", "target": "agent:run"},
            "evaluation": {"benchmark_packs": ["gauntlet.agent.compare"]},
            "scoring": {"policy": "compare-policy"},
            "artifacts": {"root": str(store.root)},
        },
        environ={},
    )
    manifest = store.create_run(
        project_id="compare-agent",
        profile_id="default",
        benchmark_pack_ids=["gauntlet.agent.compare"],
        environment_fingerprint=environment_fingerprint,
        environment={"platform": "test"},
        resolved_config=config,
        seed=42,
    )
    store.write_results(manifest.id, results)
    store.write_scorecard(
        manifest.id,
        ScoreCard(
            overall=score,
            dimensions={
                DimensionName.CORRECTNESS: DimensionScore(score=score, confidence=1),
            },
            confidence=1,
            policy_id="compare-policy",
        ),
    )
    store.write_findings(manifest.id, [])
    store.write_report(manifest.id, "# Synthetic comparison fixture")
    recommendation = ReleaseRecommendation.READY if score >= 80 else ReleaseRecommendation.NOT_READY
    summary = RunSummary(
        scenarios_completed=len(results),
        benchmark_packs=[
            BenchmarkProvenance(
                id="gauntlet.agent.compare",
                version=benchmark_version,
                schema_version=1,
            )
        ],
        config_fingerprint=config_fingerprint,
        execution_mode=execution_mode,
        isolation_level="subprocess",
        release_recommendation=recommendation,
        applied_policy_rules=[f"recommendation.{recommendation.value}"],
    )
    data = manifest.model_dump(mode="python")
    data.update(
        {
            "status": EvaluationRunStatus.COMPLETED,
            "finished_at": NOW,
            "summary": summary.model_dump(mode="json"),
        }
    )
    completed = EvaluationRun.model_validate(data)
    store.write_manifest(completed)
    return completed.id


def baseline_results() -> tuple[ScenarioResult, ...]:
    return (
        result("scenario-a", ScenarioResultStatus.PASSED, 100, cost_usd=0.1),
        result("scenario-b", ScenarioResultStatus.PASSED, 200, cost_usd=0.2),
    )


def degraded_results(*, include_cost: bool = True) -> tuple[ScenarioResult, ...]:
    return (
        result(
            "scenario-a",
            ScenarioResultStatus.FAILED,
            150,
            cost_usd=0.15 if include_cost else None,
        ),
        result("scenario-b", ScenarioResultStatus.PASSED, 250, cost_usd=0.25),
    )


def slower_results() -> tuple[ScenarioResult, ...]:
    return (
        result("scenario-a", ScenarioResultStatus.PASSED, 150, cost_usd=0.15),
        result("scenario-b", ScenarioResultStatus.PASSED, 250, cost_usd=0.25),
    )


def test_comparable_degraded_run_is_flagged_as_regression(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path / "artifacts")
    run_a = write_run(store, score=90, results=baseline_results())
    run_b = write_run(store, score=70, results=degraded_results())

    comparison = RunComparisonService(store).compare(run_a, run_b)

    assert comparison.assessment is RegressionAssessment.REGRESSION
    assert comparison.context_changes == []
    assert comparison.overall_score.absolute == -20
    assert comparison.overall_score.percent == pytest.approx(-22.222222)
    assert comparison.new_failures == ["scenario-a"]
    assert comparison.fixed_failures == []
    assert comparison.latency_ms.absolute == 50
    assert comparison.cost_usd is not None
    assert comparison.cost_usd.absolute == pytest.approx(0.1)

    reverse = RunComparisonService(store).compare(run_b, run_a)
    assert reverse.assessment is RegressionAssessment.NO_REGRESSION
    assert reverse.fixed_failures == ["scenario-a"]
    assert reverse.new_failures == []


def test_configuration_change_is_distinguished_from_regression(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path / "artifacts")
    run_a = write_run(store, score=90, results=baseline_results())
    run_b = write_run(
        store,
        score=70,
        results=degraded_results(),
        config_fingerprint="sha256:config-b",
    )

    comparison = RunComparisonService(store).compare(run_a, run_b)

    assert comparison.assessment is RegressionAssessment.NOT_COMPARABLE
    assert [change.kind for change in comparison.context_changes] == [
        ContextChangeKind.CONFIGURATION
    ]
    assert comparison.overall_score.absolute == -20
    assert comparison.new_failures == ["scenario-a"]


def test_latency_and_cost_changes_are_reported_without_inventing_thresholds(
    tmp_path: Path,
) -> None:
    store = RunArtifactStore(tmp_path / "artifacts")
    run_a = write_run(store, score=90, results=baseline_results())
    run_b = write_run(store, score=90, results=slower_results())

    comparison = RunComparisonService(store).compare(run_a, run_b)

    assert comparison.assessment is RegressionAssessment.NO_REGRESSION
    assert comparison.latency_ms.absolute == 50
    assert comparison.cost_usd is not None
    assert comparison.cost_usd.absolute == pytest.approx(0.1)
    assert "No new failures" in comparison.reasons[0]


def test_benchmark_and_environment_changes_are_reported_independently(
    tmp_path: Path,
) -> None:
    store = RunArtifactStore(tmp_path / "artifacts")
    run_a = write_run(store, score=90, results=baseline_results())
    run_b = write_run(
        store,
        score=90,
        results=baseline_results(),
        benchmark_version="0.5.0",
        environment_fingerprint="env-b",
    )

    comparison = RunComparisonService(store).compare(run_a, run_b)

    assert comparison.assessment is RegressionAssessment.NOT_COMPARABLE
    assert [change.kind for change in comparison.context_changes] == [
        ContextChangeKind.BENCHMARK,
        ContextChangeKind.ENVIRONMENT,
    ]


def test_live_service_and_missing_cost_never_gain_fabricated_conclusions(
    tmp_path: Path,
) -> None:
    store = RunArtifactStore(tmp_path / "artifacts")
    run_a = write_run(
        store,
        score=90,
        results=baseline_results(),
        execution_mode=ExecutionMode.LIVE_SERVICE,
    )
    run_b = write_run(
        store,
        score=70,
        results=degraded_results(include_cost=False),
        execution_mode=ExecutionMode.LIVE_SERVICE,
    )

    comparison = RunComparisonService(store).compare(run_a, run_b)

    assert comparison.assessment is RegressionAssessment.INSUFFICIENT_DATA
    assert comparison.cost_usd is None
    assert "repeat distributions" in comparison.reasons[0]


def test_zero_score_baseline_has_no_percentage_infinity(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path / "artifacts")
    run_a = write_run(store, score=0, results=degraded_results())
    run_b = write_run(store, score=10, results=degraded_results())

    comparison = RunComparisonService(store).compare(run_a, run_b)

    assert comparison.overall_score.percent is None
    assert comparison.assessment is RegressionAssessment.NO_REGRESSION


def test_same_pending_and_missing_artifacts_are_rejected(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path / "artifacts")
    completed = write_run(store, score=90, results=baseline_results())

    with pytest.raises(ComparisonInputError, match="different runs"):
        RunComparisonService(store).compare(completed, completed)

    config = resolve_config(
        project_config={
            "project": {"name": "pending"},
            "adapter": {"type": "python_callable", "target": "agent:run"},
            "artifacts": {"root": str(store.root)},
        },
        environ={},
    )
    pending = store.create_run(
        project_id="pending",
        profile_id="default",
        benchmark_pack_ids=["gauntlet.agent.compare"],
        environment_fingerprint="env-a",
        environment={},
        resolved_config=config,
    )
    with pytest.raises(ComparisonArtifactError, match="not a completed"):
        RunComparisonService(store).compare(completed, pending.id)

    missing_scorecard = write_run(store, score=80, results=baseline_results())
    (store.run_dir(missing_scorecard) / "scorecard.json").unlink()
    with pytest.raises(ComparisonArtifactError, match="scorecard.json"):
        RunComparisonService(store).compare(completed, missing_scorecard)

    duplicate_json = write_run(store, score=80, results=baseline_results())
    (store.run_dir(duplicate_json) / "scorecard.json").write_text(
        '{"overall":80,"overall":70}\n',
        encoding="utf-8",
    )
    with pytest.raises(ComparisonArtifactError, match="Duplicate JSON key"):
        RunComparisonService(store).compare(completed, duplicate_json)


def test_compare_cli_uses_regression_context_and_input_exit_codes(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path / "artifacts")
    baseline = write_run(store, score=90, results=baseline_results())
    regression = write_run(store, score=70, results=degraded_results())
    config_change = write_run(
        store,
        score=70,
        results=degraded_results(),
        config_fingerprint="sha256:config-b",
    )
    runner = CliRunner()

    regressed = runner.invoke(
        app,
        ["compare", baseline, regression, "--artifact-root", str(store.root)],
    )
    changed = runner.invoke(
        app,
        ["compare", baseline, config_change, "--artifact-root", str(store.root)],
    )
    same = runner.invoke(
        app,
        ["compare", baseline, baseline, "--artifact-root", str(store.root)],
    )

    assert regressed.exit_code == 1
    assert "Assessment: regression" in regressed.stdout
    assert "New failures: scenario-a" in regressed.stdout
    assert changed.exit_code == 0
    assert "Assessment: not_comparable" in changed.stdout
    assert "configuration:" in changed.stdout
    assert same.exit_code == 2
    assert "must identify different runs" in same.stderr
