"""Strict, context-aware comparison of completed GAUNTLET run bundles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from statistics import fmean
from typing import Never, cast

from pydantic import JsonValue, ValidationError, field_validator

from gauntlet.core.models import (
    DimensionName,
    EvaluationRun,
    EvaluationRunStatus,
    GauntletModel,
    ScenarioResult,
    ScenarioResultStatus,
    ScoreCard,
)
from gauntlet.evidence import RunArtifactStore
from gauntlet.reporting.models import ExecutionMode, RunSummary


class ComparisonInputError(ValueError):
    """Run selection is invalid before artifact comparison begins."""


class ComparisonArtifactError(RuntimeError):
    """A selected run is incomplete or its fixed artifacts are corrupt."""


class RegressionAssessment(StrEnum):
    """Evidence-supported comparison conclusions."""

    REGRESSION = "regression"
    NO_REGRESSION = "no_regression"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_DATA = "insufficient_data"


class ContextChangeKind(StrEnum):
    """Context changes that invalidate direct regression classification."""

    CONFIGURATION = "configuration"
    BENCHMARK = "benchmark"
    ENVIRONMENT = "environment"


class ContextChange(GauntletModel):
    """One explicit reason two runs are not directly comparable."""

    kind: ContextChangeKind
    before: JsonValue
    after: JsonValue
    details: str

    @field_validator("details")
    @classmethod
    def validate_details(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("context change details must be non-blank")
        return value


class NumericDelta(GauntletModel):
    """A numeric B-minus-A delta with an honest zero-baseline percentage."""

    before: float
    after: float
    absolute: float
    percent: float | None


class RunComparison(GauntletModel):
    """Normalized raw deltas plus a context-aware regression assessment."""

    run_a: str
    run_b: str
    assessment: RegressionAssessment
    context_changes: list[ContextChange]
    overall_score: NumericDelta
    dimension_scores: dict[DimensionName, NumericDelta]
    fixed_failures: list[str]
    new_failures: list[str]
    persistent_failures: list[str]
    latency_ms: NumericDelta
    cost_usd: NumericDelta | None
    reasons: list[str]

    @field_validator("reasons")
    @classmethod
    def validate_reasons(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("comparison reasons must contain non-blank text")
        return value


@dataclass(frozen=True, slots=True)
class _LoadedRun:
    manifest: EvaluationRun
    summary: RunSummary
    results: tuple[ScenarioResult, ...]
    scorecard: ScoreCard


class RunComparisonService:
    """Load two canonical run bundles and compare B against A."""

    def __init__(self, run_store: RunArtifactStore) -> None:
        self.run_store = run_store

    def compare(self, run_a: str, run_b: str) -> RunComparison:
        """Return exact raw deltas and a conservative regression assessment."""

        if run_a == run_b:
            raise ComparisonInputError("RUN_A and RUN_B must identify different runs")
        before = self._load(run_a)
        after = self._load(run_b)
        context_changes = self._context_changes(before, after)

        before_ids = {result.scenario_id for result in before.results}
        after_ids = {result.scenario_id for result in after.results}
        benchmark_changed = any(
            change.kind is ContextChangeKind.BENCHMARK for change in context_changes
        )
        if before_ids != after_ids and not benchmark_changed:
            raise ComparisonArtifactError(
                "Completed runs have different scenario sets without a benchmark change"
            )
        common_ids = before_ids & after_ids
        if not common_ids:
            raise ComparisonArtifactError("Runs have no common scenarios to compare")

        before_failures = _failure_ids(before.results)
        after_failures = _failure_ids(after.results)
        fixed_failures = sorted(before_failures - after_failures)
        new_failures = sorted(after_failures - before_failures)
        persistent_failures = sorted(before_failures & after_failures)

        overall = _delta(before.scorecard.overall, after.scorecard.overall)
        common_dimensions = sorted(
            set(before.scorecard.dimensions) & set(after.scorecard.dimensions),
            key=lambda dimension: dimension.value,
        )
        dimension_scores = {
            dimension: _delta(
                before.scorecard.dimensions[dimension].score,
                after.scorecard.dimensions[dimension].score,
            )
            for dimension in common_dimensions
        }
        before_by_id = {result.scenario_id: result for result in before.results}
        after_by_id = {result.scenario_id: result for result in after.results}
        before_latency = fmean(before_by_id[item].duration_ms for item in common_ids)
        after_latency = fmean(after_by_id[item].duration_ms for item in common_ids)
        latency = _delta(before_latency, after_latency)
        before_cost = _run_cost(before.results)
        after_cost = _run_cost(after.results)
        cost = (
            _delta(before_cost, after_cost)
            if before_cost is not None and after_cost is not None
            else None
        )

        reasons: list[str] = []
        if context_changes:
            assessment = RegressionAssessment.NOT_COMPARABLE
            reasons.append(
                "Configuration, benchmark, or environment changed; raw deltas are not classified as regressions."
            )
        elif (
            before.summary.execution_mode is ExecutionMode.LIVE_SERVICE
            or after.summary.execution_mode is ExecutionMode.LIVE_SERVICE
        ):
            assessment = RegressionAssessment.INSUFFICIENT_DATA
            reasons.append(
                "Live-service comparison requires repeat distributions and a significance policy."
            )
        elif new_failures or overall.absolute < 0:
            assessment = RegressionAssessment.REGRESSION
            if new_failures:
                reasons.append(f"New scenario failures: {', '.join(new_failures)}")
            if overall.absolute < 0:
                reasons.append(f"Overall score decreased by {abs(overall.absolute):g} points.")
        else:
            assessment = RegressionAssessment.NO_REGRESSION
            if fixed_failures:
                reasons.append(f"Fixed scenario failures: {', '.join(fixed_failures)}")
            else:
                reasons.append("No new failures or overall score decrease were observed.")

        return RunComparison(
            run_a=run_a,
            run_b=run_b,
            assessment=assessment,
            context_changes=context_changes,
            overall_score=overall,
            dimension_scores=dimension_scores,
            fixed_failures=fixed_failures,
            new_failures=new_failures,
            persistent_failures=persistent_failures,
            latency_ms=latency,
            cost_usd=cost,
            reasons=reasons,
        )

    def _load(self, run_id: str) -> _LoadedRun:
        manifest = self.run_store.load_manifest(run_id)
        if manifest.status is not EvaluationRunStatus.COMPLETED or manifest.finished_at is None:
            raise ComparisonArtifactError(f"Run {run_id} is not a completed evaluation")
        try:
            summary = RunSummary.model_validate(manifest.summary)
        except ValidationError as error:
            raise ComparisonArtifactError(
                f"Run {run_id} has an invalid completed summary: {error}"
            ) from error
        if [pack.id for pack in summary.benchmark_packs] != manifest.benchmark_pack_ids:
            raise ComparisonArtifactError(
                f"Run {run_id} benchmark provenance does not match its manifest"
            )
        raw_results = _load_json(self.run_store.run_dir(run_id) / "results.json", run_id)
        if not isinstance(raw_results, list):
            raise ComparisonArtifactError(f"Run {run_id} results.json must contain a list")
        try:
            results = tuple(ScenarioResult.model_validate(item) for item in raw_results)
        except ValidationError as error:
            raise ComparisonArtifactError(
                f"Run {run_id} results.json is invalid: {error}"
            ) from error
        if not results:
            raise ComparisonArtifactError(f"Run {run_id} has no scenario results")
        scenario_ids = [result.scenario_id for result in results]
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ComparisonArtifactError(f"Run {run_id} has duplicate scenario results")

        raw_scorecard = _load_json(
            self.run_store.run_dir(run_id) / "scorecard.json",
            run_id,
        )
        try:
            scorecard = ScoreCard.model_validate(raw_scorecard)
        except ValidationError as error:
            raise ComparisonArtifactError(
                f"Run {run_id} scorecard.json is invalid: {error}"
            ) from error
        return _LoadedRun(
            manifest=manifest,
            summary=summary,
            results=results,
            scorecard=scorecard,
        )

    @staticmethod
    def _context_changes(before: _LoadedRun, after: _LoadedRun) -> list[ContextChange]:
        changes: list[ContextChange] = []
        configuration_before = {
            "config_fingerprint": before.summary.config_fingerprint,
            "execution_mode": before.summary.execution_mode.value,
            "isolation_level": before.summary.isolation_level,
        }
        configuration_after = {
            "config_fingerprint": after.summary.config_fingerprint,
            "execution_mode": after.summary.execution_mode.value,
            "isolation_level": after.summary.isolation_level,
        }
        if configuration_before != configuration_after:
            changes.append(
                ContextChange(
                    kind=ContextChangeKind.CONFIGURATION,
                    before=cast(JsonValue, configuration_before),
                    after=cast(JsonValue, configuration_after),
                    details="Resolved configuration, execution mode, or isolation level changed.",
                )
            )

        benchmark_before = [pack.model_dump(mode="json") for pack in before.summary.benchmark_packs]
        benchmark_after = [pack.model_dump(mode="json") for pack in after.summary.benchmark_packs]
        if benchmark_before != benchmark_after:
            changes.append(
                ContextChange(
                    kind=ContextChangeKind.BENCHMARK,
                    before=cast(JsonValue, benchmark_before),
                    after=cast(JsonValue, benchmark_after),
                    details="Benchmark pack identity, version, or schema version changed.",
                )
            )

        environment_before: dict[str, JsonValue] = {
            "environment_fingerprint": before.manifest.environment_fingerprint,
            "gauntlet_version": before.manifest.gauntlet_version,
            "plugin_versions": cast(JsonValue, before.manifest.plugin_versions),
        }
        environment_after: dict[str, JsonValue] = {
            "environment_fingerprint": after.manifest.environment_fingerprint,
            "gauntlet_version": after.manifest.gauntlet_version,
            "plugin_versions": cast(JsonValue, after.manifest.plugin_versions),
        }
        if environment_before != environment_after:
            changes.append(
                ContextChange(
                    kind=ContextChangeKind.ENVIRONMENT,
                    before=environment_before,
                    after=environment_after,
                    details="Environment fingerprint, GAUNTLET version, or plugin versions changed.",
                )
            )
        return changes


def format_run_comparison(comparison: RunComparison) -> str:
    """Render a concise CI-safe comparison summary."""

    lines = [
        f"Comparison: {comparison.run_a} -> {comparison.run_b} (delta = RUN_B - RUN_A)",
        f"Assessment: {comparison.assessment.value}",
        "Overall score: "
        f"{comparison.overall_score.before:.2f} -> {comparison.overall_score.after:.2f} "
        f"({_signed(comparison.overall_score.absolute)})",
        "Mean latency (ms): "
        f"{comparison.latency_ms.before:.2f} -> {comparison.latency_ms.after:.2f} "
        f"({_signed(comparison.latency_ms.absolute)})",
    ]
    if comparison.cost_usd is None:
        lines.append("Observed cost (USD): not reported")
    else:
        lines.append(
            "Observed cost (USD): "
            f"{comparison.cost_usd.before:.6f} -> {comparison.cost_usd.after:.6f} "
            f"({_signed(comparison.cost_usd.absolute, places=6)})"
        )
    if comparison.dimension_scores:
        lines.append("Dimension score deltas:")
        lines.extend(
            f"  {dimension.value}: {_signed(delta.absolute)}"
            for dimension, delta in comparison.dimension_scores.items()
        )
    lines.extend(
        [
            "Fixed failures: " + (", ".join(comparison.fixed_failures) or "none"),
            "New failures: " + (", ".join(comparison.new_failures) or "none"),
            "Persistent failures: " + (", ".join(comparison.persistent_failures) or "none"),
        ]
    )
    if comparison.context_changes:
        lines.append("Context changes:")
        lines.extend(
            f"  {change.kind.value}: {change.details}" for change in comparison.context_changes
        )
    lines.append("Reasons:")
    lines.extend(f"  {reason}" for reason in comparison.reasons)
    return "\n".join(lines)


def _load_json(path: Path, run_id: str) -> JsonValue:
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        raise ComparisonArtifactError(
            f"Run {run_id} required artifact is missing or unsafe: {path.name}"
        )
    try:
        text = path.read_text(encoding="utf-8")
        return cast(
            JsonValue,
            json.loads(
                text,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_constant,
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ComparisonArtifactError(
            f"Run {run_id} artifact {path.name} is invalid: {error}"
        ) from error


def _object_without_duplicates(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Never:
    raise ValueError(f"Non-finite JSON number is not allowed: {value}")


def _failure_ids(results: tuple[ScenarioResult, ...]) -> set[str]:
    return {
        result.scenario_id for result in results if result.status is not ScenarioResultStatus.PASSED
    }


def _run_cost(results: tuple[ScenarioResult, ...]) -> float | None:
    values: list[float] = []
    for result in results:
        usage = result.metrics.get("observed_usage")
        if not isinstance(usage, dict):
            return None
        cost = usage.get("cost_usd")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            return None
        values.append(float(cost))
    return sum(values) if values else None


def _delta(before: float, after: float) -> NumericDelta:
    absolute = after - before
    percent = None if before == 0 else 100.0 * absolute / before
    return NumericDelta(
        before=before,
        after=after,
        absolute=absolute,
        percent=percent,
    )


def _signed(value: float, *, places: int = 2) -> str:
    return f"{value:+.{places}f}"
