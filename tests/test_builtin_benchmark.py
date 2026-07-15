"""Built-in and project-local benchmark resolution tests."""

from __future__ import annotations

from pathlib import Path
from shutil import copytree

import pytest

import gauntlet.benchmarks.builtin as builtin_module
from gauntlet.benchmarks import (
    BUILTIN_AGENT_MVP_ID,
    BenchmarkPackError,
    builtin_agent_mvp_path,
    load_benchmark_pack,
    resolve_benchmark_reference,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AGENT_MVP_PACK = REPOSITORY_ROOT / "benchmarks" / "agent_mvp"
MINIMAL_PACK = Path(__file__).parent / "fixtures" / "benchmarks" / "minimal_valid"


def _copy_minimal_pack(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return copytree(MINIMAL_PACK, destination)


def test_builtin_path_resolves_the_complete_source_pack() -> None:
    path = builtin_agent_mvp_path()
    loaded = load_benchmark_pack(path)

    assert path == AGENT_MVP_PACK.resolve()
    assert path.is_dir()
    assert loaded.identity.id == BUILTIN_AGENT_MVP_ID
    assert len(loaded.scenarios) == 15


def test_builtin_path_prefers_a_valid_packaged_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_root = tmp_path / "installed" / "gauntlet" / "benchmarks"
    packaged = copytree(AGENT_MVP_PACK, resource_root / "agent_mvp")
    monkeypatch.setattr(builtin_module, "files", lambda _package: resource_root)
    monkeypatch.setattr(
        builtin_module,
        "_source_checkout_candidate",
        lambda: tmp_path / "unused-source-fallback",
    )

    assert builtin_agent_mvp_path() == packaged.resolve()


def test_builtin_path_uses_source_fallback_when_resource_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builtin_module, "files", lambda _package: tmp_path / "missing-resource")
    monkeypatch.setattr(builtin_module, "_source_checkout_candidate", lambda: AGENT_MVP_PACK)

    assert builtin_agent_mvp_path() == AGENT_MVP_PACK.resolve()


def test_builtin_path_failure_lists_recovery_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builtin_module, "files", lambda _package: tmp_path / "missing-resource")
    monkeypatch.setattr(
        builtin_module,
        "_source_checkout_candidate",
        lambda: tmp_path / "missing-source",
    )

    with pytest.raises(BenchmarkPackError) as caught:
        builtin_agent_mvp_path()

    message = str(caught.value)
    assert BUILTIN_AGENT_MVP_ID in message
    assert "packaged resource" in message
    assert "source checkout fallback" in message
    assert "Reinstall GAUNTLET" in message
    assert "explicit benchmark path" in message


def test_resolve_accepts_existing_absolute_and_relative_paths(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    relative_pack = _copy_minimal_pack(project_root / "packs" / "relative")
    absolute_pack = _copy_minimal_pack(tmp_path / "outside" / "absolute")

    relative = resolve_benchmark_reference(project_root, "packs/relative")
    absolute = resolve_benchmark_reference(project_root, str(absolute_pack))

    assert relative.root == relative_pack.resolve()
    assert absolute.root == absolute_pack.resolve()


def test_resolve_rejects_relative_escape_and_allows_explicit_absolute_path(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside_pack = _copy_minimal_pack(tmp_path / "outside-pack")

    with pytest.raises(BenchmarkPackError, match="escapes project root"):
        resolve_benchmark_reference(project_root, "../outside-pack")

    assert resolve_benchmark_reference(project_root, str(outside_pack)).root == (
        outside_pack.resolve()
    )


def test_reserved_id_cannot_be_shadowed_by_project_content(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _copy_minimal_pack(project_root / BUILTIN_AGENT_MVP_ID)

    loaded = resolve_benchmark_reference(project_root, BUILTIN_AGENT_MVP_ID)

    assert loaded.identity.id == BUILTIN_AGENT_MVP_ID
    assert loaded.root == builtin_agent_mvp_path()


def test_project_local_resolution_prefers_dot_gauntlet_then_benchmarks(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    preferred = _copy_minimal_pack(project_root / ".gauntlet" / "benchmarks" / "local")
    _copy_minimal_pack(project_root / "benchmarks" / "local")

    loaded = resolve_benchmark_reference(project_root, "local")

    assert loaded.root == preferred.resolve()


@pytest.mark.parametrize("selector", ["", "   "])
def test_resolve_rejects_blank_selector(tmp_path: Path, selector: str) -> None:
    with pytest.raises(BenchmarkPackError, match="must not be blank"):
        resolve_benchmark_reference(tmp_path, selector)


def test_resolve_reports_every_checked_local_candidate(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkPackError) as caught:
        resolve_benchmark_reference(tmp_path, "missing-pack")

    message = str(caught.value)
    assert "missing-pack" in message
    assert str(tmp_path / ".gauntlet" / "benchmarks" / "missing-pack") in message
    assert str(tmp_path / "benchmarks" / "missing-pack") in message
    assert BUILTIN_AGENT_MVP_ID in message


def test_resolve_rejects_missing_project_root_and_explicit_path(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkPackError, match="Project root does not exist"):
        resolve_benchmark_reference(tmp_path / "missing-project", BUILTIN_AGENT_MVP_ID)

    with pytest.raises(BenchmarkPackError, match="Explicit benchmark path .* does not exist"):
        resolve_benchmark_reference(tmp_path, "./missing-pack")
