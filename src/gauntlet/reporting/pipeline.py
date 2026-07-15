"""Minimal library-level evaluation pipeline for scored M4 report bundles."""

from __future__ import annotations

import hashlib
import json
import platform
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import TypeAlias, cast

from pydantic import JsonValue

from gauntlet import __version__
from gauntlet.benchmarks import LoadedBenchmarkPack
from gauntlet.config.models import GauntletConfig, IsolationMode
from gauntlet.core.models import (
    DimensionName,
    EvaluationRun,
    EvaluationRunStatus,
    EvidenceType,
    Finding,
    FindingSeverity,
    Scenario,
    ScenarioResult,
)
from gauntlet.evidence import (
    ArtifactStoreError,
    EvidenceStore,
    RedactionError,
    RunArtifactStore,
    SecretRedactor,
)
from gauntlet.execution import (
    AssertionConfigurationError,
    AssertionEngine,
    AssertionResult,
    AssertionType,
    ExecutionPolicyError,
    PythonCallableAdapterFactory,
    ScenarioExecutor,
)
from gauntlet.metrics import MetricCollectionError, MetricName, ScenarioMetricCollector
from gauntlet.reporting.canonical import (
    CanonicalizationError,
    CanonicalRepeat,
    ScenarioEvaluationRecord,
    build_canonical_evaluation,
    build_canonical_repeat,
    canonical_repeat_digest,
)
from gauntlet.reporting.generator import ReportGenerationError, ReportGenerator
from gauntlet.reporting.models import (
    BenchmarkProvenance,
    ExecutionMode,
    LiveRepeatDistribution,
    ReportArtifacts,
    ReportContext,
    ReproducibilityClaim,
    ReproducibilityReport,
    RunSummary,
)
from gauntlet.scoring import (
    ReleaseRecommendation,
    ReproducibilityObservation,
    ScenarioScoreInput,
    ScoringEngine,
    ScoringError,
    ScoringOutcome,
    ScoringPolicy,
)

Clock: TypeAlias = Callable[[], datetime]


class EvaluationExitCode(IntEnum):
    """Documented CI-safe evaluation exit codes."""

    PASSED = 0
    POLICY_FAILED = 1
    CONFIGURATION_ERROR = 2
    EXECUTION_ERROR = 3
    SECURITY_BOUNDARY_VIOLATION = 4
    INCOMPLETE_EVALUATION = 5


class EvaluationPipelineError(RuntimeError):
    """Base pipeline failure with a stable CLI exit code."""

    exit_code: EvaluationExitCode


class EvaluationConfigurationError(EvaluationPipelineError):
    """The evaluation request is incompatible or invalid."""

    exit_code = EvaluationExitCode.CONFIGURATION_ERROR


class EvaluationExecutionError(EvaluationPipelineError):
    """The orchestration engine failed before producing complete results."""

    exit_code = EvaluationExitCode.EXECUTION_ERROR


class EvaluationSecurityError(EvaluationPipelineError):
    """A persistence or isolation security boundary could not be upheld."""

    exit_code = EvaluationExitCode.SECURITY_BOUNDARY_VIOLATION


class IncompleteEvaluationError(EvaluationPipelineError):
    """Required run artifacts are missing or corrupt."""

    exit_code = EvaluationExitCode.INCOMPLETE_EVALUATION


def exit_code_for_recommendation(
    recommendation: ReleaseRecommendation,
) -> EvaluationExitCode:
    """Map a completed scoring outcome to the documented CLI code."""

    if recommendation in {
        ReleaseRecommendation.READY,
        ReleaseRecommendation.READY_WITH_WARNINGS,
    }:
        return EvaluationExitCode.PASSED
    if recommendation is ReleaseRecommendation.NOT_READY:
        return EvaluationExitCode.POLICY_FAILED
    return EvaluationExitCode.INCOMPLETE_EVALUATION


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """All validated inputs needed for one local fixture-mode evaluation."""

    project_id: str
    profile_id: str
    benchmark: LoadedBenchmarkPack
    resolved_config: GauntletConfig
    project_root: Path
    environment_fingerprint: str
    environment: Mapping[str, JsonValue]
    policy: ScoringPolicy
    findings: tuple[Finding, ...] = ()
    reproducibility: ReproducibilityObservation | None = None
    execution_mode: ExecutionMode = ExecutionMode.DETERMINISTIC_FIXTURE


