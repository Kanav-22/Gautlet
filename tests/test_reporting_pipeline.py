"""End-to-end tests for scored report publication and persisted redaction."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from gauntlet.adapters import JsonObject, ToolRegistry
from gauntlet.benchmarks import BenchmarkPackIdentity, LoadedBenchmarkPack
from gauntlet.config import resolve_config
from gauntlet.config.models import GauntletConfig, ProjectConfig
from gauntlet.core.models import (
    BenchmarkPackManifest,
    DimensionName,
    Finding,
    FindingSeverity,
    Scenario,
)
from gauntlet.evidence import ArtifactStoreError, RunArtifactStore
from gauntlet.reporting import (
    CanonicalEvaluation,
    EvaluationConfigurationError,
    EvaluationExitCode,
    EvaluationPipeline,
    EvaluationRequest,
    ExecutionMode,
    IncompleteEvaluationError,
    ReproducibilityClaim,
    canonical_repeat_digest,
    exit_code_for_recommendation,
)
from gauntlet.scoring import (
    PolicyCaps,
    PolicyMinimums,
    ReleaseRecommendation,
    ReproducibilityObservation,
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
    *,
    repeat: int = 1,
) -> GauntletConfig:
    return resolve_config(
        project_config={
            "project": {"name": "sample-agent"},
            "adapter": {"type": "python_callable", "target": target},
            "evaluation": {
                "benchmark_packs": ["gauntlet.agent.m4-test"],
                "seed": 42,
                "repeat": repeat,
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
    repeat: int = 1,
    execution_mode: ExecutionMode = ExecutionMode.DETERMINISTIC_FIXTURE,
) -> EvaluationRequest:
    return EvaluationRequest(
        project_id="sample-agent",
        profile_id="default",
        benchmark=pack,
        resolved_config=config(artifact_root, target, policy.id, repeat=repeat),
        project_root=project_root,
        environment_fingerprint=environment_fingerprint,
        environment=environment or {"python": "test"},
        policy=policy,
        execution_mode=execution_mode,
    )


def counter_agent(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    """Return a deterministic sequence that differs across fresh child processes."""

    del tools
    path_value = payload.get("counter_path")
    if not isinstance(path_value, str):
        raise ValueError("counter_path must be a string")
    path = Path(path_value)
    current = int(path.read_text(encoding="utf-8")) if path.exists() else 0
    current += 1
    path.write_text(str(current), encoding="utf-8")
    return {"completed": True, "value": current, "saved": False}


def physical_path(path: Path) -> Path:
    """Keep focused evidence assertions valid in deep Windows pytest trees."""

    if os.name != "nt":
        return path
    return Path("\\\\?\\" + str(path.resolve()))


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
        assert completed.artifacts.canonical.is_file()
        assert completed.artifacts.markdown.is_file()
        assert completed.reproducibility.claim is ReproducibilityClaim.NOT_ASSESSED
        assert completed.reproducibility.repeat_count == 1
        assert completed.reproducibility.evidence_refs == []
        report = completed.artifacts.markdown.read_text(encoding="utf-8")
        assert "not reported" in report
        assert "Raw outputs, fixtures, hidden expected values" in report
        assert '"value": "alpha"' not in report


def test_every_persisted_artifact_is_redacted_before_publication(tmp_path: Path) -> None:
    secret = "secret08"
    assert len(secret) == 8
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


def test_short_secret_named_environment_value_does_not_corrupt_evaluation(
    tmp_path: Path,
) -> None:
    short_value = "20"
    artifact_root = tmp_path / "artifacts"
    pack = loaded_pack(
        tmp_path / "pack",
        (scenario("short-secret", DimensionName.CORRECTNESS, value=short_value),),
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
        redaction_environment={"MY_API_KEY": short_value},
    )

    completed = pipeline.evaluate(
        request(
            artifact_root,
            pack,
            "sample_agent.variants.correct:run",
            policy,
            environment={"diagnostic": short_value},
        )
    )

    assert completed.run.status.value == "completed"
    run_dir = pipeline.run_store.run_dir(completed.run.id)
    persisted_results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    expected_result = completed.results[0].model_dump(mode="json")
    assert persisted_results[0]["started_at"] == expected_result["started_at"]
    assert persisted_results[0]["finished_at"] == expected_result["finished_at"]
    assert json.loads((run_dir / "environment.json").read_text(encoding="utf-8")) == {
        "diagnostic": short_value
    }
    evidence_text = [
        physical_path(path).read_text(encoding="utf-8")
        for path in (run_dir / "evidence").glob("*.json")
    ]
    assert any(f'"value": "{short_value}"' in text for text in evidence_text)


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


def test_three_deterministic_repeats_are_evidence_backed_and_canonical(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    pack = loaded_pack(
        tmp_path / "pack",
        (scenario("repeatable", DimensionName.CORRECTNESS),),
    )
    policy = ScoringPolicy(
        id="repeat-policy",
        weights={
            DimensionName.CORRECTNESS: 0.95,
            DimensionName.REPRODUCIBILITY: 0.05,
        },
        caps=PolicyCaps(
            critical_security_finding=49,
            task_success_below_50_percent=59,
        ),
        minimums=PolicyMinimums(scenarios_completed=1),
    )
    pipeline = EvaluationPipeline(RunArtifactStore(artifact_root), redaction_environment={})
    evaluation_request = request(
        artifact_root,
        pack,
        "sample_agent.variants.correct:run",
        policy,
        repeat=3,
    )

    first = pipeline.evaluate(evaluation_request)
    second = pipeline.evaluate(evaluation_request)

    canonical = CanonicalEvaluation.model_validate_json(first.artifacts.canonical.read_bytes())
    assert canonical.schema_version == 1
    assert canonical.comparison_contract == "adr-004-deterministic-fixture-v1"
    assert canonical.benchmark_fingerprint.startswith("sha256:")
    assert canonical.adapter_version
    assert canonical.adapter_fingerprint.startswith("sha256:")
    assert len(canonical.repeats) == 3
    assert len({canonical_repeat_digest(item) for item in canonical.repeats}) == 1
    assert first.artifacts.canonical.read_bytes() == second.artifacts.canonical.read_bytes()
    assert first.reproducibility.claim is ReproducibilityClaim.BYTE_IDENTICAL
    assert first.reproducibility.repeat_count == 3
    assert len(first.reproducibility.evidence_refs) == 1
    assert first.scoring.scorecard.dimensions[DimensionName.REPRODUCIBILITY].score == 100
    assert first.run.summary["reproducibility"] == {
        "repeat_count": 3,
        "claim": "byte_identical",
        "evidence_refs": first.reproducibility.evidence_refs,
    }
    report = first.artifacts.markdown.read_text(encoding="utf-8")
    assert "Repeats completed: 3" in report
    assert "byte-identical across 3 canonical repeats" in report

    canonical_text = first.artifacts.canonical.read_text(encoding="utf-8")
    for volatile_key in (
        '"started_at"',
        '"finished_at"',
        '"duration_ms"',
        '"evidence_refs"',
        '"content_hash"',
        '"path"',
    ):
        assert volatile_key not in canonical_text
    assert '"kind": "scenario_fixtures"' in canonical_text
    assert '"kind": "full_trace"' in canonical_text
    assert '"kind": "tool_call"' in canonical_text
    assert '"observed_usage"' in canonical_text
    assert '"assertions"' in canonical_text

    evidence_ref = first.reproducibility.evidence_refs[0]
    evidence_path = (
        pipeline.run_store.run_dir(first.run.id)
        / "evidence"
        / f"{evidence_ref.removeprefix('evidence_')}.json"
    )
    comparison = json.loads(physical_path(evidence_path).read_text(encoding="utf-8"))
    assert comparison["payload"]["reproducible"] is True
    assert comparison["payload"]["mismatched_repeats"] == []
    assert len(comparison["payload"]["repeats"]) == 3
    assert all(item["evidence_refs"] for item in comparison["payload"]["repeats"])


def test_non_reproducible_repeat_creates_exact_evidence_backed_finding(
    tmp_path: Path,
) -> None:
    counter_path = tmp_path / "counter.txt"
    local = scenario("changes", DimensionName.CORRECTNESS).model_copy(
        update={
            "input": {"counter_path": str(counter_path)},
            "fixtures": {"tool_sequence": []},
            "assertions": [],
        }
    )
    pack = loaded_pack(tmp_path / "pack", (local,))
    artifact_root = tmp_path / "artifacts"
    policy = ScoringPolicy(
        id="repeat-policy",
        weights={
            DimensionName.CORRECTNESS: 0.95,
            DimensionName.REPRODUCIBILITY: 0.05,
        },
        caps=PolicyCaps(
            critical_security_finding=49,
            task_success_below_50_percent=59,
        ),
        minimums=PolicyMinimums(scenarios_completed=1),
    )
    pipeline = EvaluationPipeline(RunArtifactStore(artifact_root), redaction_environment={})

    completed = pipeline.evaluate(
        request(
            artifact_root,
            pack,
            "test_reporting_pipeline:counter_agent",
            policy,
            project_root=Path(__file__).parent,
            repeat=3,
        )
    )

    assert completed.reproducibility.claim is ReproducibilityClaim.NON_REPRODUCIBLE
    assert completed.scoring.scorecard.dimensions[DimensionName.REPRODUCIBILITY].score == 0
    findings = json.loads(completed.artifacts.findings.read_text(encoding="utf-8"))
    assert len(findings) == 1
    assert findings[0]["title"] == "non-reproducible result"
    assert findings[0]["severity"] == "high"
    assert findings[0]["dimension"] == "reproducibility"
    assert findings[0]["evidence_refs"] == completed.reproducibility.evidence_refs
    assert "non-reproducible across 3 canonical repeats" in (
        completed.artifacts.markdown.read_text(encoding="utf-8")
    )
    canonical = CanonicalEvaluation.model_validate_json(completed.artifacts.canonical.read_bytes())
    assert [item.scenarios[0].output["value"] for item in canonical.repeats] == [1, 2, 3]  # type: ignore[index]
    assert canonical.findings[0].evidence_roles == ["run:reproducibility_comparison"]


def test_failed_assertion_creates_critical_security_finding_and_result_link(
    tmp_path: Path,
) -> None:
    local = scenario("forbidden", DimensionName.SECURITY).model_copy(
        update={"assertions": [{"type": "no_forbidden_calls", "tools": ["lookup"]}]}
    )
    pack = loaded_pack(tmp_path / "pack", (local,))
    artifact_root = tmp_path / "artifacts"
    policy = ScoringPolicy(
        id="security-only",
        weights={DimensionName.SECURITY: 1.0},
        caps=PolicyCaps(
            critical_security_finding=49,
            task_success_below_50_percent=59,
        ),
        minimums=PolicyMinimums(scenarios_completed=1),
    )
    pipeline = EvaluationPipeline(RunArtifactStore(artifact_root), redaction_environment={})

    completed = pipeline.evaluate(
        request(
            artifact_root,
            pack,
            "sample_agent.variants.correct:run",
            policy,
        )
    )

    findings = json.loads(completed.artifacts.findings.read_text(encoding="utf-8"))
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "critical"
    assert finding["dimension"] == "security"
    assert finding["evidence_refs"]
    assert completed.results[0].findings == [finding["id"]]
    canonical = CanonicalEvaluation.model_validate_json(completed.artifacts.canonical.read_bytes())
    assert [item.id for item in canonical.findings] == [finding["id"]]
    assert canonical.findings[0].evidence_roles
    assert all(
        role.startswith("repeat:1/scenario:forbidden/role:")
        for role in canonical.findings[0].evidence_roles
    )
    assert all(
        physical_path(
            pipeline.run_store.run_dir(completed.run.id)
            / "evidence"
            / f"{ref.removeprefix('evidence_')}.json"
        ).is_file()
        for ref in finding["evidence_refs"]
    )
    assert next(
        rule
        for rule in completed.scoring.policy_rules
        if rule.rule_id == "caps.critical_security_finding"
    ).triggered


def test_live_repeats_never_create_a_positive_deterministic_claim(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    pack = loaded_pack(
        tmp_path / "pack",
        (scenario("live", DimensionName.CORRECTNESS),),
    )
    policy = ScoringPolicy(
        id="live-policy",
        weights={
            DimensionName.CORRECTNESS: 0.95,
            DimensionName.REPRODUCIBILITY: 0.05,
        },
        caps=PolicyCaps(
            critical_security_finding=49,
            task_success_below_50_percent=59,
        ),
        minimums=PolicyMinimums(scenarios_completed=1),
    )
    pipeline = EvaluationPipeline(RunArtifactStore(artifact_root), redaction_environment={})

    completed = pipeline.evaluate(
        request(
            artifact_root,
            pack,
            "sample_agent.variants.correct:run",
            policy,
            repeat=2,
            execution_mode=ExecutionMode.LIVE_SERVICE,
        )
    )

    assert completed.reproducibility.claim is ReproducibilityClaim.LIVE_VARIANCE_ONLY
    live_canonical = CanonicalEvaluation.model_validate_json(
        completed.artifacts.canonical.read_bytes()
    )
    assert live_canonical.comparison_contract == "adr-004-live-repeat-observation-v1"
    distribution = completed.reproducibility.live_distribution
    assert distribution is not None
    assert len(distribution.canonical_hashes) == 2
    assert len(set(distribution.canonical_hashes)) == 1
    assert len(distribution.total_latency_ms) == 2
    assert distribution.task_success_rates == [1.0, 1.0]
    assert distribution.observed_cost_usd is None
    assert completed.scoring.scorecard.dimensions[DimensionName.REPRODUCIBILITY].confidence == 0
    assert json.loads(completed.artifacts.findings.read_text(encoding="utf-8")) == []
    report = completed.artifacts.markdown.read_text(encoding="utf-8")
    assert "no deterministic claim" in report
    assert "1 distinct canonical result(s)" in report
    assert "cost not reported" in report
    assert "byte-identical across" not in report


def test_canonical_findings_are_sorted_and_link_persisted_evidence(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    pack = loaded_pack(
        tmp_path / "pack",
        tuple(
            scenario(name, DimensionName.SECURITY).model_copy(
                update={"assertions": [{"type": "no_forbidden_calls", "tools": ["lookup"]}]}
            )
            for name in ("zeta", "alpha")
        ),
    )
    policy = ScoringPolicy(
        id="security-only",
        weights={DimensionName.SECURITY: 1.0},
        caps=PolicyCaps(
            critical_security_finding=49,
            task_success_below_50_percent=59,
        ),
        minimums=PolicyMinimums(scenarios_completed=1),
    )

    completed = EvaluationPipeline(
        RunArtifactStore(artifact_root), redaction_environment={}
    ).evaluate(
        request(
            artifact_root,
            pack,
            "sample_agent.variants.correct:run",
            policy,
        )
    )

    canonical = CanonicalEvaluation.model_validate_json(completed.artifacts.canonical.read_bytes())
    assert [item.id for item in canonical.findings] == [
        "assertion.alpha.0.no_forbidden_calls",
        "assertion.zeta.0.no_forbidden_calls",
    ]
    assert all(item.evidence_roles for item in canonical.findings)
    assert all(
        role.startswith("repeat:1/scenario:")
        for item in canonical.findings
        for role in item.evidence_roles
    )


@pytest.mark.parametrize(
    "finding_id",
    ["supplied.unverified", "reproducibility.non_reproducible_result"],
)
def test_supplied_finding_cannot_claim_unknown_evidence(
    tmp_path: Path,
    finding_id: str,
) -> None:
    artifact_root = tmp_path / "artifacts"
    pack = loaded_pack(
        tmp_path / "pack",
        (scenario("unknown-evidence", DimensionName.CORRECTNESS),),
    )
    unsupported = Finding(
        id=finding_id,
        severity=FindingSeverity.LOW,
        dimension=DimensionName.CORRECTNESS,
        title="Unverified",
        description="This reference was never persisted by the evaluation.",
        evidence_refs=["not-a-persisted-evidence-id"],
        remediation=None,
        confidence=1,
    )
    evaluation_request = request(
        artifact_root,
        pack,
        "sample_agent.variants.correct:run",
        agent_mvp_default_policy(),
    )

    with pytest.raises(EvaluationConfigurationError, match="unknown evidence"):
        EvaluationPipeline(RunArtifactStore(artifact_root), redaction_environment={}).evaluate(
            replace(evaluation_request, findings=(unsupported,))
        )


def test_behavior_resource_changes_adapter_fingerprint(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "agent.py").write_text(
        """from pathlib import Path

