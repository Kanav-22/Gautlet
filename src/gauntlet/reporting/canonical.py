"""ADR-004 canonical semantic projections for deterministic repeat comparison."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import Field, JsonValue, PositiveInt, field_validator

from gauntlet.core.models import (
    DimensionName,
    Evidence,
    EvidenceType,
    Finding,
    FindingSeverity,
    GauntletModel,
    JsonObject,
    Scenario,
    ScenarioResultStatus,
)
from gauntlet.evidence import EvidenceStore, ScenarioEvidenceBundle, SecretRedactor
from gauntlet.execution import AssertionResult, AssertionType
from gauntlet.metrics import MetricName, ScenarioMetrics
from gauntlet.reporting.models import BenchmarkProvenance, ExecutionMode


class CanonicalizationError(RuntimeError):
    """A persisted execution cannot be projected without weakening the contract."""


class CanonicalEvidencePayload(GauntletModel):
    """Evidence semantics with volatile content-addressed identity removed."""

    role: str
    type: EvidenceType
    payload: JsonValue
    metadata: JsonObject
    redacted: bool

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("canonical evidence role must be non-blank")
        return value


class CanonicalAssertionResult(GauntletModel):
    """Assertion semantics linked to stable evidence roles, not volatile IDs."""

    assertion_index: int = Field(ge=0)
    type: AssertionType
    passed: bool
    message: str
    details: JsonObject
    evidence_roles: list[str]


class CanonicalScenarioResult(GauntletModel):
    """One scenario result with lifecycle and storage identity excluded."""

    scenario_id: str
    status: ScenarioResultStatus
    output: JsonObject | None
    error: JsonObject | None
    metrics: JsonObject
    assertions: list[CanonicalAssertionResult]
    evidence: list[CanonicalEvidencePayload]


class CanonicalRepeat(GauntletModel):
    """A complete semantic benchmark pass in stable benchmark order."""

    repeat_index: PositiveInt
    scenarios: list[CanonicalScenarioResult] = Field(min_length=1)


class CanonicalFinding(GauntletModel):
    """Finding semantics with volatile content-addressed references removed."""

    id: str
    severity: FindingSeverity
    dimension: DimensionName
    title: str
    description: str
    remediation: str | None
    confidence: float = Field(ge=0, le=1)
    evidence_roles: list[str]


class CanonicalEvaluation(GauntletModel):
    """Versioned repeatability comparison artifact defined by ADR-004."""

    schema_version: Literal[1] = 1
    comparison_contract: Literal[
        "adr-004-deterministic-fixture-v1",
        "adr-004-live-repeat-observation-v1",
    ]
    benchmark_packs: list[BenchmarkProvenance] = Field(min_length=1)
    benchmark_fingerprint: str
    adapter_type: str
    adapter_target: str
    adapter_version: str
    adapter_fingerprint: str
    config_fingerprint: str
    environment_fingerprint: str
    gauntlet_version: str
    seed: int | None
    execution_mode: ExecutionMode
    repeats: list[CanonicalRepeat] = Field(min_length=1)
    findings: list[CanonicalFinding]

    @field_validator(
        "adapter_type",
        "adapter_target",
        "adapter_version",
        "adapter_fingerprint",
        "benchmark_fingerprint",
        "config_fingerprint",
        "environment_fingerprint",
        "gauntlet_version",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("canonical evaluation identity fields must be non-blank")
        return value


@dataclass(frozen=True, slots=True)
class ScenarioEvaluationRecord:
    """One evidence-linked scenario observation used by reporting and scoring."""

    scenario: Scenario
    bundle: ScenarioEvidenceBundle
    assertions: tuple[AssertionResult, ...]
    metrics: ScenarioMetrics


def build_canonical_repeat(
    *,
    run_id: str,
    repeat_index: int,
    records: Sequence[ScenarioEvaluationRecord],
    evidence_store: EvidenceStore,
    redactor: SecretRedactor,
) -> CanonicalRepeat:
    """Build a post-redaction semantic projection from hash-verified evidence."""

    scenario_ids = [record.scenario.id for record in records]
    if not records:
        raise CanonicalizationError("A canonical repeat requires at least one scenario")
    if len(set(scenario_ids)) != len(scenario_ids):
        raise CanonicalizationError("A canonical repeat contains duplicate scenario IDs")

    scenarios: list[CanonicalScenarioResult] = []
    for record in records:
        result = record.bundle.execution.result
        if result.scenario_id != record.scenario.id:
            raise CanonicalizationError("Canonical scenario and execution IDs do not match")
        role_by_ref = _role_by_evidence_ref(record.bundle)
        evidence = [
            _canonical_evidence(
                run_id=run_id,
                item=item,
                role=role_by_ref[item.id],
                evidence_store=evidence_store,
            )
            for item in record.bundle.evidence
        ]
        evidence.sort(key=_model_sort_key)
        assertions = [
            CanonicalAssertionResult(
                assertion_index=item.assertion_index,
                type=item.type,
                passed=item.passed,
                message=item.message,
                details=copy.deepcopy(item.details),
                evidence_roles=sorted({role_by_ref[ref] for ref in item.evidence_refs}),
            )
            for item in record.assertions
        ]
        assertions.sort(key=lambda item: item.assertion_index)
        scenarios.append(
            CanonicalScenarioResult(
                scenario_id=result.scenario_id,
                status=result.status,
                output=copy.deepcopy(result.output),
                error=copy.deepcopy(result.error),
                metrics=_canonical_metrics(record.metrics),
                assertions=assertions,
                evidence=evidence,
            )
        )

    unredacted = CanonicalRepeat(repeat_index=repeat_index, scenarios=scenarios)
    redacted = redactor.redact(cast(JsonValue, unredacted.model_dump(mode="json"))).value
    try:
        return CanonicalRepeat.model_validate(redacted)
    except ValueError as error:
        raise CanonicalizationError(
            f"Redaction made the canonical repeat invalid: {error}"
        ) from error


def build_canonical_evaluation(
    *,
    repeats: Sequence[CanonicalRepeat],
    provenance: BenchmarkProvenance,
    benchmark_fingerprint: str,
    adapter_type: str,
    adapter_target: str,
    adapter_version: str,
    adapter_fingerprint: str,
    config_fingerprint: str,
    environment_fingerprint: str,
    gauntlet_version: str,
    seed: int | None,
    execution_mode: ExecutionMode,
    findings: Sequence[Finding],
    finding_evidence_roles: Mapping[str, Sequence[str]],
) -> CanonicalEvaluation:
    """Attach stable comparison context to already-redacted repeat projections."""

    return CanonicalEvaluation(
        comparison_contract=(
            "adr-004-deterministic-fixture-v1"
            if execution_mode is ExecutionMode.DETERMINISTIC_FIXTURE
            else "adr-004-live-repeat-observation-v1"
        ),
        benchmark_packs=[provenance],
        benchmark_fingerprint=benchmark_fingerprint,
        adapter_type=adapter_type,
        adapter_target=adapter_target,
        adapter_version=adapter_version,
        adapter_fingerprint=adapter_fingerprint,
        config_fingerprint=config_fingerprint,
        environment_fingerprint=environment_fingerprint,
        gauntlet_version=gauntlet_version,
        seed=seed,
        execution_mode=execution_mode,
        repeats=list(repeats),
        findings=sorted(
            [
                CanonicalFinding(
                    id=finding.id,
                    severity=finding.severity,
                    dimension=finding.dimension,
                    title=finding.title,
                    description=finding.description,
                    remediation=finding.remediation,
                    confidence=finding.confidence,
                    evidence_roles=sorted(set(finding_evidence_roles[finding.id])),
                )
                for finding in findings
            ],
            key=lambda finding: finding.id,
        ),
    )


def canonical_repeat_digest(repeat: CanonicalRepeat) -> str:
    """Hash semantic scenarios while deliberately excluding the repeat ordinal."""

    payload = cast(
        JsonValue,
        {"scenarios": [item.model_dump(mode="json") for item in repeat.scenarios]},
    )
    return "sha256:" + hashlib.sha256(_stable_json_bytes(payload)).hexdigest()


def _canonical_evidence(
    *,
    run_id: str,
    item: Evidence,
    role: str,
    evidence_store: EvidenceStore,
) -> CanonicalEvidencePayload:
    envelope = evidence_store.load(run_id, item)
    payload = copy.deepcopy(envelope["payload"])
    metadata_value = copy.deepcopy(envelope["metadata"])
    if not isinstance(metadata_value, dict):  # pragma: no cover - envelope verified by store
        raise CanonicalizationError("Canonical evidence metadata is not an object")
    metadata = metadata_value
    if role == "execution":
        if not isinstance(payload, dict):
            raise CanonicalizationError("Execution lifecycle evidence is not an object")
        payload = {
            key: value
            for key, value in payload.items()
            if key not in {"started_at", "finished_at", "duration_ms"}
        }
    return CanonicalEvidencePayload(
        role=role,
        type=item.type,
        payload=payload,
        metadata=metadata,
        redacted=item.redacted,
    )


def _role_by_evidence_ref(bundle: ScenarioEvidenceBundle) -> dict[str, str]:
    roles: dict[str, str] = {}
    known = {item.id for item in bundle.evidence}
    for role, refs in bundle.refs_by_role.items():
        for ref in refs:
            if ref not in known:
                raise CanonicalizationError(f"Evidence role {role!r} references an unknown ID")
            existing = roles.get(ref)
            if existing is not None and existing != role:
                raise CanonicalizationError("One evidence item has multiple semantic roles")
            roles[ref] = role
    missing = sorted(known - roles.keys())
    if missing:
        raise CanonicalizationError("Evidence items are missing semantic roles")
    return roles


def _canonical_metrics(metrics: ScenarioMetrics) -> JsonObject:
    completeness = {
        name.value: complete
        for name, complete in sorted(metrics.completeness.items(), key=lambda item: item[0].value)
        if name is not MetricName.LATENCY_MS
    }
    observed_usage = {
        name.value: value
        for name, value in sorted(metrics.observed_usage.items(), key=lambda item: item[0].value)
    }
    return cast(
        JsonObject,
        {
            "task_success": metrics.task_success,
            "tool_calls": metrics.tool_calls,
            "retries": metrics.retries,
            "recovery_steps": metrics.recovery_steps,
            "steps": metrics.steps,
            "exceptions": metrics.exceptions,
            "observed_usage": observed_usage,
            "completeness": completeness,
        },
    )


def _model_sort_key(model: GauntletModel) -> bytes:
    return _stable_json_bytes(cast(JsonValue, model.model_dump(mode="json")))


def _stable_json_bytes(payload: JsonValue) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
