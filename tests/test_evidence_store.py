"""Tests for content-addressed evidence and pre-persistence redaction."""

from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from gauntlet.config.loader import resolve_config
from gauntlet.config.models import NetworkPolicy
from gauntlet.core.models import (
    EvidenceType,
    Scenario,
    ScenarioResult,
    ScenarioResultStatus,
)
from gauntlet.evidence import (
    EvidenceCorruptionError,
    EvidenceStore,
    RedactionError,
    RunArtifactStore,
    SecretRedactor,
)
from gauntlet.execution import (
    AttemptRecord,
    ScenarioExecution,
    ScenarioLifecycleState,
)

FIXED_TIME = datetime(2026, 7, 14, 1, 2, 3, tzinfo=UTC)
RUN_ID = "run_20260714_010203_c0decafe"


def make_run_store(tmp_path: Path) -> RunArtifactStore:
    store = RunArtifactStore(
        tmp_path / "artifacts",
        clock=lambda: FIXED_TIME,
        nonce_factory=lambda: "c0decafe",
    )
    manifest = store.create_run(
        project_id="evidence-test",
        profile_id="default",
        benchmark_pack_ids=["test-pack"],
        environment_fingerprint="sha256:test",
        environment={"python": "test"},
        resolved_config=resolve_config(
            project_config={
                "project": {"name": "evidence-test"},
                "adapter": {"type": "python_callable", "target": "example:run"},
            },
            environ={},
        ),
        started_at=FIXED_TIME,
    )
    assert manifest.id == RUN_ID
    return store


def test_redaction_happens_before_hash_and_persistence(tmp_path: Path) -> None:
    run_store = make_run_store(tmp_path)
    store = EvidenceStore(
        run_store,
        environment={"SERVICE_API_KEY": "literal-secret", "LANG": "en_US"},
        redaction_patterns=[r"card-\d{4}"],
    )
    payload: JsonValue = {
        "nested": ["literal-secret", {"card": "card-1234"}],
        "ordinary": "en_US",
    }
    metadata = {"note": "literal-secret metadata"}
    original_payload = copy.deepcopy(payload)
    original_metadata = copy.deepcopy(metadata)

    evidence = store.persist(
        RUN_ID,
        EvidenceType.TRACE,
        payload,
        metadata=metadata,
    )

    path = store.path_for(RUN_ID, evidence)
    encoded = path.read_bytes()
    assert b"literal-secret" not in encoded
    assert b"card-1234" not in encoded
    assert b"[REDACTED]" in encoded
    assert b"en_US" in encoded
    assert evidence.redacted is True
    assert evidence.content_hash == f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    assert payload == original_payload
    assert metadata == original_metadata
    envelope = store.load(RUN_ID, evidence)
    assert envelope["redacted"] is True
    assert evidence.metadata == {"note": "[REDACTED] metadata"}


def test_canonical_content_is_stable_and_idempotent(tmp_path: Path) -> None:
    run_store = make_run_store(tmp_path)
    store = EvidenceStore(run_store, environment={})

    first = store.persist(
        RUN_ID,
        EvidenceType.METRIC,
        {"b": 2, "a": 1},
        metadata={"z": True, "a": None},
    )
    second = store.persist(
        RUN_ID,
        EvidenceType.METRIC,
        {"a": 1, "b": 2},
        metadata={"a": None, "z": True},
    )

    assert first == second
    assert first.redacted is False
    evidence_files = list((run_store.run_dir(RUN_ID) / "evidence").glob("*.json"))
    assert evidence_files == [run_store.run_dir(RUN_ID) / first.path]


@pytest.mark.parametrize(
    "evidence_type",
    [
        EvidenceType.TRACE,
        EvidenceType.STDOUT,
        EvidenceType.STDERR,
        EvidenceType.TOOL_CALL,
        EvidenceType.EXCEPTION,
    ],
)
def test_required_evidence_types_are_content_addressed(
    tmp_path: Path,
    evidence_type: EvidenceType,
) -> None:
    run_store = make_run_store(tmp_path)
    store = EvidenceStore(run_store, environment={})

    evidence = store.persist(RUN_ID, evidence_type, {"kind": evidence_type.value})

    assert evidence.type is evidence_type
    assert evidence.path.startswith("evidence/")
    assert evidence.id.startswith("evidence_")
    assert store.load(RUN_ID, evidence)["payload"] == {"kind": evidence_type.value}


