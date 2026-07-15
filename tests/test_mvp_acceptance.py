"""End-to-end proof of the ten-point MVP acceptance contract."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import JsonValue, TypeAdapter
from typer.testing import CliRunner

from gauntlet import __version__
from gauntlet.cli import app
from gauntlet.core.models import (
    DimensionName,
    EvaluationRunStatus,
    Evidence,
    Finding,
    FindingSeverity,
    ScenarioResult,
    ScenarioResultStatus,
    ScoreCard,
)
from gauntlet.core.schema_export import get_json_schemas
from gauntlet.evidence import EvidenceStore, RunArtifactStore
from gauntlet.reporting import (
    CanonicalEvaluation,
    RegressionAssessment,
    ReproducibilityClaim,
    RunComparisonService,
    canonical_repeat_digest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = REPOSITORY_ROOT / "benchmarks" / "agent_mvp"
SECRET_SCENARIO_TITLE = "Malicious content inside retrieved data"
CORRECT_TARGET_SOURCE = "from .variants.correct import run\n"
DEGRADED_TARGET_SOURCE = "from .variants.injection_vulnerable import run\n"
runner = CliRunner()


@dataclass(frozen=True, slots=True)
class CompletedCliRun:
    """One CLI evaluation and its uniquely created artifact directory."""

    exit_code: int
    output: str
    run_id: str
    run_dir: Path


@dataclass(frozen=True, slots=True)
class AcceptanceRuns:
    """Shared expensive evaluations used across acceptance assertions."""

    artifact_root: Path
    correct: tuple[CompletedCliRun, CompletedCliRun, CompletedCliRun]
    degraded: CompletedCliRun


def _write_acceptance_project(root: Path) -> Path:
    project = root / "p"
    shutil.copytree(REPOSITORY_ROOT / "examples" / "sample_agent", project / "sample_agent")
    (project / "sample_agent" / "candidate.py").write_text(
        CORRECT_TARGET_SOURCE,
        encoding="utf-8",
    )
    config = project / ".gauntlet" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """project:
  name: mvp-acceptance-agent
adapter:
  type: python_callable
  target: sample_agent.candidate:run
evaluation:
  benchmark_packs: [gauntlet.agent.mvp]
  seed: 42
  repeat: 3
  timeout_seconds: 5
execution:
  network: disabled
  isolation: subprocess
reporting:
  formats: [json, markdown]
scoring:
  policy: agent_mvp_default
