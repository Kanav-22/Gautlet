"""Project-level orchestration tests for the M5 evaluate command."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gauntlet.orchestration import (
    ProjectEvaluationError,
    evaluate_project,
    prepare_evaluation,
)
from gauntlet.reporting import (
    EvaluationConfigurationError,
    EvaluationExitCode,
    ReproducibilityClaim,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path, *, packs: list[str] | None = None) -> Path:
    project = tmp_path / "project"
    config_dir = project / ".gauntlet"
    (config_dir / "profiles").mkdir(parents=True)
    configured_packs = packs or ["gauntlet.agent.mvp"]
    pack_lines = "\n".join(f"    - {item}" for item in configured_packs)
    (config_dir / "config.yaml").write_text(
        "\n".join(
            [
                "project:",
                "  name: orchestration-test",
                "adapter:",
                "  type: python_callable",
                "  target: sample_agent.variants.correct:run",
                "evaluation:",
                "  benchmark_packs:",
                pack_lines,
                "  seed: 7",
                "  repeat: 1",
                "  timeout_seconds: 2",
                "execution:",
                "  network: enabled",
                "  isolation: subprocess",
                "reporting:",
                "  formats: [json, markdown]",
                "scoring:",
                "  policy: agent_mvp_default",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return project


def test_prepare_evaluation_applies_profile_then_cli_and_filters_scenario(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    profile = project / ".gauntlet" / "profiles" / "strict.yaml"
    profile.write_text("evaluation:\n  seed: 11\n  repeat: 2\n", encoding="utf-8")
    artifact_root = tmp_path / "artifacts"

    prepared = prepare_evaluation(
        project,
        profile="strict",
        benchmark="gauntlet.agent.mvp",
        scenario="agent.direct_answer",
        seed=42,
        repeat=3,
        offline=True,
        artifact_root=artifact_root,
        environ={},
    )

    assert prepared.profile_id == "strict"
    assert prepared.benchmark.identity.id == "gauntlet.agent.mvp"
    assert [scenario.id for scenario in prepared.benchmark.scenarios] == ["agent.direct_answer"]
    assert prepared.config.evaluation.seed == 42
    assert prepared.config.evaluation.repeat == 3
    assert prepared.config.execution.network.value == "disabled"
    assert prepared.config.artifacts.root == artifact_root.resolve()
    assert prepared.environment_fingerprint.startswith("sha256:")


def test_prepare_evaluation_requires_config_and_unambiguous_benchmark(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ProjectEvaluationError, match="gauntlet init"):
        prepare_evaluation(empty, environ={})

    project = _project(tmp_path, packs=["gauntlet.agent.mvp", "second.pack"])
    with pytest.raises(ProjectEvaluationError, match="exactly one benchmark"):
        prepare_evaluation(project, environ={})


def test_prepare_evaluation_rejects_unsafe_profile_and_unknown_scenario(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    with pytest.raises(ProjectEvaluationError, match="safe project-local name"):
        prepare_evaluation(project, profile="../secret", environ={})
    with pytest.raises(ProjectEvaluationError, match="available:"):
        prepare_evaluation(project, scenario="agent.not_present", environ={})


def test_evaluate_project_runs_real_repeat_pipeline_and_writes_canonical(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    shutil.copytree(REPOSITORY_ROOT / "examples" / "sample_agent", project / "sample_agent")
    artifact_root = tmp_path / "artifacts"

    result = evaluate_project(
        project,
        scenario="agent.direct_answer",
        seed=42,
        repeat=2,
        offline=True,
        artifact_root=artifact_root,
        environ={},
    )

    assert result.exit_code is EvaluationExitCode.INCOMPLETE_EVALUATION
    assert result.run.status.value == "completed"
    assert result.reproducibility.claim is ReproducibilityClaim.BYTE_IDENTICAL
    assert result.reproducibility.repeat_count == 2
    assert result.artifacts.canonical.is_file()
    assert result.artifacts.markdown.is_file()


def test_network_enabled_evaluation_never_claims_deterministic_reproducibility(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    shutil.copytree(REPOSITORY_ROOT / "examples" / "sample_agent", project / "sample_agent")

    result = evaluate_project(
        project,
        scenario="agent.direct_answer",
        repeat=2,
        artifact_root=tmp_path / "live-artifacts",
        environ={},
    )

    assert result.reproducibility.claim is ReproducibilityClaim.LIVE_VARIANCE_ONLY
    assert result.reproducibility.live_distribution is not None
    assert "no deterministic claim" in result.artifacts.markdown.read_text(encoding="utf-8")


def test_evaluation_rejects_installed_target_outside_project_root(tmp_path: Path) -> None:
    project = _project(tmp_path)
    config = project / ".gauntlet" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "sample_agent.variants.correct:run",
            "gauntlet.cli.app:main",
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationConfigurationError, match="beneath project root"):
        evaluate_project(
            project,
            scenario="agent.direct_answer",
            artifact_root=tmp_path / "artifacts",
            environ={},
        )


def test_src_layout_target_executes_from_its_project_owned_import_root(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    config = project / ".gauntlet" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "sample_agent.variants.correct:run",
            "sample_agent.agent:run",
        ),
        encoding="utf-8",
    )
    package = project / "src" / "sample_agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agent.py").write_text(
        """def run(payload, *, tools):
    del payload, tools
    return {"completed": True, "value": 4, "saved": False}
""",
        encoding="utf-8",
    )

    result = evaluate_project(
        project,
        scenario="agent.direct_answer",
        artifact_root=tmp_path / "artifacts",
        environ={},
    )

    assert result.run.status.value == "completed"
    assert result.results[0].status.value == "passed"
