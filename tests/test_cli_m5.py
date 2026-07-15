"""CLI coverage for M5 evaluate, inspect, and doctor commands."""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from gauntlet.cli import app

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def _sample_project(tmp_path: Path) -> Path:
    project = tmp_path / "sample-project"
    shutil.copytree(REPOSITORY_ROOT / "examples" / "sample_agent", project / "sample_agent")
    config = project / ".gauntlet" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """project:
  name: sample-agent
adapter:
  type: python_callable
  target: sample_agent.variants.correct:run
evaluation:
  benchmark_packs: [gauntlet.agent.mvp]
  seed: 42
  repeat: 1
  timeout_seconds: 2
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


def test_inspect_cli_reports_static_callable_without_importing_it(tmp_path: Path) -> None:
    project = _sample_project(tmp_path)
    marker = project / "imported.txt"
    app_source = project / "sample_agent" / "app.py"
    app_source.write_text(
        app_source.read_text(encoding="utf-8")
        + f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    config = project / ".gauntlet" / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "sample_agent.variants.correct:run", "sample_agent.app:run"
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["inspect", str(project)])

    assert result.exit_code == 0, result.output
    assert "Supported adapter: python_callable" in result.output
    assert "Recommended target: sample_agent.app:run" in result.output
    assert "Estimated evaluation cost: not reported" in result.output
    assert not marker.exists()


def test_doctor_cli_passes_all_offline_checks(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["doctor", "--artifact-root", str(tmp_path / "doctor-artifacts")],
    )

    assert result.exit_code == 0, result.output
    assert "[pass] child_process" in result.output
    assert "[pass] builtin_benchmark" in result.output
    assert "Doctor passed" in result.output


def test_evaluate_cli_honors_flags_writes_report_and_returns_policy_code(
    tmp_path: Path,
) -> None:
    project = _sample_project(tmp_path)
    artifact_root = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        [
            "evaluate",
            str(project),
            "--benchmark",
            "gauntlet.agent.mvp",
            "--scenario",
            "agent.direct_answer",
            "--seed",
            "42",
            "--repeat",
            "2",
            "--offline",
            "--artifact-root",
            str(artifact_root),
            "--verbose",
        ],
    )

    assert result.exit_code == 5, result.output
    assert "Recommendation: evaluation_inconclusive" in result.output
    assert "Reproducibility: byte_identical (2 repeat(s))" in result.output
    assert "Report:" in result.output
    reports = list((artifact_root / "runs").glob("*/report.md"))
    assert len(reports) == 1
    assert (reports[0].parent / "canonical.json").is_file()


def test_evaluate_cli_rejects_conflicting_output_modes_and_bad_repeat(tmp_path: Path) -> None:
    project = _sample_project(tmp_path)

    conflicting = runner.invoke(
        app,
        ["evaluate", str(project), "--quiet", "--verbose"],
    )
    bad_repeat = runner.invoke(
        app,
        ["evaluate", str(project), "--repeat", "0"],
    )

    assert conflicting.exit_code == 2
    assert "mutually exclusive" in conflicting.output
    assert bad_repeat.exit_code == 2


def test_evaluate_cli_classifies_unusable_artifact_root_without_traceback(
    tmp_path: Path,
) -> None:
    project = _sample_project(tmp_path)
    unusable_root = project / ".gauntlet" / "config.yaml"

    result = runner.invoke(
        app,
        [
            "evaluate",
            str(project),
            "--scenario",
            "agent.direct_answer",
            "--artifact-root",
            str(unusable_root),
        ],
    )

    assert result.exit_code == 2
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_evaluate_help_lists_every_m5_flag() -> None:
    result = runner.invoke(app, ["evaluate", "--help"])

    assert result.exit_code == 0, result.output
    for option in (
        "--profile",
        "--benchmark",
        "--scenario",
        "--seed",
        "--repeat",
        "--offline",
        "--artifact-root",
        "--quiet",
        "--verbose",
    ):
        assert option in result.output
