"""Typed context and artifact metadata shared by reports and comparisons."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveInt, field_validator, model_validator

from gauntlet.core.models import GauntletModel
from gauntlet.scoring import ReleaseRecommendation


class ExecutionMode(StrEnum):
    """Execution modes with distinct reproducibility claims."""

    DETERMINISTIC_FIXTURE = "deterministic_fixture"
    LIVE_SERVICE = "live_service"


class ReproducibilityClaim(StrEnum):
    """Mode-aware claims supported by persisted repeat evidence."""

    NOT_ASSESSED = "not_assessed"
    BYTE_IDENTICAL = "byte_identical"
    NON_REPRODUCIBLE = "non_reproducible"
    LIVE_VARIANCE_ONLY = "live_variance_only"


class LiveRepeatDistribution(GauntletModel):
    """Observed per-repeat facts for live mode without a determinism claim."""

    canonical_hashes: list[str] = Field(min_length=2)
    total_latency_ms: list[NonNegativeInt] = Field(min_length=2)
    task_success_rates: list[Annotated[float, Field(ge=0, le=1)] | None] = Field(min_length=2)
    observed_cost_usd: list[Annotated[float, Field(ge=0)]] | None = None

    @model_validator(mode="after")
    def validate_parallel_distributions(self) -> LiveRepeatDistribution:
        count = len(self.canonical_hashes)
        if len(self.total_latency_ms) != count or len(self.task_success_rates) != count:
            raise ValueError("live repeat distributions must have the same length")
        if self.observed_cost_usd is not None and len(self.observed_cost_usd) != count:
            raise ValueError("observed live cost distribution must match the repeat count")
        if any(not item.startswith("sha256:") for item in self.canonical_hashes):
            raise ValueError("live canonical hashes must use sha256 identifiers")
        return self


class ReproducibilityReport(GauntletModel):
    """Repeat count, claim, and direct evidence links shown in every report."""

    repeat_count: PositiveInt
    claim: ReproducibilityClaim
    evidence_refs: list[str] = Field(default_factory=list)
    live_distribution: LiveRepeatDistribution | None = None

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("reproducibility evidence refs must be non-blank")
        if len(set(value)) != len(value):
            raise ValueError("reproducibility evidence refs must be unique")
        return value

    @model_validator(mode="after")
    def validate_claim_evidence(self) -> ReproducibilityReport:
        if self.claim is ReproducibilityClaim.LIVE_VARIANCE_ONLY:
            if self.live_distribution is None:
                raise ValueError("live variance claims require observed repeat distributions")
            if len(self.live_distribution.canonical_hashes) != self.repeat_count:
                raise ValueError("live repeat distribution does not match repeat_count")
        elif self.live_distribution is not None:
            raise ValueError("live repeat distributions are only valid for live-service claims")
        return self


def _unassessed_reproducibility() -> ReproducibilityReport:
    return ReproducibilityReport(
        repeat_count=1,
        claim=ReproducibilityClaim.NOT_ASSESSED,
    )


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
    reproducibility: ReproducibilityReport = Field(default_factory=_unassessed_reproducibility)

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
    canonical_schema_version: Literal[1] = 1
    scenarios_completed: NonNegativeInt
    benchmark_packs: list[BenchmarkProvenance] = Field(min_length=1)
    config_fingerprint: str
    execution_mode: ExecutionMode
    isolation_level: str
    release_recommendation: ReleaseRecommendation
    applied_policy_rules: list[str]
    reproducibility: ReproducibilityReport = Field(default_factory=_unassessed_reproducibility)

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
    canonical: Path
    markdown: Path