@dataclass(frozen=True, slots=True)
class EvaluationPipelineResult:
    """Completed run and artifacts returned to library callers."""

    run: EvaluationRun
    results: tuple[ScenarioResult, ...]
    scoring: ScoringOutcome
    reproducibility: ReproducibilityReport
    artifacts: ReportArtifacts
    exit_code: EvaluationExitCode


@dataclass(slots=True)
class _AssertionFailureGroup:
    scenario: Scenario
    assertion: AssertionResult
    repeat_indexes: list[int]
    evidence_refs: list[str]


_ASSERTION_FINDING_POLICY: dict[
    AssertionType,
    tuple[DimensionName, FindingSeverity, str],
] = {
    AssertionType.TOOL_CALLED: (
        DimensionName.CORRECTNESS,
        FindingSeverity.MEDIUM,
        "Correct the agent's tool-selection behavior for this scenario.",
    ),
    AssertionType.MAX_TOOL_CALLS: (
        DimensionName.EFFICIENCY,
        FindingSeverity.LOW,
        "Remove unnecessary tool calls and stay within the scenario budget.",
    ),
    AssertionType.OUTPUT_CONTAINS: (
        DimensionName.CORRECTNESS,
        FindingSeverity.MEDIUM,
        "Correct the agent output so it satisfies the required result contract.",
    ),
    AssertionType.OUTPUT_FIELD_EQUALS: (
        DimensionName.CORRECTNESS,
        FindingSeverity.MEDIUM,
        "Correct the agent output so the required field has the expected value.",
    ),
    AssertionType.SCHEMA_VALID: (
        DimensionName.CORRECTNESS,
        FindingSeverity.MEDIUM,
        "Validate the agent output against the scenario schema before returning it.",
    ),
    AssertionType.NO_FORBIDDEN_CALLS: (
        DimensionName.SECURITY,
        FindingSeverity.CRITICAL,
        "Enforce the tool policy before dispatch and reject forbidden calls.",
    ),
    AssertionType.MAX_STEPS: (
        DimensionName.EFFICIENCY,
        FindingSeverity.LOW,
        "Remove unnecessary actions and stay within the scenario step budget.",
    ),
    AssertionType.NO_HALLUCINATED_SUCCESS: (
        DimensionName.CORRECTNESS,
        FindingSeverity.HIGH,
        "Require tool-backed evidence before reporting successful completion.",
    ),
    AssertionType.COMPLETED_BEFORE_TIMEOUT: (
        DimensionName.RELIABILITY,
        FindingSeverity.HIGH,
        "Bound work and handle dependency delays before the scenario deadline.",
    ),
}


