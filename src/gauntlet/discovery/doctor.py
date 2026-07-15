"""Entirely offline environment checks for the GAUNTLET MVP."""

from __future__ import annotations

import importlib
import os
import platform
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from types import ModuleType

from gauntlet.benchmarks import BenchmarkPackError, load_benchmark_pack
from gauntlet.evidence.store import DEFAULT_ARTIFACT_ROOT
from gauntlet.scoring import ScoringPolicyError, agent_mvp_default_policy

_REQUIRED_RUNTIME_IMPORTS = ("jsonschema", "pydantic", "rich", "typer", "yaml")
_SUPPORTED_SYSTEMS = frozenset({"Darwin", "Linux", "Windows"})
_CHILD_SENTINEL = "gauntlet-doctor-child-ok"


class DoctorStatus(StrEnum):
    """Stable status values for individual environment checks."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One offline health check with remediation on non-pass results."""

    id: str
    status: DoctorStatus
    message: str
    action: str | None = None


@dataclass(frozen=True, slots=True)
class DoctorResult:
    """Ordered environment-health result returned to the CLI."""

    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        """Whether every required check passed or only warned."""

        return all(check.status is not DoctorStatus.FAIL for check in self.checks)


def locate_builtin_agent_mvp() -> Path | None:
    """Locate the installed or source-checkout flagship benchmark without I/O beyond disk."""

    candidates: list[Path] = []
    try:
        package_root = files("gauntlet")
        candidates.extend(
            [
                Path(str(package_root.joinpath("benchmarks/agent_mvp"))),
                Path(str(package_root.joinpath("_data/benchmarks/agent_mvp"))),
            ]
        )
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - broken installation
        pass
    candidates.append(Path(__file__).resolve().parents[3] / "benchmarks" / "agent_mvp")
    for candidate in candidates:
        if (candidate / "manifest.yaml").is_file():
            return candidate.resolve()
    return None


def _pass(check_id: str, message: str) -> DoctorCheck:
    return DoctorCheck(id=check_id, status=DoctorStatus.PASS, message=message)


def _fail(check_id: str, message: str, action: str) -> DoctorCheck:
    return DoctorCheck(id=check_id, status=DoctorStatus.FAIL, message=message, action=action)


def _check_python(version: tuple[int, int, int]) -> DoctorCheck:
    display = ".".join(str(part) for part in version)
    if version >= (3, 11, 0):
        return _pass("python_version", f"Python {display} satisfies the >=3.11 requirement.")
    return _fail(
        "python_version",
        f"Python {display} is unsupported; GAUNTLET requires Python >=3.11.",
        "Install Python 3.11 or newer and reinstall GAUNTLET in that environment.",
    )


def _check_operating_system(system: str) -> DoctorCheck:
    detail = f"{system} ({platform.machine() or 'unknown architecture'})"
    if system in _SUPPORTED_SYSTEMS:
        return _pass("operating_system", f"Operating system detected: {detail}.")
    return _fail(
        "operating_system",
        f"Unsupported operating system detected: {detail}.",
        "Run GAUNTLET on Linux, macOS, or Windows.",
    )


def _import_runtime_module(name: str) -> ModuleType:
    return importlib.import_module(name)


def _check_runtime_imports() -> DoctorCheck:
    failures: list[str] = []
    for name in _REQUIRED_RUNTIME_IMPORTS:
        try:
            _import_runtime_module(name)
        except Exception as error:  # pragma: no cover - exercised through the helper in tests
            failures.append(f"{name} ({type(error).__name__}: {error})")
    if not failures:
        return _pass(
            "runtime_imports",
            "Required runtime packages import successfully: "
            + ", ".join(_REQUIRED_RUNTIME_IMPORTS)
            + ".",
        )
    return _fail(
        "runtime_imports",
        "Required runtime imports failed: " + "; ".join(failures) + ".",
        "Reinstall GAUNTLET and its runtime dependencies in the active environment.",
    )


