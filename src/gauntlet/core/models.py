"""Validated domain models for GAUNTLET evaluation data."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, NonNegativeInt, PositiveInt

JsonObject: TypeAlias = dict[str, JsonValue]
ScoreValue: TypeAlias = Annotated[float, Field(ge=0, le=100)]
ConfidenceValue: TypeAlias = Annotated[float, Field(ge=0, le=1)]


class GauntletModel(BaseModel):
    """Base model for strict, finite, versioned GAUNTLET schemas."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class EvaluationRunStatus(StrEnum):
    """Evaluation run lifecycle states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScenarioResultStatus(StrEnum):
    """Terminal scenario result states."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"


class EvidenceType(StrEnum):
    """Supported evidence artifact categories."""

    TRACE = "trace"
    STDOUT = "stdout"
    STDERR = "stderr"
    TOOL_CALL = "tool_call"
    ARTIFACT = "artifact"
    METRIC = "metric"
    EXCEPTION = "exception"
    JUDGE_OUTPUT = "judge_output"


class FindingSeverity(StrEnum):
    """Finding severity levels."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DimensionName(StrEnum):
    """Canonical superset of evaluation and finding dimensions."""

    CORRECTNESS = "correctness"
    RELIABILITY = "reliability"
    SECURITY = "security"
    PERFORMANCE = "performance"
    EFFICIENCY = "efficiency"
    COST = "cost"
    REPRODUCIBILITY = "reproducibility"
    MAINTAINABILITY = "maintainability"


class EvaluationRun(GauntletModel):
    """Manifest-level metadata for one evaluation run."""

    id: str
    project_id: str
    profile_id: str
    benchmark_pack_ids: list[str]
    started_at: datetime
    finished_at: datetime | None
    status: EvaluationRunStatus
    seed: int | None
    environment_fingerprint: str
    gauntlet_version: str
    plugin_versions: JsonObject
    summary: JsonObject


class Scenario(GauntletModel):
    """A validated benchmark scenario definition."""

    id: str
    title: str
    description: str
    category: str
    difficulty: int
    tags: list[str]
    required_capabilities: list[str]
    input: JsonObject
    fixtures: JsonObject
    execution_policy: JsonObject
    assertions: list[JsonObject]
    metrics: list[str]


class ScenarioResult(GauntletModel):
    """Normalized outcome and evidence links for a scenario."""

    scenario_id: str
    status: ScenarioResultStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: NonNegativeInt
    output: JsonObject | None
    error: JsonObject | None
    metrics: JsonObject
    evidence_refs: list[str]
    findings: list[str]


class Evidence(GauntletModel):
    """Stored evidence metadata."""

    id: str
    type: EvidenceType
    path: str
    content_hash: str
    redacted: bool
    metadata: JsonObject


class Finding(GauntletModel):
    """Evidence-backed evaluation finding."""

    id: str
    severity: FindingSeverity
    dimension: DimensionName
    title: str
    description: str
    evidence_refs: list[str]
    remediation: str | None
    confidence: ConfidenceValue


class DimensionScore(GauntletModel):
    """Score and confidence for one policy-selected dimension."""

    score: ScoreValue
    confidence: ConfidenceValue


class ScoreCard(GauntletModel):
    """Normalized evaluation scorecard."""

    overall: ScoreValue
    dimensions: dict[DimensionName, DimensionScore]
    confidence: ConfidenceValue
    policy_id: str


class BenchmarkPackManifest(GauntletModel):
    """Manifest for a versioned benchmark pack."""

    id: str
    version: str
    title: str
    description: str
    schema_version: PositiveInt
    required_capabilities: list[str]
    dimensions: list[DimensionName]
    scenarios: list[str]
    scoring_policy: str
