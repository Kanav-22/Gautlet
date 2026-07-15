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
    EvaluationRun,
    EvaluationRunStatus,
    Finding,
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
    ExecutionPolicyError,
    PythonCallableAdapterFactory,
    ScenarioExecutor,
)
from gauntlet.metrics import MetricCollectionError, ScenarioMetricCollector
from gauntlet.reporting.generator import ReportGenerationError, ReportGenerator
from gauntlet.reporting.models import (
    BenchmarkProvenance,
    ExecutionMode,
    ReportArtifacts,
    ReportContext,
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
    artifacts: ReportArtifacts
    exit_code: EvaluationExitCode


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

        self._validate_request(request)
        clean_config = _sanitized_config(request.resolved_config, self.redactor)
        clean_environment = _sanitized_mapping(request.environment, self.redactor)
        provenance = BenchmarkProvenance(
            id=request.benchmark.identity.id,
            version=request.benchmark.identity.version,
            schema_version=request.benchmark.identity.schema_version,
        )
        config_fingerprint = _config_fingerprint(request.resolved_config)
        initial_summary: dict[str, JsonValue] = {
            "artifact_schema_version": 1,
            "execution_mode": request.execution_mode.value,
            "isolation_level": request.resolved_config.execution.isolation.value,
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

        try:
            evidence_store = EvidenceStore(
                self.run_store,
                environment=self._redaction_environment,
                secret_names=self._secret_names,
                redaction_patterns=self._redaction_patterns,
            )
            factory = PythonCallableAdapterFactory(
                request.resolved_config.adapter.target,
                project_root=request.project_root,
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
            results: list[ScenarioResult] = []
            score_inputs: list[ScenarioScoreInput] = []
            for scenario in request.benchmark.scenarios:
                execution = executor.execute(scenario)
                evidence_bundle = evidence_store.record_execution(running.id, scenario, execution)
                assertion_evaluation = assertion_engine.evaluate(scenario, evidence_bundle)
                evaluated_bundle = replace(
                    evidence_bundle,
                    execution=assertion_evaluation.execution,
                )
                metrics = metric_collector.collect(evaluated_bundle)
                result_data = assertion_evaluation.execution.result.model_dump(mode="json")
                result_data["metrics"] = metrics.to_result_metrics()
                result = ScenarioResult.model_validate(result_data)
                results.append(result)
                score_inputs.append(ScenarioScoreInput(scenario, metrics))

            scoring = ScoringEngine().score(
                score_inputs,
                findings=request.findings,
                policy=request.policy,
                reproducibility=request.reproducibility,
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
            )
            artifacts = ReportGenerator(self.run_store).write(
                running.id,
                results=results,
                scoring=scoring,
                findings=request.findings,
                context=context,
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
            )
            clean_summary = _sanitized_summary(summary, self.redactor)
            completed = _manifest_with(
                running,
                status=EvaluationRunStatus.COMPLETED,
                finished_at=self._clock(),
                summary=cast(dict[str, JsonValue], clean_summary.model_dump(mode="json")),
            )
            self.run_store.write_manifest(completed)
            return EvaluationPipelineResult(
                run=completed,
                results=tuple(results),
                scoring=scoring,
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

    def _validate_request(self, request: EvaluationRequest) -> None:
        if not request.project_id.strip() or not request.profile_id.strip():
            raise EvaluationConfigurationError("project_id and profile_id must be non-blank")
        if not request.environment_fingerprint.strip():
            raise EvaluationConfigurationError("environment_fingerprint must be non-blank")
        if request.resolved_config.adapter.type != "python_callable":
            raise EvaluationConfigurationError("MVP pipeline supports only python_callable")
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
        if isinstance(error, (RedactionError, ReportGenerationError)):
            return EvaluationSecurityError(message)
        if isinstance(error, ArtifactStoreError):
            return IncompleteEvaluationError(message)
        return EvaluationExecutionError(message)


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