""",
        encoding="utf-8",
    )
    return project


def _invoke_evaluation(
    project: Path,
    artifact_root: Path,
    *,
    repeat: int = 3,
    scenario: str | None = None,
) -> CompletedCliRun:
    runs_root = artifact_root / "runs"
    before = {path.name for path in runs_root.glob("*") if path.is_dir()}
    evaluation_environment: dict[str, str | None] = {
        name: None for name in os.environ if name.startswith("GAUNTLET_")
    }
    evaluation_environment["MVP_ACCEPTANCE_SECRET"] = SECRET_SCENARIO_TITLE
    arguments = [
        "evaluate",
        str(project),
        "--benchmark",
        "gauntlet.agent.mvp",
        "--seed",
        "42",
        "--repeat",
        str(repeat),
        "--offline",
        "--artifact-root",
        str(artifact_root),
        "--quiet",
    ]
    if scenario is not None:
        arguments.extend(("--scenario", scenario))
    result = runner.invoke(
        app,
        arguments,
        env=evaluation_environment,
    )
    after = {path.name for path in runs_root.glob("*") if path.is_dir()}
    created = after - before
    if len(created) != 1:
        raise AssertionError(
            "One evaluation must create exactly one run directory; "
            f"created={sorted(created)!r}, output={result.output!r}"
        )
    run_id = created.pop()
    return CompletedCliRun(
        exit_code=result.exit_code,
        output=result.output,
        run_id=run_id,
        run_dir=runs_root / run_id,
    )


@pytest.fixture(scope="module")
def acceptance_runs(tmp_path_factory: pytest.TempPathFactory) -> AcceptanceRuns:
    """Run the full pack only four times for all ten acceptance checks."""

    root = tmp_path_factory.mktemp("a")
    project = _write_acceptance_project(root)
    artifact_root = root / "o"

    # Prime the installed worker/import path before measuring scenario budgets.
    # This run is not one of the three acceptance comparisons.
    warmup = _invoke_evaluation(
        project,
        artifact_root,
        repeat=1,
        scenario="agent.direct_answer",
    )
    if warmup.exit_code != 5:
        raise AssertionError(f"Unexpected warm-up outcome: {warmup.output}")

    correct_list = tuple(_invoke_evaluation(project, artifact_root) for _ in range(3))
    if len(correct_list) != 3:  # pragma: no cover - fixed comprehension size
        raise AssertionError("Three deterministic acceptance runs are required")
    correct = correct_list

    # Keep the configured target stable so comparison observes a code regression,
    # not an adapter-target/configuration change.
    (project / "sample_agent" / "candidate.py").write_text(
        DEGRADED_TARGET_SOURCE,
        encoding="utf-8",
    )
    degraded = _invoke_evaluation(project, artifact_root)
    return AcceptanceRuns(
        artifact_root=artifact_root,
        correct=correct,
        degraded=degraded,
    )


def _scenario_results(run: CompletedCliRun) -> list[ScenarioResult]:
    return TypeAdapter(list[ScenarioResult]).validate_json(
        (run.run_dir / "results.json").read_bytes()
    )


def _findings(run: CompletedCliRun) -> list[Finding]:
    return TypeAdapter(list[Finding]).validate_json((run.run_dir / "findings.json").read_bytes())


def test_acceptance_01_fresh_install_has_distribution_and_entrypoint() -> None:
    # The milestone's fresh-venv install command is the authoritative installation
    # proof. This test independently checks the installed metadata and console script.
    distribution = metadata.distribution("gauntlet")

    assert distribution.version == __version__
    assert any(
        entry_point.group == "console_scripts"
        and entry_point.name == "gauntlet"
        and entry_point.value == "gauntlet.cli:app"
        for entry_point in distribution.entry_points
    )
    version_result = runner.invoke(app, ["--version"])
    assert version_result.exit_code == 0, version_result.output
    assert version_result.output.strip() == f"gauntlet {__version__}"


def _physical_path(path: Path) -> Path:
    """Keep evidence checks valid beyond the legacy Windows path limit."""

    if os.name != "nt":
        return path
    value = str(path.resolve())
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def test_acceptance_02_doctor_passes(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "doctor",
            "--artifact-root",
            str(tmp_path / "doctor-artifacts"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Doctor passed: all required offline checks succeeded." in result.output
    assert result.output.count("[pass]") == 7


def test_acceptance_03_sample_benchmark_validates() -> None:
    result = runner.invoke(app, ["benchmark", "validate", str(PACK_ROOT)])

    assert result.exit_code == 0, result.output
    assert "Valid benchmark gauntlet.agent.mvp version 0.1.0" in result.output
    assert "15 scenarios" in result.output


def test_acceptance_04_sample_agent_evaluation_completes(
    acceptance_runs: AcceptanceRuns,
) -> None:
    store = RunArtifactStore(acceptance_runs.artifact_root)

    for completed in acceptance_runs.correct:
        assert completed.exit_code == 0, completed.output
        manifest = store.load_manifest(completed.run_id)
        assert manifest.status is EvaluationRunStatus.COMPLETED
        assert manifest.summary["scenarios_completed"] == 15
        assert manifest.summary["release_recommendation"] == "ready"
        results = _scenario_results(completed)
        assert len(results) == 15
        assert all(result.status is ScenarioResultStatus.PASSED for result in results)
        for artifact in (
            "results.json",
            "scorecard.json",
            "findings.json",
            "canonical.json",
            "report.md",
        ):
            assert (completed.run_dir / artifact).is_file()


def test_acceptance_05_expected_findings_are_evidence_backed(
    acceptance_runs: AcceptanceRuns,
) -> None:
    findings = _findings(acceptance_runs.degraded)
    results = _scenario_results(acceptance_runs.degraded)
    critical_security = [
        finding
        for finding in findings
        if finding.severity is FindingSeverity.CRITICAL
        and finding.dimension is DimensionName.SECURITY
        and "no_forbidden_calls" in finding.id
    ]

    expected = next(
        finding
        for finding in critical_security
        if "agent.malicious_retrieved_content" in finding.id
    )
    assert expected.evidence_refs
    malicious_result = next(
        result for result in results if result.scenario_id == "agent.malicious_retrieved_content"
    )
    assert expected.id in malicious_result.findings
    assert all(
        _physical_path(
            acceptance_runs.degraded.run_dir
            / "evidence"
            / f"{reference.removeprefix('evidence_')}.json"
        ).is_file()
        for reference in expected.evidence_refs
    )


def test_acceptance_06_json_conforms_to_schema_and_evidence_hashes(
    acceptance_runs: AcceptanceRuns,
) -> None:
    completed = acceptance_runs.correct[0]
    store = RunArtifactStore(acceptance_runs.artifact_root)
    evidence_store = EvidenceStore(store, environment={})
    schemas = get_json_schemas()
    validators = {name: Draft202012Validator(schema) for name, schema in schemas.items()}
    for validator in validators.values():
        validator.check_schema(validator.schema)

    manifest = store.load_manifest(completed.run_id)
    validators["evaluation-run"].validate(manifest.model_dump(mode="json"))

    raw_results = cast(
        list[object],
        json.loads((completed.run_dir / "results.json").read_text(encoding="utf-8")),
    )
    results = TypeAdapter(list[ScenarioResult]).validate_python(raw_results)
    assert len(results) == 15
    for result in raw_results:
        validators["scenario-result"].validate(result)

    raw_scorecard = cast(
        dict[str, object],
        json.loads((completed.run_dir / "scorecard.json").read_text(encoding="utf-8")),
    )
    validators["score-card"].validate(raw_scorecard)
    ScoreCard.model_validate(raw_scorecard)

    raw_findings = cast(
        list[object],
        json.loads((completed.run_dir / "findings.json").read_text(encoding="utf-8")),
    )
    findings = TypeAdapter(list[Finding]).validate_python(raw_findings)
    for finding in raw_findings:
        validators["finding"].validate(finding)

    CanonicalEvaluation.model_validate_json((completed.run_dir / "canonical.json").read_bytes())
    evidence_paths = sorted((completed.run_dir / "evidence").glob("*.json"))
    assert evidence_paths
    evidence_ids: set[str] = set()
    for path in evidence_paths:
        encoded = _physical_path(path).read_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        assert path.stem == digest
        envelope = cast(dict[str, JsonValue], json.loads(encoded))
        assert set(envelope) == {
            "schema_version",
            "type",
            "payload",
            "metadata",
            "redacted",
        }
        evidence = Evidence.model_validate(
            {
                "id": f"evidence_{digest}",
                "type": envelope["type"],
                "path": f"evidence/{digest}.json",
                "content_hash": f"sha256:{digest}",
                "redacted": envelope["redacted"],
                "metadata": envelope["metadata"],
            }
        )
        evidence_ids.add(evidence.id)
        validators["evidence"].validate(evidence.model_dump(mode="json"))
        assert evidence_store.load(completed.run_id, evidence) == envelope

    linked_refs = {reference for result in results for reference in result.evidence_refs} | {
        reference for finding in findings for reference in finding.evidence_refs
    }
    reproducibility = manifest.summary["reproducibility"]
    assert isinstance(reproducibility, dict)
    reproducibility_refs = reproducibility["evidence_refs"]
    assert isinstance(reproducibility_refs, list)
    assert all(isinstance(reference, str) for reference in reproducibility_refs)
    linked_refs.update(cast(list[str], reproducibility_refs))
    assert linked_refs <= evidence_ids


def test_acceptance_07_markdown_report_is_redacted_and_has_15_rows(
    acceptance_runs: AcceptanceRuns,
) -> None:
    run_dir = acceptance_runs.degraded.run_dir
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    scenario_rows = [line for line in report.splitlines() if line.startswith("| agent.")]

    assert report.startswith("# GAUNTLET Evaluation Report")
    assert "## Scenario results" in report
    assert len(scenario_rows) == 15
    assert "Repeats completed: 3" in report
    assert "not a hardened sandbox for malicious code" in report
    assert "Raw outputs, fixtures, hidden expected values" in report
    assert "synthetic-only" not in report
    assert "[REDACTED]" in report
    assert SECRET_SCENARIO_TITLE not in report
    assert all(
        SECRET_SCENARIO_TITLE not in _physical_path(path).read_text(encoding="utf-8")
        for path in run_dir.rglob("*")
        if path.is_file()
    )


def test_acceptance_08_three_deterministic_runs_are_byte_identical(
    acceptance_runs: AcceptanceRuns,
) -> None:
    canonical_bytes = tuple(
        (completed.run_dir / "canonical.json").read_bytes() for completed in acceptance_runs.correct
    )

    assert canonical_bytes[0] == canonical_bytes[1] == canonical_bytes[2]
    canonical = CanonicalEvaluation.model_validate_json(canonical_bytes[0])
    assert canonical.comparison_contract == "adr-004-deterministic-fixture-v1"
    assert len(canonical.repeats) == 3
    assert len({canonical_repeat_digest(repeat) for repeat in canonical.repeats}) == 1
    for completed in acceptance_runs.correct:
        manifest = RunArtifactStore(acceptance_runs.artifact_root).load_manifest(completed.run_id)
        reproducibility = manifest.summary["reproducibility"]
        assert isinstance(reproducibility, dict)
        assert reproducibility["claim"] == ReproducibilityClaim.BYTE_IDENTICAL.value


def test_acceptance_09_degraded_agent_is_reported_as_regression(
    acceptance_runs: AcceptanceRuns,
) -> None:
    baseline = acceptance_runs.correct[0]
    candidate = acceptance_runs.degraded

    comparison = RunComparisonService(RunArtifactStore(acceptance_runs.artifact_root)).compare(
        baseline.run_id, candidate.run_id
    )
    cli_result = runner.invoke(
        app,
        [
            "compare",
            baseline.run_id,
            candidate.run_id,
            "--artifact-root",
            str(acceptance_runs.artifact_root),
        ],
    )

    assert comparison.assessment is RegressionAssessment.REGRESSION
    assert comparison.context_changes == []
    assert comparison.new_failures
    assert cli_result.exit_code == 1, cli_result.output
    assert "regression" in cli_result.output.lower()


def test_acceptance_10_policy_failure_exits_nonzero_with_completed_artifacts(
    acceptance_runs: AcceptanceRuns,
) -> None:
    degraded = acceptance_runs.degraded
    store = RunArtifactStore(acceptance_runs.artifact_root)
    manifest = store.load_manifest(degraded.run_id)
    scorecard = ScoreCard.model_validate_json((degraded.run_dir / "scorecard.json").read_bytes())

    assert degraded.exit_code == 1, degraded.output
    assert manifest.status is EvaluationRunStatus.COMPLETED
    assert manifest.summary["release_recommendation"] == "not_ready"
    assert scorecard.overall <= 49
    assert _findings(degraded)
    for artifact in (
        "results.json",
        "scorecard.json",
        "findings.json",
        "canonical.json",
        "report.md",
    ):
        assert (degraded.run_dir / artifact).is_file()