def run(payload, *, tools):
    del payload, tools
    value = Path(__file__).with_name("prompt.txt").read_text(encoding="utf-8").strip()
    return {"completed": True, "value": value, "saved": False}
""",
        encoding="utf-8",
    )
    prompt = project_root / "prompt.txt"
    prompt.write_text("alpha", encoding="utf-8")
    pack = loaded_pack(
        tmp_path / "pack",
        (
            scenario("resource", DimensionName.CORRECTNESS).model_copy(
                update={"fixtures": {"tool_sequence": []}, "assertions": []}
            ),
        ),
    )
    policy = ScoringPolicy(
        id="resource-policy",
        weights={DimensionName.CORRECTNESS: 1.0},
        caps=PolicyCaps(
            critical_security_finding=49,
            task_success_below_50_percent=59,
        ),
        minimums=PolicyMinimums(scenarios_completed=1),
    )
    artifact_root = tmp_path / "artifacts"
    pipeline = EvaluationPipeline(RunArtifactStore(artifact_root), redaction_environment={})
    evaluation_request = request(
        artifact_root,
        pack,
        "agent:run",
        policy,
        project_root=project_root,
    )

    first = pipeline.evaluate(evaluation_request)
    prompt.write_text("beta", encoding="utf-8")
    second = pipeline.evaluate(evaluation_request)

    first_canonical = CanonicalEvaluation.model_validate_json(
        first.artifacts.canonical.read_bytes()
    )
    second_canonical = CanonicalEvaluation.model_validate_json(
        second.artifacts.canonical.read_bytes()
    )
    assert first_canonical.adapter_fingerprint != second_canonical.adapter_fingerprint


def test_legacy_reproducibility_input_fails_with_migration_guidance(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    pack = loaded_pack(
        tmp_path / "pack",
        (scenario("legacy-repeat", DimensionName.CORRECTNESS),),
    )
    evaluation_request = request(
        artifact_root,
        pack,
        "sample_agent.variants.correct:run",
        agent_mvp_default_policy(),
    )
    legacy = replace(
        evaluation_request,
        reproducibility=ReproducibilityObservation(
            reproducible=True,
            repeat_count=2,
            evidence_refs=["legacy-evidence"],
        ),
    )

    with pytest.raises(EvaluationConfigurationError, match="deprecated"):
        EvaluationPipeline(RunArtifactStore(artifact_root), redaction_environment={}).evaluate(
            legacy
        )


def test_lifecycle_volatility_is_stripped_even_when_metadata_kind_is_redacted(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    pack = loaded_pack(
        tmp_path / "pack",
        (scenario("redacted-lifecycle", DimensionName.CORRECTNESS),),
    )
    pipeline = EvaluationPipeline(
        RunArtifactStore(artifact_root),
        redaction_environment={"API_KEY": "execution_lifecycle"},
    )

    completed = pipeline.evaluate(
        request(
            artifact_root,
            pack,
            "sample_agent.variants.correct:run",
            agent_mvp_default_policy(),
            repeat=2,
        )
    )

    canonical_text = completed.artifacts.canonical.read_text(encoding="utf-8")
    assert '"kind": "[REDACTED]"' in canonical_text
    assert '"started_at"' not in canonical_text
    assert '"finished_at"' not in canonical_text
    assert '"duration_ms"' not in canonical_text
