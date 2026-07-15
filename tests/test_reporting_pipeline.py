"""End-to-end tests for scored report publication and persisted redaction."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gauntlet.benchmarks import BenchmarkPackIdentity, LoadedBenchmarkPack
from gauntlet.config import resolve_config
from gauntlet.config.models import GauntletConfig, ProjectConfig
from gauntlet.core.models import BenchmarkPackManifest, DimensionName, Scenario
from gauntlet.evidence import ArtifactStoreError, RunArtifactStore
from gauntlet.reporting import (
    EvaluationExitCode,
    EvaluationPipeline,
    EvaluationRequest,
    IncompleteEvaluationError,
    exit_code_for_recommendation,
)
from gauntlet.scoring import (
    PolicyCaps,
    PolicyMinimums,
    ReleaseRecommendation,
    ScoringPolicy,
    agent_mvp_default_policy,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = REPOSITORY_ROOT / "examples"


def scenario(scenario_id: str, category: DimensionName, *, value: str = "alpha") -> Scenario:
    return Scenario(
        id=scenario_id,
        title=scenario_id,
        description="Golden report pipeline scenario",
        category=category.value,
        difficulty=1,
        tags=["m4"],
        required_capabilities=["invoke", "trace_tool_calls"],
        input={"key": "case-1"},
        fixtures={
            "tool_sequence": [
                {
                    "tool": "lookup",
                    "arguments": {"key": "case-1"},
                    "response": {"value": value},
                },
                {
                    "tool": "save",
                    "arguments": {"value": value},
                    "response": {"ok": True},
                },
            ]
        },
        execution_policy={"timeout_seconds": 5},
        assertions=[{"type": "no_hallucinated_success"}],
        metrics=["task_success"],
    )


def loaded_pack(root: Path, scenarios: tuple[Scenario, ...]) -> LoadedBenchmarkPack:
    identity = BenchmarkPackIdentity(
        id="gauntlet.agent.m4-test",
        version="0.4.0",
        schema_version=1,
    )
    manifest = BenchmarkPackManifest(
        id=identity.id,
        version=identity.version,
        title="M4 golden test",
        description="Deterministic M4 pipeline fixture",
        schema_version=identity.schema_version,
        required_capabilities=["invoke", "trace_tool_calls"],
        dimensions=list(DimensionName)[:6],
        scenarios=[f"scenarios/{item.id}.yaml" for item in scenarios],
        scoring_policy="scoring.yaml",
    )
    return LoadedBenchmarkPack(
        root=root,
        manifest_path=root / "manifest.yaml",
        manifest=manifest,
        identity=identity,
        scenarios=scenarios,
        scenario_paths=tuple(root / f"scenarios/{item.id}.yaml" for item in scenarios),
        scoring_policy_path=root / "scoring.yaml",
    )


def config(
    artifact_root: Path,
    target: str,
    policy_id: str = "agent_mvp_default",
) -> GauntletConfig:
    return resolve_config(
        project_config={
            "project": {"name": "sample-agent"},
            "adapter": {"type": "python_callable", "target": target},
            "evaluation": {
                "benchmark_packs": ["gauntlet.agent.m4-test"],
                "seed": 42,
                "repeat": 1,
                "timeout_seconds": 5,
            },
            "scoring": {"policy": policy_id},
            "artifacts": {"root": str(artifact_root)},
        },
        environ={},
    )


def request(
    artifact_root: Path,
    pack: LoadedBenchmarkPack,
    target: str,
    policy: ScoringPolicy,
    *,
    project_root: Path = EXAMPLES_ROOT,
    environment_fingerprint: str = "env-m4",
    environment: dict[str, str] | None = None,
) -> EvaluationRequest:
    return EvaluationRequest(
        project_id="sample-agent",
        profile_id="default",
        benchmark=pack,
        resolved_config=config(artifact_root, target, policy.id),
        project_root=project_root,
        environment_fingerprint=environment_fingerprint,
        environment=environment or {"python": "test"},
        policy=policy,
    )


def test_correct_golden_agent_outscores_degraded_and_writes_all_reports(
    tmp_path: Path,
) -> None:
    categories = (
        DimensionName.CORRECTNESS,
        DimensionName.RELIABILITY,
        DimensionName.SECURITY,
        DimensionName.PERFORMANCE,
        DimensionName.EFFICIENCY,
    ) * 2
    pack = loaded_pack(
        tmp_path / "pack",
        tuple(scenario(f"golden-{index}", category) for index, category in enumerate(categories)),
    )
    artifact_root = tmp_path / "artifacts"
    pipeline = EvaluationPipeline(RunArtifactStore(artifact_root), redaction_environment={})
    policy = agent_mvp_default_policy()

    correct = pipeline.evaluate(
        request(
            artifact_root,
            pack,
            "sample_agent.variants.correct:run",
            policy,
        )
    )
    degraded = pipeline.evaluate(
        request(
            artifact_root,
            pack,
            "sample_agent.variants.hallucinating:run",
            policy,
        )
    )

    assert correct.scoring.scorecard.overall > degraded.scoring.scorecard.overall
    assert correct.scoring.scorecard.overall == 100
    assert degraded.scoring.scorecard.overall == 0
    assert all(result.status.value == "passed" for result in correct.results)
    assert all(result.status.value == "failed" for result in degraded.results)
    for completed in (correct, degraded):
        assert completed.run.status.value == "completed"
        assert completed.run.finished_at is not None
        assert completed.run.summary["benchmark_packs"] == [
            {"id": "gauntlet.agent.m4-test", "version": "0.4.0", "schema_version": 1}
        ]
        assert completed.artifacts.results.is_file()
        assert completed.artifacts.scorecard.is_file()
        assert completed.artifacts.findings.is_file()
        assert completed.artifacts.markdown.is_file()
        report = completed.artifacts.markdown.read_text(encoding="utf-8")
        assert "not reported" in report
        assert "Raw outputs, fixtures, hidden expected values" in report
        assert '"value": "alpha"' not in report


def test_every_persisted_artifact_is_redacted_before_publication(tmp_path: Path) -> None:
    secret = "never-persist-m4-secret"
    artifact_root = tmp_path / "artifacts"
    pack = loaded_pack(
        tmp_path / "pack",
        (scenario("secret-scenario", DimensionName.CORRECTNESS, value=secret),),
    )
    policy = ScoringPolicy(
        id="correctness-only",
        weights={DimensionName.CORRECTNESS: 1.0},
        caps=PolicyCaps(
            critical_security_finding=49,
            task_success_below_50_percent=59,
        ),
        minimums=PolicyMinimums(scenarios_completed=1),
    )
    pipeline = EvaluationPipeline(
        RunArtifactStore(artifact_root),
        redaction_environment={"SERVICE_TOKEN": secret},
    )
    evaluation_request = request(
        artifact_root,
        pack,
        "sample_agent.variants.correct:run",
        policy,
        environment_fingerprint=f"fingerprint-{secret}",
        environment={"diagnostic": secret},
    )
    evaluation_request = replace(
        evaluation_request,
        project_id=secret,
        profile_id=secret,
        resolved_config=evaluation_request.resolved_config.model_copy(
            update={"project": ProjectConfig(name=secret)}
        ),
    )

    completed = pipeline.evaluate(evaluation_request)

    run_dir = pipeline.run_store.run_dir(completed.run.id)
    persisted = [path for path in run_dir.rglob("*") if path.is_file()]
    assert persisted
    assert all(secret not in path.read_text(encoding="utf-8") for path in persisted)
    assert "[REDACTED]" in (run_dir / "results.json").read_text(encoding="utf-8")
    assert "[REDACTED]" in (run_dir / "config.resolved.yaml").read_text(encoding="utf-8")
    assert "[REDACTED]" in (run_dir / "environment.json").read_text(encoding="utf-8")


class FailingReportStore(RunArtifactStore):
    def write_report(self, run_id: str, markdown: str) -> Path:
        del run_id, markdown
        raise ArtifactStoreError("synthetic report failure")


def test_manifest_is_not_completed_when_report_publication_fails(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    store = FailingReportStore(artifact_root)
    pack = loaded_pack(
        tmp_path / "pack",
        (scenario("report-failure", DimensionName.CORRECTNESS),),
    )
    policy = ScoringPolicy(
        id="correctness-only",
        weights={DimensionName.CORRECTNESS: 1.0},
        caps=PolicyCaps(
            critical_security_finding=49,
            task_success_below_50_percent=59,
        ),
        minimums=PolicyMinimums(scenarios_completed=1),
    )

    with pytest.raises(IncompleteEvaluationError, match="synthetic report failure") as caught:
        EvaluationPipeline(store, redaction_environment={}).evaluate(
            request(
                artifact_root,
                pack,
                "sample_agent.variants.correct:run",
                policy,
            )
        )

    assert caught.value.exit_code is EvaluationExitCode.INCOMPLETE_EVALUATION

    scan = store.scan()
    assert len(scan.runs) == 1
    assert scan.runs[0].status.value == "failed"
    assert not (store.run_dir(scan.runs[0].id) / "report.md").exists()


@pytest.mark.parametrize(
    ("recommendation", "expected"),
    [
        (ReleaseRecommendation.READY, EvaluationExitCode.PASSED),
        (ReleaseRecommendation.READY_WITH_WARNINGS, EvaluationExitCode.PASSED),
        (ReleaseRecommendation.NOT_READY, EvaluationExitCode.POLICY_FAILED),
        (
            ReleaseRecommendation.EVALUATION_INCONCLUSIVE,
            EvaluationExitCode.INCOMPLETE_EVALUATION,
        ),
    ],
)
def test_completed_recommendations_map_to_documented_exit_codes(
    recommendation: ReleaseRecommendation,
    expected: EvaluationExitCode,
) -> None:
    assert exit_code_for_recommendation(recommendation) is expected


def test_all_documented_exit_codes_are_stable() -> None:
    assert [item.value for item in EvaluationExitCode] == [0, 1, 2, 3, 4, 5]