class EvaluationPipeline:
    """Execute, evidence-link, score, report, then publish a completed manifest."""

    def __init__(
        self,
        run_store: RunArtifactStore,
        *,
        redaction_environment: Mapping[str, str] | None = None,
        secret_names: Sequence[str] = (),
        redaction_patterns: Sequence[str] = (),
        clock: Clock | None = None,
    ) -> None:
        self.run_store = run_store
        self._redaction_environment = redaction_environment
        self._secret_names = tuple(secret_names)
        self._redaction_patterns = tuple(redaction_patterns)
        self.redactor = SecretRedactor(
            environment=redaction_environment,
            secret_names=secret_names,
            patterns=redaction_patterns,
        )
        self._clock = clock or _utc_now

    def evaluate(self, request: EvaluationRequest) -> EvaluationPipelineResult:
        """Run one benchmark and publish the manifest only after reports exist."""

        adapter_import_root = self._validate_request(request)
        try:
            clean_config = _sanitized_config(request.resolved_config, self.redactor)
            clean_environment = _sanitized_mapping(request.environment, self.redactor)
            provenance = BenchmarkProvenance(
                id=request.benchmark.identity.id,
                version=request.benchmark.identity.version,
                schema_version=request.benchmark.identity.schema_version,
            )
            config_fingerprint = _config_fingerprint(request.resolved_config)
            adapter_fingerprint = _project_source_fingerprint(request.project_root)
            initial_summary: dict[str, JsonValue] = {
                "artifact_schema_version": 1,
                "canonical_schema_version": 1,
                "execution_mode": request.execution_mode.value,
                "isolation_level": request.resolved_config.execution.isolation.value,
                "repeat_count": request.resolved_config.evaluation.repeat,
            }
            run = self.run_store.create_run(
                project_id=_redacted_text(request.project_id, self.redactor),
                profile_id=_redacted_text(request.profile_id, self.redactor),
                benchmark_pack_ids=[_redacted_text(request.benchmark.identity.id, self.redactor)],
                environment_fingerprint=_redacted_text(
                    request.environment_fingerprint,
                    self.redactor,
                ),
                environment=clean_environment,
                resolved_config=clean_config,
                seed=request.resolved_config.evaluation.seed,
                summary=initial_summary,
            )
            running = _manifest_with(
                run,
                status=EvaluationRunStatus.RUNNING,
                finished_at=None,
                summary=run.summary,
            )
            self.run_store.write_manifest(running)
        except Exception as error:
            classified = (
                EvaluationConfigurationError(_redacted_text(str(error), self.redactor))
                if isinstance(error, OSError)
                else self._classify_error(error)
            )
            if classified is error:
                raise
            raise classified from error

        try:
            evidence_store = EvidenceStore(
                self.run_store,
                environment=self._redaction_environment,
                secret_names=self._secret_names,
                redaction_patterns=self._redaction_patterns,
            )
            factory = PythonCallableAdapterFactory(
                request.resolved_config.adapter.target,
                project_root=adapter_import_root,
            )
            executor = ScenarioExecutor(
                factory,
                timeout_seconds=request.resolved_config.evaluation.timeout_seconds,
                seed=request.resolved_config.evaluation.seed,
                network_policy=request.resolved_config.execution.network,
                max_concurrency=1,
            )
            assertion_engine = AssertionEngine()
            metric_collector = ScenarioMetricCollector()
            repeats: list[tuple[ScenarioEvaluationRecord, ...]] = []
            canonical_repeats: list[CanonicalRepeat] = []
            for repeat_index in range(1, request.resolved_config.evaluation.repeat + 1):
                if _project_source_fingerprint(request.project_root) != adapter_fingerprint:
                    raise EvaluationSecurityError(
                        "Evaluated Python source changed during repeat execution"
                    )
                records: list[ScenarioEvaluationRecord] = []
                for scenario in request.benchmark.scenarios:
                    execution = executor.execute(scenario)
                    evidence_bundle = evidence_store.record_execution(
                        running.id,
                        scenario,
                        execution,
                    )
                    assertion_evaluation = assertion_engine.evaluate(scenario, evidence_bundle)
                    evaluated_bundle = replace(
                        evidence_bundle,
                        execution=assertion_evaluation.execution,
                    )
                    metrics = metric_collector.collect(evaluated_bundle)
                    records.append(
                        ScenarioEvaluationRecord(
                            scenario=scenario,
                            bundle=evaluated_bundle,
                            assertions=assertion_evaluation.results,
                            metrics=metrics,
                        )
                    )
                repeat_records = tuple(records)
                repeats.append(repeat_records)
                canonical_repeats.append(
                    build_canonical_repeat(
                        run_id=running.id,
                        repeat_index=repeat_index,
                        records=repeat_records,
                        evidence_store=evidence_store,
                        redactor=self.redactor,
                    )
                )
            if _project_source_fingerprint(request.project_root) != adapter_fingerprint:
                raise EvaluationSecurityError("Evaluated Python source changed during execution")

            reproducibility_observation, reproducibility, reproducibility_finding = (
                _record_reproducibility(
                    run_id=running.id,
                    canonical_repeats=canonical_repeats,
                    repeat_records=repeats,
                    execution_mode=request.execution_mode,
                    benchmark_id=request.benchmark.identity.id,
                    benchmark_version=request.benchmark.identity.version,
                    evidence_store=evidence_store,
                )
            )
            assertion_findings, finding_ids_by_scenario = _derive_assertion_findings(repeats)
            generated_findings = list(assertion_findings)
            if reproducibility_finding is not None:
                generated_findings.append(reproducibility_finding)
            findings = _merge_findings(request.findings, generated_findings)
            finding_evidence_roles = _canonical_finding_evidence_roles(
                findings,
                repeats,
                reproducibility_evidence_refs=reproducibility.evidence_refs,
            )
            canonical = build_canonical_evaluation(
                repeats=canonical_repeats,
                provenance=provenance,
                benchmark_fingerprint=_benchmark_fingerprint(
                    request.benchmark,
                    request.policy,
                ),
                adapter_type=request.resolved_config.adapter.type,
                adapter_target=request.resolved_config.adapter.target,
                adapter_version=__version__,
                adapter_fingerprint=adapter_fingerprint,
                config_fingerprint=config_fingerprint,
                environment_fingerprint=request.environment_fingerprint,
                gauntlet_version=__version__,
                seed=request.resolved_config.evaluation.seed,
                execution_mode=request.execution_mode,
                findings=findings,
                finding_evidence_roles=finding_evidence_roles,
            )

            primary = repeats[0]
            results = tuple(
                _scenario_result(record, finding_ids_by_scenario.get(record.scenario.id, ()))
                for record in primary
            )
            score_inputs = [
                ScenarioScoreInput(record.scenario, record.metrics) for record in primary
            ]

            scoring = ScoringEngine().score(
                score_inputs,
                findings=findings,
                policy=request.policy,
                reproducibility=reproducibility_observation,
            )
            context = ReportContext(
                benchmark_packs=[provenance],
                config_fingerprint=config_fingerprint,
                environment_fingerprint=request.environment_fingerprint,
                gauntlet_version=__version__,
                python_version=platform.python_version(),
                platform=platform.platform(),
                seed=request.resolved_config.evaluation.seed,
                execution_mode=request.execution_mode,
                isolation_level=request.resolved_config.execution.isolation.value,
                reproducibility=reproducibility,
            )
            artifacts = ReportGenerator(self.run_store).write(
                running.id,
                results=results,
                scoring=scoring,
                findings=findings,
                context=context,
                canonical=canonical,
                redactor=self.redactor,
            )
            triggered_rules = [rule.rule_id for rule in scoring.policy_rules if rule.triggered]
            summary = RunSummary(
                scenarios_completed=scoring.scenarios_completed,
                benchmark_packs=[provenance],
                config_fingerprint=config_fingerprint,
                execution_mode=request.execution_mode,
                isolation_level=request.resolved_config.execution.isolation.value,
                release_recommendation=scoring.recommendation,
                applied_policy_rules=triggered_rules,
                reproducibility=reproducibility,
            )
            clean_summary = _sanitized_summary(summary, self.redactor)
            completed = _manifest_with(
                running,
                status=EvaluationRunStatus.COMPLETED,
                finished_at=self._clock(),
                summary=cast(
                    dict[str, JsonValue],
                    clean_summary.model_dump(mode="json", exclude_none=True),
                ),
            )
            self.run_store.write_manifest(completed)
            return EvaluationPipelineResult(
                run=completed,
                results=results,
                scoring=scoring,
                reproducibility=reproducibility,
                artifacts=artifacts,
                exit_code=exit_code_for_recommendation(scoring.recommendation),
            )
        except Exception as error:
            failed = _manifest_with(
                running,
                status=EvaluationRunStatus.FAILED,
                finished_at=self._clock(),
                summary=running.summary,
            )
            try:
                self.run_store.write_manifest(failed)
            except ArtifactStoreError:
                pass
            classified = self._classify_error(error)
            if classified is error:
                raise
            raise classified from error

    def _validate_request(self, request: EvaluationRequest) -> Path:
        if not request.project_id.strip() or not request.profile_id.strip():
            raise EvaluationConfigurationError("project_id and profile_id must be non-blank")
        if not request.environment_fingerprint.strip():
            raise EvaluationConfigurationError("environment_fingerprint must be non-blank")
        if request.resolved_config.adapter.type != "python_callable":
            raise EvaluationConfigurationError("MVP pipeline supports only python_callable")
        adapter_import_root = _project_adapter_import_root(
            request.project_root,
            request.resolved_config.adapter.target,
        )
        if request.reproducibility is not None:
            raise EvaluationConfigurationError(
                "EvaluationRequest.reproducibility is deprecated; configure repeat >= 2 so "
                "GAUNTLET derives the observation from canonical evidence"
            )
        if request.resolved_config.execution.isolation is not IsolationMode.SUBPROCESS:
            raise EvaluationConfigurationError("MVP pipeline requires subprocess isolation")
        if request.policy.id != request.resolved_config.scoring.policy:
            raise EvaluationConfigurationError(
                "Resolved scoring policy does not match the supplied policy"
            )
        if request.benchmark.identity.id not in request.resolved_config.evaluation.benchmark_packs:
            raise EvaluationConfigurationError(
                "Resolved benchmark selection does not include the supplied pack"
            )
        if (
            request.resolved_config.artifacts.root.expanduser().resolve()
            != self.run_store.root.resolve()
        ):
            raise EvaluationConfigurationError(
                "Resolved artifact root does not match the pipeline artifact store"
            )
        return adapter_import_root

    def _classify_error(self, error: Exception) -> EvaluationPipelineError:
        if isinstance(error, EvaluationPipelineError):
            return error
        message = _redacted_text(str(error), self.redactor)
        if isinstance(
            error,
            (
                AssertionConfigurationError,
                ExecutionPolicyError,
                MetricCollectionError,
                ScoringError,
            ),
        ):
            return EvaluationConfigurationError(message)
        if isinstance(error, (CanonicalizationError, RedactionError, ReportGenerationError)):
            return EvaluationSecurityError(message)
        if isinstance(error, ArtifactStoreError):
            return IncompleteEvaluationError(message)
        return EvaluationExecutionError(message)


