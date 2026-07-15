"""Static project-inspection tests."""

from pathlib import Path

import pytest

from gauntlet.discovery import (
    InspectionInputError,
    InspectionLevel,
    PythonProjectKind,
    inspect_project,
)


def _write_config(project: Path, target: str) -> None:
    config = project / ".gauntlet" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"adapter:\n  type: python_callable\n  target: {target}\n",
        encoding="utf-8",
    )


def test_inspect_finds_configured_callable_without_executing_user_code(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "src" / "sample_agent"
    package.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    marker = project / "imported.txt"
    (package / "app.py").write_text(
        "from pathlib import Path\n"
        "import langchain\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "def run(payload, *, tools):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    _write_config(project, "sample_agent.app:run")

    result = inspect_project(project)

    assert result.ok
    assert result.project_kind is PythonProjectKind.PROJECT
    assert result.packages == ("sample_agent",)
    assert result.framework_hints == ("LangChain",)
    assert result.configured_target == "sample_agent.app:run"
    assert result.recommended_target == "sample_agent.app:run"
    assert result.recommended_adapter == "python_callable"
    assert [candidate.target for candidate in result.callables] == ["sample_agent.app:run"]
    assert result.callables[0].accepts_payload
    assert result.callables[0].accepts_tools
    assert not result.callables[0].async_callable
    assert "configured_target_found" in {finding.code for finding in result.findings}
    assert "framework_import_detected" in {finding.code for finding in result.findings}
    assert not marker.exists(), "inspection imported or executed user code"


def test_inspect_reports_incompatible_configured_target_actionably(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = project / "agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "app.py").write_text(
        "async def run(payload):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    _write_config(project, "agent.app:run")

    result = inspect_project(project)

    assert not result.ok
    finding = next(
        finding for finding in result.findings if finding.code == "incompatible_adapter_signature"
    )
    assert finding.level is InspectionLevel.ERROR
    assert "synchronous" in finding.message
    assert finding.action is not None
    assert "injected tools registry" in finding.action


def test_inspect_warns_for_bad_source_and_recommends_valid_candidate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (project / "worker.py").write_text(
        "def invoke(payload, **kwargs):\n    return {'ok': True}\n",
        encoding="utf-8",
    )

    result = inspect_project(project)

    assert result.ok
    assert result.project_kind is PythonProjectKind.MODULE
    assert result.packages == ()
    assert result.recommended_target == "worker:invoke"
    by_code = {finding.code: finding for finding in result.findings}
    assert by_code["missing_gauntlet_config"].level is InspectionLevel.WARNING
    assert by_code["uninspectable_source"].level is InspectionLevel.WARNING
    assert "Repair or exclude" in (by_code["uninspectable_source"].action or "")


def test_inspect_rejects_missing_path() -> None:
    with pytest.raises(InspectionInputError, match="does not exist or is unreadable"):
        inspect_project("definitely-missing-gauntlet-project")


def test_inspect_rejects_non_python_file(tmp_path: Path) -> None:
    text = tmp_path / "README.md"
    text.write_text("not Python", encoding="utf-8")

    with pytest.raises(InspectionInputError, match="must be Python source"):
        inspect_project(text)
