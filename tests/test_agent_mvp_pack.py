"""Acceptance tests for the deterministic flagship Agent MVP benchmark pack."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from gauntlet.adapters import PythonCallableAdapter
from gauntlet.benchmarks import LoadedBenchmarkPack, load_benchmark_pack
from gauntlet.core.models import Evidence, EvidenceType, ScenarioResultStatus
from gauntlet.evidence import ScenarioEvidenceBundle
from gauntlet.execution import (
    AssertionEngine,
    PythonCallableAdapterFactory,
    ScenarioExecution,
    ScenarioExecutor,
)
from gauntlet.scoring import agent_mvp_default_policy, load_scoring_policy

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = REPOSITORY_ROOT / "benchmarks" / "agent_mvp"
EXAMPLES_ROOT = REPOSITORY_ROOT / "examples"

SCENARIO_IDS = (
    "agent.direct_answer",
    "agent.single_tool_call",
    "agent.two_step_dependent_calls",
    "agent.unavailable_tool",
    "agent.tool_timeout_recovery",
    "agent.malformed_tool_response",
    "agent.contradictory_instructions",
    "agent.malicious_retrieved_content",
    "agent.missing_required_information",
    "agent.loop_resistance",
    "agent.state_reset_between_tasks",
    "agent.long_irrelevant_context",
    "agent.forbidden_tool_attempt",
    "agent.hallucinated_completion",
    "agent.reproducibility_across_seeds",
)

EXPECTED_FAILURES = {
    "sample_agent.variants.correct:run": frozenset(),
    "sample_agent.variants.inefficient:run": frozenset(
        scenario_id
        for scenario_id in SCENARIO_IDS
        if scenario_id
        not in {
            "agent.state_reset_between_tasks",
            "agent.forbidden_tool_attempt",
            "agent.reproducibility_across_seeds",
        }
    ),
    "sample_agent.variants.hallucinating:run": frozenset(
        scenario_id
        for scenario_id in SCENARIO_IDS
        if scenario_id
        not in {
            "agent.direct_answer",
            "agent.state_reset_between_tasks",
            "agent.forbidden_tool_attempt",
            "agent.reproducibility_across_seeds",
        }
    ),
    "sample_agent.variants.loop_prone:run": frozenset(
        {
            "agent.unavailable_tool",
            "agent.malformed_tool_response",
            "agent.missing_required_information",
            "agent.loop_resistance",
        }
    ),
    "sample_agent.variants.injection_vulnerable:run": frozenset(
        {
            "agent.contradictory_instructions",
            "agent.malicious_retrieved_content",
            "agent.forbidden_tool_attempt",
        }
    ),
    "sample_agent.variants.recovery_capable:run": frozenset(),
}


def _loaded_pack() -> LoadedBenchmarkPack:
    return load_benchmark_pack(
        PACK_ROOT,
        available_capabilities={"invoke", "trace_tool_calls"},
    )


def _evidence_bundle(execution: ScenarioExecution) -> ScenarioEvidenceBundle:
    role_types = {
        "output": EvidenceType.ARTIFACT,
        "trace": EvidenceType.TRACE,
        "fixtures": EvidenceType.ARTIFACT,
        "execution": EvidenceType.METRIC,
    }
    evidence = tuple(
        Evidence(
            id=f"evidence-{role}",
            type=evidence_type,
            path=f"evidence/{role}.json",
            content_hash=f"sha256:{role}",
            redacted=False,
            metadata={"role": role},
        )
        for role, evidence_type in role_types.items()
    )
    refs: dict[str, tuple[str, ...]] = {role: (f"evidence-{role}",) for role in role_types}
    return ScenarioEvidenceBundle(execution=execution, evidence=evidence, refs_by_role=refs)


def _variant_failures(target: str) -> frozenset[str]:
    pack = _loaded_pack()
    executor = ScenarioExecutor(
        PythonCallableAdapterFactory(target, project_root=EXAMPLES_ROOT),
        timeout_seconds=2,
        seed=42,
    )
    failures: set[str] = set()
    for scenario in pack.scenarios:
        execution = executor.execute(scenario)
        evaluated = AssertionEngine().evaluate(scenario, _evidence_bundle(execution))
        if evaluated.execution.result.status is not ScenarioResultStatus.PASSED:
            failures.add(scenario.id)
    return frozenset(failures)


def test_flagship_pack_has_exact_order_policy_and_external_seed_control() -> None:
    pack = _loaded_pack()

    assert pack.identity.id == "gauntlet.agent.mvp"
    assert pack.identity.version == "0.1.0"
    assert tuple(scenario.id for scenario in pack.scenarios) == SCENARIO_IDS
    assert len(pack.scenario_paths) == 15
    assert all("seed" not in scenario.execution_policy for scenario in pack.scenarios)
    assert {scenario.execution_policy.get("timeout_seconds") for scenario in pack.scenarios} == {5}
    assert load_scoring_policy(pack.scoring_policy_path) == agent_mvp_default_policy()

    timeout_scenario = pack.scenarios[4]
    raw_sequence = timeout_scenario.fixtures["tool_sequence"]
    assert isinstance(raw_sequence, list)
    first_fixture = raw_sequence[0]
    assert isinstance(first_fixture, dict)
    assert first_fixture == {
        "tool": "lookup",
        "arguments": {"key": "case-4"},
        "error": {"code": "timeout", "message": "synthetic tool deadline exceeded"},
    }
    assert "delay_ms" not in first_fixture

    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    force_include = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include == {"benchmarks/agent_mvp": "gauntlet/benchmarks/agent_mvp"}


@pytest.mark.parametrize(("target", "expected_failures"), EXPECTED_FAILURES.items())
def test_golden_variants_have_the_declared_discriminating_outcomes(
    target: str,
    expected_failures: frozenset[str],
) -> None:
    assert _variant_failures(target) == expected_failures


def test_state_reset_probe_requires_and_proves_a_real_adapter_reset() -> None:
    probe = _loaded_pack().scenarios[10]
    precondition = {
        "task": "state_precondition",
        "session_key": "synthetic-state",
        "value": "dirty",
    }
    with PythonCallableAdapter(
        "sample_agent.variants.correct:run",
        project_root=EXAMPLES_ROOT,
        tool_sequence=[],
        seed=42,
    ) as adapter:
        assert adapter.invoke(precondition)["value"] == "dirty"
        dirty_probe = adapter.invoke(probe.input)
        assert dirty_probe == {"completed": False, "value": "dirty", "saved": False}

        adapter.reset()
        clean_probe = adapter.invoke(probe.input)

    assert clean_probe == {"completed": True, "value": "clean", "saved": False}


def test_reproducibility_probe_is_byte_identical_across_external_seeds() -> None:
    scenario = _loaded_pack().scenarios[14]
    assert "seed" not in scenario.execution_policy
    canonical_runs: list[bytes] = []

    for seed in (7, 42, 99):
        with PythonCallableAdapter(
            "sample_agent.variants.correct:run",
            project_root=EXAMPLES_ROOT,
            tool_sequence=[],
            seed=seed,
        ) as adapter:
            payload = {
                "output": adapter.invoke(scenario.input),
                "trace": adapter.trace(),
                "usage": adapter.usage(),
            }
        canonical_runs.append(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    assert len(set(canonical_runs)) == 1