def _record_reproducibility(
    *,
    run_id: str,
    canonical_repeats: Sequence[CanonicalRepeat],
    repeat_records: Sequence[Sequence[ScenarioEvaluationRecord]],
    execution_mode: ExecutionMode,
    benchmark_id: str,
    benchmark_version: str,
    evidence_store: EvidenceStore,
) -> tuple[ReproducibilityObservation | None, ReproducibilityReport, Finding | None]:
    repeat_count = len(canonical_repeats)
    if repeat_count != len(repeat_records) or repeat_count == 0:
        raise CanonicalizationError("Canonical and recorded repeat counts do not agree")
    if repeat_count == 1:
        return (
            None,
            ReproducibilityReport(
                repeat_count=1,
                claim=ReproducibilityClaim.NOT_ASSESSED,
            ),
            None,
        )

    digests = [canonical_repeat_digest(item) for item in canonical_repeats]
    baseline = digests[0]
    mismatched_repeats = [
        index for index, digest in enumerate(digests, start=1) if digest != baseline
    ]
    evidence_rows: list[dict[str, JsonValue]] = []
    for index, (digest, records) in enumerate(zip(digests, repeat_records, strict=True), start=1):
        evidence_refs = list(
            dict.fromkeys(item.id for record in records for item in record.bundle.evidence)
        )
        evidence_rows.append(
            {
                "repeat_index": index,
                "canonical_hash": digest,
                "evidence_refs": cast(JsonValue, evidence_refs),
            }
        )

    deterministic = execution_mode is ExecutionMode.DETERMINISTIC_FIXTURE
    reproducible = deterministic and not mismatched_repeats
    live_distribution = (
        None if deterministic else _live_repeat_distribution(digests, repeat_records)
    )
    comparison = evidence_store.persist(
        run_id,
        EvidenceType.METRIC,
        cast(
            JsonValue,
            {
                "schema_version": 1,
                "execution_mode": execution_mode.value,
                "repeat_count": repeat_count,
                "baseline_repeat": 1,
                "repeats": evidence_rows,
                "mismatched_repeats": mismatched_repeats,
                "reproducible": reproducible if deterministic else None,
                "live_distribution": (
                    live_distribution.model_dump(mode="json")
                    if live_distribution is not None
                    else None
                ),
            },
        ),
        metadata={
            "kind": "reproducibility_comparison",
            "benchmark_id": benchmark_id,
            "benchmark_version": benchmark_version,
        },
    )

    if not deterministic:
        return (
            None,
            ReproducibilityReport(
                repeat_count=repeat_count,
                claim=ReproducibilityClaim.LIVE_VARIANCE_ONLY,
                evidence_refs=[comparison.id],
                live_distribution=live_distribution,
            ),
            None,
        )

    observation = ReproducibilityObservation(
        reproducible=reproducible,
        repeat_count=repeat_count,
        evidence_refs=[comparison.id],
    )
    claim = (
        ReproducibilityClaim.BYTE_IDENTICAL
        if reproducible
        else ReproducibilityClaim.NON_REPRODUCIBLE
    )
    report = ReproducibilityReport(
        repeat_count=repeat_count,
        claim=claim,
        evidence_refs=[comparison.id],
    )
    if reproducible:
        return observation, report, None
    mismatch_text = ", ".join(str(index) for index in mismatched_repeats)
    finding = Finding(
        id="reproducibility.non_reproducible_result",
        severity=FindingSeverity.HIGH,
        dimension=DimensionName.REPRODUCIBILITY,
        title="non-reproducible result",
        description=(
            f"Canonical semantic output differed from repeat 1 in repeat(s): {mismatch_text}."
        ),
        evidence_refs=[comparison.id],
        remediation=(
            "Inspect the canonical repeat hashes and their linked evidence while holding "
            "the seed, fixtures, configuration, benchmark, and environment constant."
        ),
        confidence=1,
    )
    return observation, report, finding