@pytest.mark.parametrize("pattern", ["(", ".*", ""])
def test_invalid_or_empty_matching_patterns_fail_before_writes(
    tmp_path: Path,
    pattern: str,
) -> None:
    run_store = make_run_store(tmp_path)

    with pytest.raises(RedactionError, match="pattern"):
        EvidenceStore(run_store, environment={}, redaction_patterns=[pattern])

    assert not list((run_store.run_dir(RUN_ID) / "evidence").iterdir())


def test_redacted_key_collision_fails_closed(tmp_path: Path) -> None:
    run_store = make_run_store(tmp_path)
    store = EvidenceStore(
        run_store,
        environment={
            "SERVICE_TOKEN": "alpha-secret",
            "OTHER_TOKEN": "beta-secret",
        },
    )

    with pytest.raises(RedactionError, match="colliding"):
        store.persist(
            RUN_ID,
            EvidenceType.ARTIFACT,
            {"alpha-secret": 1, "beta-secret": 2},
        )

    assert not list((run_store.run_dir(RUN_ID) / "evidence").iterdir())


def test_explicit_environment_secret_names_are_validated() -> None:
    with pytest.raises(RedactionError, match="missing"):
        SecretRedactor(environment={}, secret_names=["CUSTOM_VALUE"])


def test_tampered_evidence_fails_hash_verification(tmp_path: Path) -> None:
    run_store = make_run_store(tmp_path)
    store = EvidenceStore(run_store, environment={})
    evidence = store.persist(RUN_ID, EvidenceType.ARTIFACT, {"answer": 42})
    path = store.path_for(RUN_ID, evidence)
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(EvidenceCorruptionError, match="hash verification"):
        store.load(RUN_ID, evidence)


def make_scenario_execution(secret: str) -> tuple[Scenario, ScenarioExecution]:
    scenario = Scenario(
        id="test.secret",
        title="Secret redaction",
        description="Persist every execution evidence class safely.",
        category="security",
        difficulty=2,
        tags=["security"],
        required_capabilities=["invoke", "trace_tool_calls"],
        input={"request": "test"},
        fixtures={
            "tool_sequence": [
                {"tool": "lookup", "response": {"value": secret}},
            ]
        },
        execution_policy={"network": "disabled"},
        assertions=[],
        metrics=[],
    )
    attempt = AttemptRecord(
        attempt_number=1,
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
        duration_ms=5,
        output=None,
        error={"type": "adapter_child_error", "message": f"failed with {secret}"},
        trace=(
            {
                "type": "tool_call",
                "tool": "lookup",
                "arguments": {"key": secret},
                "outcome": "error",
            },
        ),
        usage={"tool_calls": 1},
        stderr=f"traceback includes {secret}",
        isolation_level="subprocess",
        timed_out=False,
        retryable=False,
    )
    result = ScenarioResult(
        scenario_id=scenario.id,
        status=ScenarioResultStatus.ERROR,
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
        duration_ms=5,
        output=None,
        error=attempt.error,
        metrics={"attempts": 1},
        evidence_refs=[],
        findings=[],
    )
    execution = ScenarioExecution(
        result=result,
        lifecycle=(
            ScenarioLifecycleState.LOADED,
            ScenarioLifecycleState.VALIDATED,
            ScenarioLifecycleState.PREPARED,
            ScenarioLifecycleState.RUNNING,
            ScenarioLifecycleState.ERROR,
            ScenarioLifecycleState.FINALIZED,
        ),
        attempts=(attempt,),
        seed=42,
        network_policy=NetworkPolicy.DISABLED,
    )
    return scenario, execution


def test_execution_evidence_is_linked_and_all_persisted_secrets_are_redacted(
    tmp_path: Path,
) -> None:
    secret = "never-persist-this-token"
    run_store = make_run_store(tmp_path)
    store = EvidenceStore(
        run_store,
        environment={"SERVICE_TOKEN": secret},
    )
    scenario, execution = make_scenario_execution(secret)

    bundle = store.record_execution(RUN_ID, scenario, execution)

    assert {
        "output",
        "trace",
        "fixtures",
        "execution",
        "usage",
        "stderr",
        "exception",
        "tool_call",
    } <= bundle.refs_by_role.keys()
    known_ids = {item.id for item in bundle.evidence}
    assert set(bundle.execution.result.evidence_refs) == known_ids
    assert all(refs for refs in bundle.refs_by_role.values())
    for evidence in bundle.evidence:
        store.load(RUN_ID, evidence)
    for evidence in bundle.evidence:
        assert secret not in store.path_for(RUN_ID, evidence).read_text(encoding="utf-8")
