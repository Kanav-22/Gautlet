"""Filesystem-backed storage for GAUNTLET run artifacts."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias

from pydantic import JsonValue, ValidationError

from gauntlet import __version__
from gauntlet.config.loader import save_resolved_config
from gauntlet.config.models import GauntletConfig
from gauntlet.core.models import (
    EvaluationRun,
    EvaluationRunStatus,
    Finding,
    ScenarioResult,
    ScoreCard,
)

DEFAULT_ARTIFACT_ROOT = Path.home() / ".gauntlet" / "artifacts"
RUN_ID_PATTERN = re.compile(r"^run_\d{8}_\d{6}_[0-9a-f]{8}$")
_RUN_DIRECTORIES = ("logs", "traces", "evidence", "scenarios")

Clock: TypeAlias = Callable[[], datetime]
NonceFactory: TypeAlias = Callable[[], str]


class ArtifactStoreError(RuntimeError):
    """Base exception for artifact-store failures."""


class InvalidRunIdError(ArtifactStoreError):
    """Raised when a run ID is not in the canonical safe format."""


class RunNotFoundError(ArtifactStoreError):
    """Raised when a requested run manifest does not exist."""


class ArtifactCorruptionError(ArtifactStoreError):
    """Raised when a stored manifest cannot be loaded safely."""

    def __init__(self, run_id: str, manifest_path: Path, reason: str) -> None:
        self.run_id = run_id
        self.manifest_path = manifest_path
        self.reason = reason
        super().__init__(f"{run_id} ({manifest_path}): {reason}")


@dataclass(frozen=True, slots=True)
class RunScanProblem:
    """A corrupt or unsafe manifest found during a filesystem scan."""

    run_id: str
    manifest_path: Path
    reason: str

    def __str__(self) -> str:
        return f"{self.run_id} ({self.manifest_path}): {self.reason}"


@dataclass(frozen=True, slots=True)
class RunScanResult:
    """Valid runs and explicit problems discovered by a scan."""

    runs: tuple[EvaluationRun, ...]
    problems: tuple[RunScanProblem, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _nonce() -> str:
    return secrets.token_hex(4)


def _json_text(payload: object) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def _filesystem_path(path: Path) -> Path:
    """Use an extended-length absolute path for Windows filesystem calls."""

    if os.name != "nt":
        return path
    value = str(path.resolve())
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def _atomic_write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    destination = _filesystem_path(path)
    temporary = destination.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")

    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, destination)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 4:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        for attempt in range(5):
            try:
                temporary.unlink(missing_ok=True)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 4:
                    raise
                time.sleep(0.01 * (attempt + 1))

    return path


class RunArtifactStore:
    """Create, inspect, and update canonical run artifact directories."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        clock: Clock | None = None,
        nonce_factory: NonceFactory | None = None,
    ) -> None:
        self.root = Path(root).expanduser() if root is not None else DEFAULT_ARTIFACT_ROOT
        self.runs_root = self.root / "runs"
        self._clock = clock or _utc_now
        self._nonce_factory = nonce_factory or _nonce

    @staticmethod
    def validate_run_id(run_id: str) -> str:
        """Validate and return a canonical run ID."""
        if RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise InvalidRunIdError("Run ID must match run_YYYYMMDD_HHMMSS_<8 lowercase hex>")
        return run_id

    def generate_run_id(self, at: datetime | None = None) -> str:
        """Generate a canonical UTC run ID using injectable entropy."""
        timestamp = at if at is not None else self._clock()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        timestamp = timestamp.astimezone(UTC)

        nonce = self._nonce_factory().lower()
        if re.fullmatch(r"[0-9a-f]{8}", nonce) is None:
            raise InvalidRunIdError(
                "Run ID nonce factory must return exactly 8 hexadecimal characters"
            )

        return f"run_{timestamp:%Y%m%d_%H%M%S}_{nonce}"

    def run_dir(self, run_id: str) -> Path:
        """Return the canonical directory for a validated run ID."""
        self.validate_run_id(run_id)
        return self.runs_root / run_id

    def create_run(
        self,
        *,
        project_id: str,
        profile_id: str,
        benchmark_pack_ids: Sequence[str],
        environment_fingerprint: str,
        environment: Mapping[str, JsonValue],
        resolved_config: GauntletConfig,
        seed: int | None = None,
        gauntlet_version: str = __version__,
        plugin_versions: Mapping[str, JsonValue] | None = None,
        summary: Mapping[str, JsonValue] | None = None,
        started_at: datetime | None = None,
    ) -> EvaluationRun:
        """Create and persist a new pending run."""
        timestamp = started_at if started_at is not None else self._clock()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        timestamp = timestamp.astimezone(UTC)

        manifest = EvaluationRun(
            id=self.generate_run_id(timestamp),
            project_id=project_id,
            profile_id=profile_id,
            benchmark_pack_ids=list(benchmark_pack_ids),
            started_at=timestamp,
            finished_at=None,
            status=EvaluationRunStatus.PENDING,
            seed=seed,
            environment_fingerprint=environment_fingerprint,
            gauntlet_version=gauntlet_version,
            plugin_versions=dict(plugin_versions or {}),
            summary=dict(summary or {}),
        )
        self.initialize_run(manifest, environment, resolved_config)
        return manifest

    def initialize_run(
        self,
        manifest: EvaluationRun,
        environment: Mapping[str, JsonValue],
        resolved_config: GauntletConfig,
    ) -> Path:
        """Initialize the artifact tree for an already constructed pending run."""
        self.validate_run_id(manifest.id)
        if manifest.status is not EvaluationRunStatus.PENDING:
            raise ArtifactStoreError("A new run must have pending status")
        if manifest.finished_at is not None:
            raise ArtifactStoreError("A new pending run cannot have finished_at")

        self.runs_root.mkdir(parents=True, exist_ok=True)
        run_dir = self.run_dir(manifest.id)
        try:
            run_dir.mkdir()
        except FileExistsError as error:
            raise ArtifactStoreError(f"Run already exists: {manifest.id}") from error

        for directory in _RUN_DIRECTORIES:
            (run_dir / directory).mkdir()

        _atomic_write_text(
            run_dir / "environment.json",
            _json_text(dict(environment)),
        )
        save_resolved_config(resolved_config, run_dir)

        # Publish the manifest last so scans do not observe a run before its
        # mandatory supporting artifacts exist.
        _atomic_write_text(
            run_dir / "manifest.json",
            _json_text(manifest.model_dump(mode="json")),
        )
        return run_dir

    def load_manifest(self, run_id: str) -> EvaluationRun:
        """Load the exact typed manifest for a run."""
        self.validate_run_id(run_id)
        run_dir = self.run_dir(run_id)
        manifest_path = run_dir / "manifest.json"
        if run_dir.is_symlink() or manifest_path.is_symlink():
            raise ArtifactCorruptionError(
                run_id,
                manifest_path,
                "symlinked run directories and manifests are not allowed",
            )
        if not manifest_path.is_file():
            raise RunNotFoundError(f"Run manifest not found: {run_id}")

        try:
            content = manifest_path.read_text(encoding="utf-8")
            manifest = EvaluationRun.model_validate_json(content)
        except (OSError, UnicodeError, ValidationError, ValueError) as error:
            raise ArtifactCorruptionError(
                run_id,
                manifest_path,
                f"invalid manifest: {error}",
            ) from error

        if manifest.id != run_id:
            raise ArtifactCorruptionError(
                run_id,
                manifest_path,
                f"manifest ID {manifest.id!r} does not match directory",
            )
        return manifest

    def scan(self) -> RunScanResult:
        """Scan only immediate runs/*/manifest.json files."""
        if not self.runs_root.is_dir():
            return RunScanResult(runs=(), problems=())

        runs: list[EvaluationRun] = []
        problems: list[RunScanProblem] = []

        for manifest_path in sorted(self.runs_root.glob("*/manifest.json")):
            if not manifest_path.is_file():
                continue

            run_id = manifest_path.parent.name
            try:
                runs.append(self.load_manifest(run_id))
            except ArtifactCorruptionError as error:
                problems.append(
                    RunScanProblem(
                        run_id=run_id,
                        manifest_path=manifest_path,
                        reason=error.reason,
                    )
                )
            except ArtifactStoreError as error:
                problems.append(
                    RunScanProblem(
                        run_id=run_id,
                        manifest_path=manifest_path,
                        reason=str(error),
                    )
                )

        runs.sort(key=lambda run: run.id, reverse=True)
        problems.sort(key=lambda problem: problem.run_id)
        return RunScanResult(runs=tuple(runs), problems=tuple(problems))

    def write_manifest(self, manifest: EvaluationRun) -> Path:
        """Atomically replace an existing run manifest."""
        self.load_manifest(manifest.id)
        return _atomic_write_text(
            self.run_dir(manifest.id) / "manifest.json",
            _json_text(manifest.model_dump(mode="json")),
        )

    def write_json(
        self,
        run_id: str,
        relative_path: str | Path,
        payload: object,
    ) -> Path:
        """Atomically write a contained JSON artifact for an existing run."""
        self.load_manifest(run_id)
        run_dir = self.run_dir(run_id)

        relative = Path(relative_path)
        if (
            relative.is_absolute()
            or relative.drive
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.suffix.lower() != ".json"
        ):
            raise ArtifactStoreError("Artifact path must be a contained relative .json path")

        base = run_dir.resolve()
        destination = (run_dir / relative).resolve()
        try:
            destination.relative_to(base)
        except ValueError as error:
            raise ArtifactStoreError("Artifact path escapes the canonical run directory") from error

        return _atomic_write_text(destination, _json_text(payload))

    def write_results(
        self,
        run_id: str,
        results: Sequence[ScenarioResult],
    ) -> Path:
        """Write completed scenario results."""
        return self.write_json(
            run_id,
            "results.json",
            [result.model_dump(mode="json") for result in results],
        )

    def write_scorecard(self, run_id: str, scorecard: ScoreCard) -> Path:
        """Write a completed scorecard."""
        return self.write_json(
            run_id,
            "scorecard.json",
            scorecard.model_dump(mode="json"),
        )

    def write_findings(
        self,
        run_id: str,
        findings: Sequence[Finding],
    ) -> Path:
        """Write completed findings."""
        return self.write_json(
            run_id,
            "findings.json",
            [finding.model_dump(mode="json") for finding in findings],
        )

    def write_report(self, run_id: str, markdown: str) -> Path:
        """Atomically write the fixed human-readable report artifact."""

        self.load_manifest(run_id)
        if "\x00" in markdown:
            raise ArtifactStoreError("Markdown report must not contain null bytes")
        content = markdown if markdown.endswith("\n") else markdown + "\n"
        return _atomic_write_text(self.run_dir(run_id) / "report.md", content)