def _child_environment() -> dict[str, str]:
    allowed = ("COMSPEC", "PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "WINDIR")
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _check_child_process() -> DoctorCheck:
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", f"print({_CHILD_SENTINEL!r})"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=5,
            env=_child_environment(),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as error:
        return _fail(
            "child_process",
            f"Unable to launch an isolated Python child process: {type(error).__name__}: {error}.",
            "Check OS process policy, Python executable permissions, and endpoint security rules.",
        )
    if completed.returncode == 0 and completed.stdout.strip() == _CHILD_SENTINEL:
        return _pass("child_process", "An isolated Python child process launched successfully.")
    detail = completed.stderr.strip() or completed.stdout.strip() or "no child output"
    return _fail(
        "child_process",
        f"Python child process exited {completed.returncode}: {detail}.",
        "Check OS process policy, Python executable permissions, and endpoint security rules.",
    )


def _check_artifact_root(root: Path) -> DoctorCheck:
    artifact_root = root.expanduser()
    doctor_directory = artifact_root / f".doctor-{secrets.token_hex(8)}"
    source = doctor_directory / ".atomic.tmp"
    destination = doctor_directory / "atomic.txt"
    created_root = not artifact_root.exists()
    try:
        doctor_directory.mkdir(parents=True, exist_ok=False)
        with source.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_CHILD_SENTINEL + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(source, destination)
        if destination.read_text(encoding="utf-8") != _CHILD_SENTINEL + "\n":
            raise OSError("atomic replacement content did not round-trip")
    except (OSError, UnicodeError) as error:
        return _fail(
            "artifact_root",
            f"Artifact root {artifact_root} failed an atomic write check: {type(error).__name__}: {error}.",
            "Choose a writable local artifact root that supports atomic file replacement.",
        )
    finally:
        shutil.rmtree(doctor_directory, ignore_errors=True)
        if created_root:
            try:
                artifact_root.rmdir()
            except OSError:
                pass
    return _pass("artifact_root", f"Artifact root supports atomic writes: {artifact_root}.")


def _check_default_policy() -> DoctorCheck:
    try:
        policy = agent_mvp_default_policy()
    except (ScoringPolicyError, OSError, ValueError) as error:
        return _fail(
            "default_policy",
            f"Packaged default scoring policy is unavailable or invalid: {error}.",
            "Reinstall GAUNTLET from a complete distribution.",
        )
    if policy.id != "agent_mvp_default":  # pragma: no cover - schema-valid packaging defect
        return _fail(
            "default_policy",
            f"Packaged default policy has unexpected id {policy.id!r}.",
            "Reinstall GAUNTLET from a complete distribution.",
        )
    return _pass("default_policy", "Packaged scoring policy agent_mvp_default is valid.")


def _check_builtin_benchmark(root: Path | None) -> DoctorCheck:
    if root is None:
        return _fail(
            "builtin_benchmark",
            "Built-in benchmark gauntlet.agent.mvp is not installed.",
            "Reinstall GAUNTLET from a complete distribution containing the flagship benchmark.",
        )
    try:
        benchmark = load_benchmark_pack(root)
    except (BenchmarkPackError, OSError, ValueError) as error:
        return _fail(
            "builtin_benchmark",
            f"Built-in benchmark at {root} is unavailable or invalid: {error}.",
            "Reinstall GAUNTLET or repair the packaged flagship benchmark.",
        )
    if benchmark.identity.id != "gauntlet.agent.mvp":
        return _fail(
            "builtin_benchmark",
            f"Built-in benchmark has unexpected id {benchmark.identity.id!r}.",
            "Reinstall GAUNTLET from a complete distribution.",
        )
    return _pass(
        "builtin_benchmark",
        f"Built-in benchmark {benchmark.identity.id} {benchmark.identity.version} is valid "
        f"({len(benchmark.scenarios)} scenarios).",
    )


def run_doctor(
    *,
    artifact_root: Path | str | None = None,
    benchmark_root: Path | str | None = None,
    python_version: tuple[int, int, int] | None = None,
    platform_name: str | None = None,
) -> DoctorResult:
    """Run required local checks without network access or user-code imports."""

    selected_artifact_root = (
        Path(artifact_root) if artifact_root is not None else DEFAULT_ARTIFACT_ROOT
    )
    selected_benchmark_root = (
        Path(benchmark_root) if benchmark_root is not None else locate_builtin_agent_mvp()
    )
    selected_version = python_version if python_version is not None else sys.version_info[:3]
    selected_platform = platform_name if platform_name is not None else platform.system()
    checks = (
        _check_python(selected_version),
        _check_operating_system(selected_platform),
        _check_runtime_imports(),
        _check_child_process(),
        _check_artifact_root(selected_artifact_root),
        _check_default_policy(),
        _check_builtin_benchmark(selected_benchmark_root),
    )
    return DoctorResult(checks=checks)