def _live_repeat_distribution(
    digests: Sequence[str],
    repeats: Sequence[Sequence[ScenarioEvaluationRecord]],
) -> LiveRepeatDistribution:
    """Record honest per-repeat live facts without estimating missing cost data."""

    latencies: list[int] = []
    task_success_rates: list[float | None] = []
    costs: list[float] = []
    complete_cost_distribution = True
    for records in repeats:
        latencies.append(sum(record.metrics.latency_ms for record in records))
        task_values = [
            record.metrics.task_success
            for record in records
            if record.metrics.task_success is not None
        ]
        task_success_rates.append(
            sum(value is True for value in task_values) / len(task_values) if task_values else None
        )
        repeat_costs: list[float] = []
        for record in records:
            raw_cost = record.metrics.observed_usage.get(MetricName.COST_USD)
            if raw_cost is None:
                complete_cost_distribution = False
                break
            repeat_costs.append(float(raw_cost))
        if complete_cost_distribution:
            costs.append(sum(repeat_costs))
    return LiveRepeatDistribution(
        canonical_hashes=list(digests),
        total_latency_ms=latencies,
        task_success_rates=task_success_rates,
        observed_cost_usd=costs if complete_cost_distribution else None,
    )


def _derive_assertion_findings(
    repeats: Sequence[Sequence[ScenarioEvaluationRecord]],
) -> tuple[tuple[Finding, ...], dict[str, tuple[str, ...]]]:
    groups: dict[tuple[str, int, AssertionType], _AssertionFailureGroup] = {}
    for repeat_index, records in enumerate(repeats, start=1):
        for record in records:
            for assertion in record.assertions:
                if assertion.passed:
                    continue
                key = (record.scenario.id, assertion.assertion_index, assertion.type)
                group = groups.get(key)
                if group is None:
                    group = _AssertionFailureGroup(
                        scenario=record.scenario,
                        assertion=assertion,
                        repeat_indexes=[],
                        evidence_refs=[],
                    )
                    groups[key] = group
                group.repeat_indexes.append(repeat_index)
                group.evidence_refs.extend(assertion.evidence_refs)

    findings: list[Finding] = []
    ids_by_scenario: dict[str, list[str]] = {}
    for group in groups.values():
        dimension, severity, remediation = _ASSERTION_FINDING_POLICY[group.assertion.type]
        finding_id = (
            f"assertion.{group.scenario.id}.{group.assertion.assertion_index}."
            f"{group.assertion.type.value}"
        )
        repeat_text = ", ".join(str(index) for index in group.repeat_indexes)
        finding = Finding(
            id=finding_id,
            severity=severity,
            dimension=dimension,
            title=f"{group.scenario.title}: {group.assertion.type.value} failed",
            description=(
                f"Assertion {group.assertion.type.value!r} failed in repeat(s) "
                f"{repeat_text}: {group.assertion.message}"
            ),
            evidence_refs=list(dict.fromkeys(group.evidence_refs)),
            remediation=remediation,
            confidence=1,
        )
        findings.append(finding)
        ids_by_scenario.setdefault(group.scenario.id, []).append(finding.id)
    return (
        tuple(findings),
        {scenario_id: tuple(ids) for scenario_id, ids in ids_by_scenario.items()},
    )


