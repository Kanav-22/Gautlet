"""Validated benchmark scoring policies and the built-in MVP policy."""

from __future__ import annotations

import math
from importlib.resources import files
from pathlib import Path
from typing import Annotated, cast

import yaml
from pydantic import Field, PositiveInt, ValidationError, field_validator, model_validator

from gauntlet.core.models import DimensionName, FindingSeverity, GauntletModel

Weight = Annotated[float, Field(gt=0, le=1)]
ScoreThreshold = Annotated[float, Field(ge=0, le=100)]
_UNSCORED_MVP_DIMENSIONS = {DimensionName.COST, DimensionName.MAINTAINABILITY}


class ScoringPolicyError(ValueError):
    """Raised when a scoring policy is missing, malformed, or unsafe."""


class PolicyCaps(GauntletModel):
    """Hard post-weighting caps from the documented MVP policy."""

    critical_security_finding: ScoreThreshold
    task_success_below_50_percent: ScoreThreshold


class PolicyMinimums(GauntletModel):
    """Minimum evidence required for a conclusive recommendation."""

    scenarios_completed: PositiveInt


class RecommendationPolicy(GauntletModel):
    """Explicit release bands added where the specification is silent."""

    ready_score: ScoreThreshold = 80
    passing_score: ScoreThreshold = 60
    warning_severities: list[FindingSeverity] = Field(
        default_factory=lambda: [
            FindingSeverity.LOW,
            FindingSeverity.MEDIUM,
            FindingSeverity.HIGH,
            FindingSeverity.CRITICAL,
        ]
    )

    @field_validator("warning_severities")
    @classmethod
    def validate_warning_severities(cls, value: list[FindingSeverity]) -> list[FindingSeverity]:
        if len(set(value)) != len(value):
            raise ValueError("warning_severities must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_bands(self) -> RecommendationPolicy:
        if self.passing_score > self.ready_score:
            raise ValueError("passing_score must not exceed ready_score")
        return self


class ScoringPolicy(GauntletModel):
    """Transparent weights, caps, minimums, and recommendation bands."""

    id: str
    weights: dict[DimensionName, Weight]
    caps: PolicyCaps
    minimums: PolicyMinimums
    recommendation: RecommendationPolicy = Field(default_factory=RecommendationPolicy)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("policy id must be non-blank")
        return value

    @model_validator(mode="after")
    def validate_weights(self) -> ScoringPolicy:
        if not self.weights:
            raise ValueError("weights must contain at least one scored dimension")
        unsupported = sorted(
            dimension.value for dimension in set(self.weights) & _UNSCORED_MVP_DIMENSIONS
        )
        if unsupported:
            raise ValueError(
                "MVP policy cannot score deferred dimensions: " + ", ".join(unsupported)
            )
        if not math.isclose(sum(self.weights.values()), 1.0, rel_tol=0, abs_tol=1e-9):
            raise ValueError("scoring weights must sum to 1")
        return self


def load_scoring_policy(path: Path | str) -> ScoringPolicy:
    """Load one strict UTF-8 YAML policy with actionable failures."""

    policy_path = Path(path)
    try:
        raw = cast(object, yaml.safe_load(policy_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ScoringPolicyError(f"Unable to load scoring policy {policy_path}: {error}") from error
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise ScoringPolicyError(f"Scoring policy {policy_path} must contain a YAML mapping")
    try:
        return ScoringPolicy.model_validate(raw)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or '<root>'}: {issue['msg']}"
            for issue in error.errors(include_url=False)
        )
        raise ScoringPolicyError(
            f"Scoring policy validation failed for {policy_path}: {details}"
        ) from error


def agent_mvp_default_policy() -> ScoringPolicy:
    """Load the packaged policy matching the specification's example."""

    resource = files("gauntlet.scoring").joinpath("policies/agent_mvp_default.yaml")
    try:
        raw = cast(object, yaml.safe_load(resource.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError) as error:  # pragma: no cover - packaging defect
        raise ScoringPolicyError(
            f"Unable to load packaged agent_mvp_default policy: {error}"
        ) from error
    try:
        return ScoringPolicy.model_validate(raw)
    except ValidationError as error:  # pragma: no cover - packaging defect
        raise ScoringPolicyError(
            f"Packaged agent_mvp_default policy is invalid: {error}"
        ) from error
