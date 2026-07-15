"""Typed context and artifact metadata shared by reports and comparisons."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, NonNegativeInt, PositiveInt, field_validator

from gauntlet.core.models import GauntletModel
from gauntlet.scoring import ReleaseRecommendation


class ExecutionMode(StrEnum):
    """Execution modes with distinct reproducibility claims."""

    DETERMINISTIC_FIXTURE = "deterministic_fixture"
    LIVE_SERVICE = "live_service"


class BenchmarkProvenance(GauntletModel):
    """Version identity required for honest cross-run comparison."""

    id: str
    version: str
    schema_version: PositiveInt

    @field_validator("id", "version")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("benchmark identity fields must be non-blank")
        return value


class ReportContext(GauntletModel):
    """Environment and provenance facts safe to show in the report."""

    benchmark_packs: list[BenchmarkProvenance] = Field(min_length=1)
    config_fingerprint: str
    environment_fingerprint: str
    gauntlet_version: str
    python_version: str
    platform: str
    seed: int | None
    execution_mode: ExecutionMode
    isolation_level: str

    @field_validator(
        "config_fingerprint",
        "environment_fingerprint",
        "gauntlet_version",
        "python_version",
        "platform",
        "isolation_level",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("report context fields must be non-blank")
        return value


class RunSummary(GauntletModel):
    """Comparison-critical manifest summary published with a completed run."""

    artifact_schema_version: Literal[1] = 1
    scenarios_completed: NonNegativeInt
    benchmark_packs: list[BenchmarkProvenance] = Field(min_length=1)
    config_fingerprint: str
    execution_mode: ExecutionMode
    isolation_level: str
    release_recommendation: ReleaseRecommendation
    applied_policy_rules: list[str]

    @field_validator("config_fingerprint", "isolation_level")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("run summary fields must be non-blank")
        return value

    @field_validator("applied_policy_rules")
    @classmethod
    def validate_rules(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("applied_policy_rules must contain non-blank rule IDs")
        if len(set(value)) != len(value):
            raise ValueError("applied_policy_rules must contain unique rule IDs")
        return value


class ReportArtifacts(GauntletModel):
    """Fixed canonical files written for one completed evaluation."""

    results: Path
    scorecard: Path
    findings: Path
    markdown: Path