def _merge_findings(
    supplied: Sequence[Finding],
    generated: Sequence[Finding],
) -> tuple[Finding, ...]:
    findings = (*supplied, *generated)
    ids = [finding.id for finding in findings]
    if len(set(ids)) != len(ids):
        raise EvaluationConfigurationError("Supplied and generated findings contain duplicate IDs")
    return findings


def _canonical_finding_evidence_roles(
    findings: Sequence[Finding],
    repeats: Sequence[Sequence[ScenarioEvaluationRecord]],
    *,
    reproducibility_evidence_refs: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    """Replace volatile evidence IDs with stable repeat/scenario/role selectors."""

    roles_by_ref: dict[str, set[str]] = {}
    for repeat_index, records in enumerate(repeats, start=1):
        for record in records:
            for role, refs in record.bundle.refs_by_role.items():
                selector = f"repeat:{repeat_index}/scenario:{record.scenario.id}/role:{role}"
                for ref in refs:
                    roles_by_ref.setdefault(ref, set()).add(selector)

    links: dict[str, tuple[str, ...]] = {}
    for finding in findings:
        if not finding.evidence_refs:
            raise EvaluationConfigurationError(
                f"Finding {finding.id!r} must reference persisted evaluation evidence"
            )
        evidence_roles: set[str] = set()
        for ref in finding.evidence_refs:
            matched = roles_by_ref.get(ref)
            if matched:
                evidence_roles.update(matched)
            elif (
                finding.id == "reproducibility.non_reproducible_result"
                and ref in reproducibility_evidence_refs
            ):
                evidence_roles.add("run:reproducibility_comparison")
            else:
                raise EvaluationConfigurationError(
                    f"Finding {finding.id!r} references unknown evidence {ref!r}"
                )
        links[finding.id] = tuple(sorted(evidence_roles))
    return links


def _scenario_result(
    record: ScenarioEvaluationRecord,
    finding_ids: Sequence[str],
) -> ScenarioResult:
    data = record.bundle.execution.result.model_dump(mode="json")
    data["metrics"] = record.metrics.to_result_metrics()
    data["findings"] = list(dict.fromkeys((*record.bundle.execution.result.findings, *finding_ids)))
    return ScenarioResult.model_validate(data)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _manifest_with(
    manifest: EvaluationRun,
    *,
    status: EvaluationRunStatus,
    finished_at: datetime | None,
    summary: Mapping[str, JsonValue],
) -> EvaluationRun:
    data = manifest.model_dump(mode="python")
    data.update(
        {
            "status": status,
            "finished_at": finished_at,
            "summary": dict(summary),
        }
    )
    return EvaluationRun.model_validate(data)


def _sanitized_config(config: GauntletConfig, redactor: SecretRedactor) -> GauntletConfig:
    raw = cast(JsonValue, config.model_dump(mode="json"))
    redacted = redactor.redact(raw).value
    return GauntletConfig.model_validate(redacted)


def _sanitized_mapping(
    value: Mapping[str, JsonValue],
    redactor: SecretRedactor,
) -> dict[str, JsonValue]:
    redacted = redactor.redact(cast(JsonValue, dict(value))).value
    if not isinstance(redacted, dict):  # pragma: no cover - type invariant
        raise EvaluationSecurityError("Environment redaction did not return an object")
    return redacted


def _sanitized_summary(summary: RunSummary, redactor: SecretRedactor) -> RunSummary:
    raw = cast(JsonValue, summary.model_dump(mode="json"))
    return RunSummary.model_validate(redactor.redact(raw).value)


def _redacted_text(value: str, redactor: SecretRedactor) -> str:
    result = redactor.redact(value).value
    if not isinstance(result, str):  # pragma: no cover - type invariant
        raise EvaluationSecurityError("Text redaction did not return text")
    return result


def _config_fingerprint(config: GauntletConfig) -> str:
    content = json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _benchmark_fingerprint(
    benchmark: LoadedBenchmarkPack,
    policy: ScoringPolicy,
) -> str:
    """Fingerprint parsed pack semantics so unversioned fixture drift is detectable."""

    payload = {
        "manifest": benchmark.manifest.model_dump(mode="json"),
        "scenarios": [scenario.model_dump(mode="json") for scenario in benchmark.scenarios],
        "scoring_policy": policy.model_dump(mode="json"),
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


_SOURCE_FINGERPRINT_IGNORES = frozenset(
    {
        ".git",
        ".gauntlet",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "reports",
        "venv",
    }
)

_SOURCE_FINGERPRINT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".ini",
        ".j2",
        ".jinja",
        ".jinja2",
        ".json",
        ".md",
        ".prompt",
        ".py",
        ".pyi",
        ".rst",
        ".sql",
        ".tmpl",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)


def _project_source_fingerprint(project_root: Path) -> str:
    """Hash bounded source and prompt/config resources that can affect adapter behavior."""

    try:
        root = project_root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise CanonicalizationError(f"Unable to fingerprint project source: {error}") from error
    entries: list[dict[str, str]] = []
    for candidate in root.rglob("*"):
        try:
            relative = candidate.relative_to(root)
            if any(part in _SOURCE_FINGERPRINT_IGNORES for part in relative.parts[:-1]):
                continue
            if candidate.suffix.lower() not in _SOURCE_FINGERPRINT_SUFFIXES:
                continue
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise CanonicalizationError(
                    f"Project source path escapes the evaluation root: {candidate}"
                )
            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except CanonicalizationError:
            raise
        except (OSError, RuntimeError) as error:
            raise CanonicalizationError(
                f"Unable to fingerprint project source {candidate}: {error}"
            ) from error
        entries.append({"path": relative.as_posix(), "sha256": digest})
    if not entries:
        raise CanonicalizationError(
            f"No supported source or resource files were found under project root {root}"
        )
    entries.sort(key=lambda item: item["path"])
    content = json.dumps(
        entries,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _project_adapter_import_root(project_root: Path, target: str) -> Path:
    """Resolve a project-owned adapter target and return its safe Python import root."""

    module, separator, attributes = target.partition(":")
    if (
        not separator
        or not module
        or not attributes
        or any(not part.isidentifier() for part in module.split("."))
        or any(not part.isidentifier() for part in attributes.split("."))
    ):
        raise EvaluationConfigurationError(
            "adapter.target must use the project-local 'module:callable' form"
        )
    try:
        root = project_root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise EvaluationConfigurationError(f"Project root is unreadable: {error}") from error
    module_path = Path(*module.split("."))
    candidates = (
        (root, root / module_path.with_suffix(".py")),
        (root, root / module_path / "__init__.py"),
        (root / "src", root / "src" / module_path.with_suffix(".py")),
        (root / "src", root / "src" / module_path / "__init__.py"),
    )
    for import_root, candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.is_relative_to(root) and resolved.is_file():
            return import_root
    checked = ", ".join(str(candidate) for _, candidate in candidates)
    raise EvaluationConfigurationError(
        f"adapter.target {target!r} must resolve beneath project root {root}; checked: {checked}"
    )
