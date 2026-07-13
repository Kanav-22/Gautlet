"""CLI tests for project initialization and run discovery."""

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from gauntlet import __version__
from gauntlet.cli import app
from gauntlet.config.loader import resolve_config
from gauntlet.core.models import EvaluationRun, EvaluationRunStatus

runner = CliRunner()
RUN_ID = "run_20260713_010203_a1b2c3d4"


def write_manifest(artifact_root: Path, run_id: str = RUN_ID) -> EvaluationRun:
    run = EvaluationRun(
        id=run_id,
        project_id="sample-agent",
        profile_id="default",
        benchmark_pack_ids=["gauntlet.agent.mvp"],
        started_at=datetime(2026, 7, 13, 1, 2, 3, tzinfo=UTC),
        finished_at=None,
        status=EvaluationRunStatus.PENDING,
        seed=42,
        environment_fingerprint="test-environment",
        gauntlet_version=__version__,
        plugin_versions={},
        summary={},
    )
    run_dir = artifact_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return run


def test_init_creates_exact_valid_project_layout(tmp_path: Path) -> None:
    project = tmp_path / "sample-agent"

    result = runner.invoke(app, ["init", str(project)])

    assert result.exit_code == 0, result.output
    files = {path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file()}
    directories = {
        path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_dir()
    }
    assert files == {
        ".gauntlet/config.yaml",
        ".gauntlet/profiles/default.yaml",
        ".gauntlet/benchmarks/README.md",
        ".gauntlet/adapters/python_callable.py",
        ".gauntletignore",
    }
    assert directories == {
        ".gauntlet",
        ".gauntlet/profiles",
        ".gauntlet/benchmarks",
        ".gauntlet/adapters",
    }

    resolved = resolve_config(
        project_config=project / ".gauntlet/config.yaml",
        profile_defaults=project / ".gauntlet/profiles/default.yaml",
        environ={},
    )
    assert resolved.project.name == "sample-agent"
    assert resolved.artifacts.root == Path.home() / ".gauntlet" / "artifacts"

    adapter_source = (project / ".gauntlet/adapters/python_callable.py").read_text(encoding="utf-8")
    ast.parse(adapter_source)
    assert "def run(payload: dict) -> dict:" in adapter_source
    assert "NotImplementedError" in adapter_source
    assert "import gauntlet" not in adapter_source.lower()
    assert "Milestone 2" in adapter_source


def test_init_is_non_destructive_and_force_overwrites(tmp_path: Path) -> None:
    project = tmp_path / "existing"
    first = runner.invoke(app, ["init", str(project)])
    assert first.exit_code == 0, first.output
    config = project / ".gauntlet/config.yaml"
    config.write_text("user-owned: true\n", encoding="utf-8")

    repeated = runner.invoke(app, ["init", str(project)])

    assert repeated.exit_code == 0, repeated.output
    assert "created=0, overwritten=0, skipped=5" in repeated.output
    assert config.read_text(encoding="utf-8") == "user-owned: true\n"

    forced = runner.invoke(app, ["init", str(project), "--force"])

    assert forced.exit_code == 0, forced.output
    assert "created=0, overwritten=5, skipped=0" in forced.output
    assert "project:" in config.read_text(encoding="utf-8")


def test_runs_list_missing_root_is_an_empty_success(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["runs", "list", "--artifact-root", str(tmp_path / "missing")],
    )

    assert result.exit_code == 0, result.output
    assert result.output == "No runs found.\n"


def test_runs_list_and_show_valid_manifest(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    expected = write_manifest(artifact_root)

    listed = runner.invoke(
        app,
        ["runs", "list", "--artifact-root", str(artifact_root)],
    )
    shown = runner.invoke(
        app,
        ["runs", "show", RUN_ID, "--artifact-root", str(artifact_root)],
    )

    assert listed.exit_code == 0, listed.output
    assert RUN_ID in listed.output
    assert "pending" in listed.output
    assert "sample-agent" in listed.output
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output) == expected.model_dump(mode="json")


def test_runs_list_uses_environment_artifact_root(tmp_path: Path) -> None:
    artifact_root = tmp_path / "environment-artifacts"
    write_manifest(artifact_root)

    result = runner.invoke(
        app,
        ["runs", "list"],
        env={"GAUNTLET_ARTIFACT_ROOT": str(artifact_root)},
    )

    assert result.exit_code == 0, result.output
    assert RUN_ID in result.output


def test_runs_show_invalid_and_unknown_ids_are_configuration_errors(tmp_path: Path) -> None:
    invalid = runner.invoke(
        app,
        ["runs", "show", "../bad", "--artifact-root", str(tmp_path)],
    )
    unknown = runner.invoke(
        app,
        [
            "runs",
            "show",
            "run_20260713_010203_deadbeef",
            "--artifact-root",
            str(tmp_path),
        ],
    )

    assert invalid.exit_code == 2
    assert "Error:" in invalid.output
    assert unknown.exit_code == 2
    assert "Error:" in unknown.output


def test_runs_list_and_show_report_corrupt_manifests_as_incomplete(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    manifest = artifact_root / "runs" / RUN_ID / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{not-json", encoding="utf-8")

    listed = runner.invoke(
        app,
        ["runs", "list", "--artifact-root", str(artifact_root)],
    )
    shown = runner.invoke(
        app,
        ["runs", "show", RUN_ID, "--artifact-root", str(artifact_root)],
    )

    assert listed.exit_code == 5
    assert "Incomplete:" in listed.output
    assert RUN_ID in listed.output
    assert shown.exit_code == 5
    assert "Incomplete:" in shown.output
