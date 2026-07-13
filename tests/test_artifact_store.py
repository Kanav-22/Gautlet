"""Tests for the filesystem-backed run artifact store."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from gauntlet.config.loader import resolve_config
from gauntlet.config.models import GauntletConfig
from gauntlet.core.models import EvaluationRun, EvaluationRunStatus
from gauntlet.evidence import (
    ArtifactCorruptionError,
    ArtifactStoreError,
    InvalidRunIdError,
    RunArtifactStore,
    RunNotFoundError,
)

FIXED_TIME = datetime(2026, 7, 13, 8, 9, 10, tzinfo=UTC)


def resolved_config() -> GauntletConfig:
    return resolve_config(
        project_config={
            "project": {"name": "Example"},
            "adapter": {"type": "python_callable", "target": "example:run"},
            "evaluation": {"seed": 42},
        },
        environ={},
    )


def make_store(tmp_path: Path) -> RunArtifactStore:
    return RunArtifactStore(
        tmp_path / "artifacts",
        clock=lambda: FIXED_TIME,
        nonce_factory=lambda: "deadbeef",
    )


def create_run(store: RunArtifactStore) -> EvaluationRun:
    return store.create_run(
        project_id="project-123",
        profile_id="default",
        benchmark_pack_ids=["core"],
        environment_fingerprint="sha256:environment",
        environment={"python": "3.12", "platform": "test"},
        resolved_config=resolved_config(),
        seed=42,
        plugin_versions={"builtin": "1"},
    )


def test_default_root_is_user_artifact_directory() -> None:
    store = RunArtifactStore()

    assert store.root == Path.home() / ".gauntlet" / "artifacts"
    assert store.runs_root == store.root / "runs"


def test_create_pending_run_writes_only_mandatory_artifacts(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    manifest = create_run(store)
    run_dir = store.runs_root / manifest.id

    assert manifest.id == "run_20260713_080910_deadbeef"
    assert manifest.project_id == "project-123"
    assert manifest.status is EvaluationRunStatus.PENDING
    assert manifest.finished_at is None

    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "environment.json").is_file()
    assert (run_dir / "config.resolved.yaml").is_file()

    for directory in ("logs", "traces", "evidence", "scenarios"):
        assert (run_dir / directory).is_dir()

    for absent_name in (
        "results.json",
        "scorecard.json",
        "findings.json",
        "report.md",
        "report.html",
    ):
        assert not (run_dir / absent_name).exists()

    assert store.load_manifest(manifest.id) == manifest
    assert json.loads((run_dir / "environment.json").read_text(encoding="utf-8")) == {
        "platform": "test",
        "python": "3.12",
    }

    resolved = yaml.safe_load((run_dir / "config.resolved.yaml").read_text(encoding="utf-8"))
    assert resolved["project"]["name"] == "Example"
    assert resolved["evaluation"]["seed"] == 42


def test_run_id_generation_normalizes_to_utc(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path, nonce_factory=lambda: "ABCDEF12")
    local_time = datetime.fromisoformat("2026-07-13T13:39:10+05:30")

    assert store.generate_run_id(local_time) == "run_20260713_080910_abcdef12"


@pytest.mark.parametrize(
    "bad_nonce",
    ["", "abc", "abcdefgh", "000000000", "../bad00"],
)
def test_run_id_generation_rejects_unsafe_nonce(
    tmp_path: Path,
    bad_nonce: str,
) -> None:
    store = RunArtifactStore(
        tmp_path,
        clock=lambda: FIXED_TIME,
        nonce_factory=lambda: bad_nonce,
    )

    with pytest.raises(InvalidRunIdError):
        store.generate_run_id()


@pytest.mark.parametrize(
    "run_id",
    [
        "../manifest.json",
        "run_20260713_080910_DEADBEEF",
        "run_20260713_080910_deadbee",
        "run_20260713_080910_deadbeef/extra",
        "not-a-run",
    ],
)
def test_load_rejects_noncanonical_run_ids(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(InvalidRunIdError):
        make_store(tmp_path).load_manifest(run_id)


def test_create_rejects_duplicate_run_id(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    create_run(store)

    with pytest.raises(ArtifactStoreError, match="already exists"):
        create_run(store)


def test_scan_returns_valid_runs_and_explicit_corruption(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    valid = create_run(store)

    corrupt_id = "run_20260713_080911_badc0ffe"
    corrupt_dir = store.runs_root / corrupt_id
    corrupt_dir.mkdir()
    (corrupt_dir / "manifest.json").write_text(
        "{this is not json",
        encoding="utf-8",
    )

    invalid_dir = store.runs_root / "unsafe-run-name"
    invalid_dir.mkdir()
    (invalid_dir / "manifest.json").write_text("{}", encoding="utf-8")

    nested_manifest = store.runs_root / "unrelated" / "nested" / "manifest.json"
    nested_manifest.parent.mkdir(parents=True)
    nested_manifest.write_text("{}", encoding="utf-8")

    result = store.scan()

    assert result.runs == (valid,)
    assert len(result.problems) == 2
    assert {problem.run_id for problem in result.problems} == {
        corrupt_id,
        "unsafe-run-name",
    }
    assert all(problem.reason for problem in result.problems)
    assert valid.status is EvaluationRunStatus.PENDING


def test_load_reports_corrupt_or_mismatched_manifest(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    manifest = create_run(store)
    manifest_path = store.run_dir(manifest.id) / "manifest.json"
    mismatched = manifest.model_copy(update={"id": "run_20260713_080911_badc0ffe"})
    manifest_path.write_text(mismatched.model_dump_json(), encoding="utf-8")

    with pytest.raises(ArtifactCorruptionError, match="does not match") as caught:
        store.load_manifest(manifest.id)

    assert caught.value.run_id == manifest.id
    assert caught.value.manifest_path == manifest_path


def test_missing_run_has_explicit_error(tmp_path: Path) -> None:
    with pytest.raises(RunNotFoundError):
        make_store(tmp_path).load_manifest("run_20260713_080911_badc0ffe")


@pytest.mark.parametrize(
    "relative_path",
    [
        "../escape.json",
        "nested/../../escape.json",
        "not-json.txt",
    ],
)
def test_json_writer_rejects_unsafe_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    store = make_store(tmp_path)
    manifest = create_run(store)

    with pytest.raises(ArtifactStoreError):
        store.write_json(manifest.id, relative_path, {"unsafe": True})

    assert not (store.root / "escape.json").exists()


def test_json_and_manifest_writes_are_atomic_and_loadable(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    manifest = create_run(store)

    results_path = store.write_json(
        manifest.id,
        "scenarios/result.json",
        {"status": "passed"},
    )
    assert json.loads(results_path.read_text(encoding="utf-8")) == {"status": "passed"}

    running = manifest.model_copy(update={"status": EvaluationRunStatus.RUNNING})
    store.write_manifest(running)
    assert store.load_manifest(manifest.id).status is EvaluationRunStatus.RUNNING

    run_dir = store.run_dir(manifest.id)
    assert not tuple(run_dir.rglob("*.tmp"))
