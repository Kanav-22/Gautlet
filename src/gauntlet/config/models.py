"""Strict resolved-configuration models."""

from enum import StrEnum
from pathlib import Path

from pydantic import PositiveFloat, PositiveInt

from gauntlet.core.models import GauntletModel


class NetworkPolicy(StrEnum):
    """Network access policy for evaluated processes."""

    DISABLED = "disabled"
    ENABLED = "enabled"


class IsolationMode(StrEnum):
    """Supported MVP execution isolation modes."""

    SUBPROCESS = "subprocess"


class ReportFormat(StrEnum):
    """Supported MVP report formats."""

    JSON = "json"
    MARKDOWN = "markdown"


class ProjectConfig(GauntletModel):
    """Project identity settings."""

    name: str


class AdapterConfig(GauntletModel):
    """System-under-test adapter settings."""

    type: str
    target: str


class EvaluationConfig(GauntletModel):
    """Scenario execution settings."""

    benchmark_packs: list[str]
    seed: int | None
    repeat: PositiveInt
    timeout_seconds: PositiveFloat


class ExecutionConfig(GauntletModel):
    """Execution boundary settings."""

    network: NetworkPolicy
    isolation: IsolationMode


class ReportingConfig(GauntletModel):
    """Requested report outputs."""

    formats: list[ReportFormat]


class ScoringConfig(GauntletModel):
    """Scoring policy selection."""

    policy: str


class ArtifactsConfig(GauntletModel):
    """Filesystem-backed run artifact settings."""

    root: Path


class GauntletConfig(GauntletModel):
    """Fully resolved GAUNTLET configuration."""

    project: ProjectConfig
    adapter: AdapterConfig
    evaluation: EvaluationConfig
    execution: ExecutionConfig
    reporting: ReportingConfig
    scoring: ScoringConfig
    artifacts: ArtifactsConfig
