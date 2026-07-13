"""Round-trip and schema tests for GAUNTLET's public models."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from gauntlet.config import GauntletConfig
from gauntlet.core import (
    BenchmarkPackManifest,
    DimensionName,
    DimensionScore,
    EvaluationRun,
    EvaluationRunStatus,
    Evidence,
    EvidenceType,
    Finding,
    FindingSeverity,
    GauntletModel,
    Scenario,
    ScenarioResult,
    ScenarioResultStatus,
    ScoreCard,
)
from gauntlet.core.schema_export import SCHEMA_MODELS, export_json_schemas, get_json_schemas

STARTED_AT = datetime(2026, 7, 13, 8, 30, tzinfo=UTC)
FINISHED_AT = datetime(2026, 7, 13, 8, 31, tzinfo=UTC)


def _resolved_config() -> GauntletConfig:
    return GauntletConfig.model_validate(
        {
            "project": {"name": "sample-agent"},
            "adapter": {"type": "python_callable", "target": "sample_agent.app:run"},
            "evaluation": {
                "benchmark_packs": ["gauntlet.agent.mvp"],
                "seed": 42,
                "repeat": 1,
                "timeout_seconds": 60,
            },
            "execution": {"network": "disabled", "isolation": "subprocess"},
            "reporting": {"formats": ["json", "markdown"]},
            "scoring": {"policy": "agent_mvp_default"},
            "artifacts": {"root": "~/.gauntlet/artifacts"},
        }
    )


def _public_models() -> list[GauntletModel]:
    return [
        EvaluationRun(
            id="run_20260713_083000_1a2b3c4d",
            project_id="sample-agent",
            profile_id="default",
            benchmark_pack_ids=["gauntlet.agent.mvp"],
            started_at=STARTED_AT,
            finished_at=None,
            status=EvaluationRunStatus.RUNNING,
            seed=42,
            environment_fingerprint="sha256:environment",
            gauntlet_version="0.1.0",
            plugin_versions={"gauntlet-agent-core": "0.1.0"},
            summary={"scenarios_completed": 0},
        ),
        Scenario(
            id="agent.tool_failure_recovery",
            title="Recover from a temporary tool failure",
            description="Retry a failed lookup without hallucinating success.",
            category="reliability",
            difficulty=2,
            tags=["tools", "recovery"],
            required_capabilities=["invoke", "trace_tool_calls"],
            input={"user": "Find the value."},
            fixtures={"tool_sequence": [{"tool": "lookup", "error": "temporary"}]},
            execution_policy={"timeout_seconds": 60},
            assertions=[{"type": "max_tool_calls", "value": 3}],
            metrics=["task_success", "tool_calls"],
        ),
        ScenarioResult(
            scenario_id="agent.tool_failure_recovery",
            status=ScenarioResultStatus.TIMED_OUT,
            started_at=STARTED_AT,
            finished_at=FINISHED_AT,
            duration_ms=60_000,
            output=None,
            error={"type": "timeout"},
            metrics={"tool_calls": 1},
            evidence_refs=["evidence-timeout"],
            findings=["finding-timeout"],
        ),
        Evidence(
            id="evidence-timeout",
            type=EvidenceType.EXCEPTION,
            path="evidence/timeout.json",
            content_hash="sha256:evidence",
            redacted=False,
            metadata={"scenario_id": "agent.tool_failure_recovery"},
        ),
        Finding(
            id="finding-maintainability",
            severity=FindingSeverity.LOW,
            dimension=DimensionName.MAINTAINABILITY,
            title="Adapter requires a shim",
            description="The system hard-wires its tool registry.",
            evidence_refs=["evidence-timeout"],
            remediation=None,
            confidence=0.75,
        ),
        ScoreCard(
            overall=82.5,
            dimensions={
                DimensionName.CORRECTNESS: DimensionScore(score=90, confidence=0.95),
                DimensionName.EFFICIENCY: DimensionScore(score=75, confidence=0.8),
            },
            confidence=0.87,
            policy_id="agent_mvp_default",
        ),
        BenchmarkPackManifest(
            id="gauntlet.agent.mvp",
            version="0.1.0",
            title="Agent MVP Evaluation Pack",
            description="Core agentic system evaluation",
            schema_version=1,
            required_capabilities=["invoke", "trace_tool_calls"],
            dimensions=[
                DimensionName.CORRECTNESS,
                DimensionName.RELIABILITY,
                DimensionName.SECURITY,
            ],
            scenarios=[
                "scenarios/basic_tool_use.yaml",
                "scenarios/tool_failure_recovery.yaml",
            ],
            scoring_policy="scoring.yaml",
        ),
        _resolved_config(),
    ]


@pytest.mark.parametrize("model", _public_models(), ids=lambda model: type(model).__name__)
def test_public_models_round_trip_through_json(model: GauntletModel) -> None:
    restored = type(model).model_validate_json(model.model_dump_json(round_trip=True))

    assert restored == model
    json.dumps(model.model_dump(mode="json"))


def test_nullable_result_fields_remain_explicit() -> None:
    result = _public_models()[2]
    dumped = result.model_dump(mode="json")

    assert dumped["output"] is None
    assert "output" in dumped


def test_unknown_nested_config_field_is_rejected() -> None:
    data = _resolved_config().model_dump(mode="json")
    data["project"]["unknown"] = True

    with pytest.raises(ValidationError):
        GauntletConfig.model_validate(data)


@pytest.mark.parametrize(
    ("score", "confidence"),
    [(-1, 0.5), (101, 0.5), (50, -0.1), (50, 1.1)],
)
def test_dimension_score_bounds_are_enforced(score: float, confidence: float) -> None:
    with pytest.raises(ValidationError):
        DimensionScore(score=score, confidence=confidence)


def test_schema_registry_exports_all_public_contracts(tmp_path: Path) -> None:
    expected = {
        "evaluation-run",
        "scenario",
        "scenario-result",
        "evidence",
        "finding",
        "score-card",
        "benchmark-pack-manifest",
        "gauntlet-config",
    }

    assert set(SCHEMA_MODELS) == expected
    assert set(get_json_schemas()) == expected

    paths = export_json_schemas(tmp_path)
    assert {path.name for path in paths} == {f"{name}.schema.json" for name in expected}
    for path in paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
