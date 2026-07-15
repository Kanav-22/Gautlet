"""Offline GAUNTLET environment-doctor tests."""

import socket
from pathlib import Path
from types import ModuleType

import pytest

import gauntlet.discovery.doctor as doctor_module
from gauntlet.discovery import DoctorStatus, locate_builtin_agent_mvp, run_doctor

EXPECTED_CHECKS = [
    "python_version",
    "operating_system",
    "runtime_imports",
    "child_process",
    "artifact_root",
    "default_policy",
    "builtin_benchmark",
]


def test_doctor_passes_all_required_checks_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A network attempt would fail the test; doctor checks only local runtime state.
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("doctor attempted network access")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    benchmark = locate_builtin_agent_mvp()
    assert benchmark is not None

    result = run_doctor(
        artifact_root=tmp_path / "artifacts",
        benchmark_root=benchmark,
        python_version=(3, 11, 9),
        platform_name="Linux",
    )

    assert result.ok
    assert [check.id for check in result.checks] == EXPECTED_CHECKS
    assert all(check.status is DoctorStatus.PASS for check in result.checks)
    assert not (tmp_path / "artifacts").exists()


def test_doctor_reports_unsupported_python_with_remediation(tmp_path: Path) -> None:
    result = run_doctor(
        artifact_root=tmp_path / "artifacts",
        benchmark_root=locate_builtin_agent_mvp(),
        python_version=(3, 10, 14),
        platform_name="Linux",
    )

    assert not result.ok
    check = next(check for check in result.checks if check.id == "python_version")
    assert check.status is DoctorStatus.FAIL
    assert "requires Python >=3.11" in check.message
    assert check.action is not None
    assert "Python 3.11" in check.action


def test_doctor_reports_runtime_import_failure_actionably(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = doctor_module._import_runtime_module

    def selective_import(name: str) -> ModuleType:
        if name == "rich":
            raise ImportError("test missing dependency")
        return original(name)

    monkeypatch.setattr(doctor_module, "_import_runtime_module", selective_import)

    result = run_doctor(
        artifact_root=tmp_path / "artifacts",
        benchmark_root=locate_builtin_agent_mvp(),
    )

    check = next(check for check in result.checks if check.id == "runtime_imports")
    assert check.status is DoctorStatus.FAIL
    assert "rich (ImportError: test missing dependency)" in check.message
    assert check.action is not None
    assert "Reinstall GAUNTLET" in check.action


def test_doctor_reports_unwritable_artifact_root(tmp_path: Path) -> None:
    not_a_directory = tmp_path / "artifact-file"
    not_a_directory.write_text("occupied", encoding="utf-8")

    result = run_doctor(
        artifact_root=not_a_directory,
        benchmark_root=locate_builtin_agent_mvp(),
    )

    check = next(check for check in result.checks if check.id == "artifact_root")
    assert check.status is DoctorStatus.FAIL
    assert "failed an atomic write check" in check.message
    assert check.action is not None
    assert "atomic file replacement" in check.action


def test_doctor_reports_missing_builtin_benchmark(tmp_path: Path) -> None:
    result = run_doctor(
        artifact_root=tmp_path / "artifacts",
        benchmark_root=tmp_path / "missing-benchmark",
    )

    check = next(check for check in result.checks if check.id == "builtin_benchmark")
    assert check.status is DoctorStatus.FAIL
    assert "unavailable or invalid" in check.message
    assert check.action is not None
    assert "flagship benchmark" in check.action
