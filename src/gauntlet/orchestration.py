"""Project-level orchestration for the public ``gauntlet evaluate`` command."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from gauntlet import __version__
from gauntlet.benchmarks import LoadedBenchmarkPack, resolve_benchmark_reference
from gauntlet.config import GauntletConfig, NetworkPolicy, resolve_config
from gauntlet.evidence import RunArtifactStore
from gauntlet.reporting import (
    EvaluationPipeline,
    EvaluationPipelineResult,
    EvaluationRequest,
    ExecutionMode,
)
from gauntlet.scoring import ScoringPolicy, load_scoring_policy

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ProjectEvaluationError(ValueError):
    """A project cannot be converted into a safe evaluation request."""


@dataclass(frozen=True, slots=True)
class PreparedEvaluation:
    """Resolved, validated inputs ready for the library evaluation pipeline."""

    project_root: Path
    profile_id: str
    benchmark: LoadedBenchmarkPack
    config: GauntletConfig
    policy: ScoringPolicy
    environment: dict[str, JsonValue]
    environment_fingerprint: str


def prepare_evaluation(
    path: Path | str,
    *,
    profile: str | None = None,
    benchmark: str | None = None,
    scenario: str | None = None,
    seed: int | None = None,
    repeat: int | None = None,
    offline: bool = False,
    artifact_root: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> PreparedEvaluation:
    """Resolve config precedence, benchmark selection, and stable environment facts."""

    project_root = _project_root(path)
    project_config = project_root / ".gauntlet" / "config.yaml"
    if not project_config.is_file():
        raise ProjectEvaluationError(
            f"GAUNTLET project config not found: {project_config}; run 'gauntlet init {project_root}'"
        )
    profile_id, profile_source = _profile_source(project_root, profile)
    environment_source = dict(os.environ if environ is None else environ)

    preliminary_overrides = _cli_overrides(
        seed=seed,
        repeat=repeat,
        offline=offline,
        artifact_root=artifact_root,
    )
    preliminary = resolve_config(
        project_config=project_config,
        profile_defaults=profile_source,
        environ=environment_source,
        cli_overrides=preliminary_overrides,
    )
    selector = benchmark or _configured_benchmark(preliminary)
    loaded = resolve_benchmark_reference(project_root, selector)
    if scenario is not None:
        loaded = _select_scenario(loaded, scenario)
    if offline:
        loaded = _force_offline(loaded)

    final_overrides = _cli_overrides(
        seed=seed,
        repeat=repeat,
        offline=offline,
        artifact_root=artifact_root,
        benchmark_id=loaded.identity.id,
    )
    config = resolve_config(
        project_config=project_config,
        profile_defaults=profile_source,
        environ=environment_source,
        cli_overrides=final_overrides,
    )
    if not config.artifacts.root.is_absolute():
        normalized_root = (project_root / config.artifacts.root).resolve()
        config = config.model_copy(
            update={"artifacts": config.artifacts.model_copy(update={"root": normalized_root})}
        )
    try:
        policy = load_scoring_policy(loaded.scoring_policy_path)
    except ValueError as error:
        raise ProjectEvaluationError(str(error)) from error

    environment = _environment_facts()
    return PreparedEvaluation(
        project_root=project_root,
        profile_id=profile_id,
        benchmark=loaded,
        config=config,
        policy=policy,
        environment=environment,
        environment_fingerprint=_environment_fingerprint(environment),
    )


def evaluate_project(
    path: Path | str,
    *,
    profile: str | None = None,
    benchmark: str | None = None,
    scenario: str | None = None,
    seed: int | None = None,
    repeat: int | None = None,
    offline: bool = False,
    artifact_root: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    execution_mode: ExecutionMode | None = None,
) -> EvaluationPipelineResult:
    """Execute one fully resolved local project evaluation."""

    prepared = prepare_evaluation(
        path,
        profile=profile,
        benchmark=benchmark,
        scenario=scenario,
        seed=seed,
        repeat=repeat,
        offline=offline,
        artifact_root=artifact_root,
        environ=environ,
    )
    redaction_environment = dict(os.environ if environ is None else environ)
    selected_mode = execution_mode
    if selected_mode is None:
        selected_mode = (
            ExecutionMode.DETERMINISTIC_FIXTURE
            if prepared.config.execution.network is NetworkPolicy.DISABLED
            else ExecutionMode.LIVE_SERVICE
        )
    request = EvaluationRequest(
        project_id=prepared.config.project.name,
        profile_id=prepared.profile_id,
        benchmark=prepared.benchmark,
        resolved_config=prepared.config,
        project_root=prepared.project_root,
        environment_fingerprint=prepared.environment_fingerprint,
        environment=prepared.environment,
        policy=prepared.policy,
        execution_mode=selected_mode,
    )
    return EvaluationPipeline(
        RunArtifactStore(prepared.config.artifacts.root),
        redaction_environment=redaction_environment,
    ).evaluate(request)


def _project_root(path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ProjectEvaluationError(
            f"Project path does not exist or is unreadable: {candidate}"
        ) from error
    if not resolved.is_dir():
        raise ProjectEvaluationError(f"Project path is not a directory: {resolved}")
    return resolved


def _profile_source(project_root: Path, profile: str | None) -> tuple[str, Path | None]:
    profile_id = profile or "default"
    if _PROFILE_NAME.fullmatch(profile_id) is None or Path(profile_id).name != profile_id:
        raise ProjectEvaluationError(
            "Profile must be a safe project-local name containing letters, numbers, '.', '_', or '-'"
        )
    suffix = Path(profile_id).suffix.lower()
    if suffix in {".yaml", ".yml"}:
        filename = profile_id
        normalized_id = Path(profile_id).stem
    else:
        filename = profile_id + ".yaml"
        normalized_id = profile_id
    source = project_root / ".gauntlet" / "profiles" / filename
    if source.is_file():
        return normalized_id, source
    if profile is not None:
        raise ProjectEvaluationError(f"Evaluation profile not found: {source}")
    return normalized_id, None


def _cli_overrides(
    *,
    seed: int | None,
    repeat: int | None,
    offline: bool,
    artifact_root: Path | str | None,
    benchmark_id: str | None = None,
) -> dict[str, object]:
    if repeat is not None and repeat < 1:
        raise ProjectEvaluationError("--repeat must be at least 1")
    evaluation: dict[str, object] = {}
    if benchmark_id is not None:
        evaluation["benchmark_packs"] = [benchmark_id]
    if seed is not None:
        evaluation["seed"] = seed
    if repeat is not None:
        evaluation["repeat"] = repeat

    overrides: dict[str, object] = {}
    if evaluation:
        overrides["evaluation"] = evaluation
    if offline:
        overrides["execution"] = {"network": "disabled"}
    if artifact_root is not None:
        root = Path(artifact_root).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        overrides["artifacts"] = {"root": str(root)}
    return overrides


def _configured_benchmark(config: GauntletConfig) -> str:
    selectors = config.evaluation.benchmark_packs
    if len(selectors) != 1:
        raise ProjectEvaluationError(
            "The MVP evaluates exactly one benchmark pack at a time; use --benchmark to select one"
        )
    selector = selectors[0]
    if not selector.strip():
        raise ProjectEvaluationError("Configured benchmark selector must be non-blank")
    return selector


def _select_scenario(pack: LoadedBenchmarkPack, scenario_id: str) -> LoadedBenchmarkPack:
    if not scenario_id.strip():
        raise ProjectEvaluationError("--scenario must be a non-blank exact scenario ID")
    matches = [
        (scenario, path)
        for scenario, path in zip(pack.scenarios, pack.scenario_paths, strict=True)
        if scenario.id == scenario_id
    ]
    if not matches:
        available = ", ".join(scenario.id for scenario in pack.scenarios)
        raise ProjectEvaluationError(
            f"Scenario {scenario_id!r} is not in benchmark {pack.identity.id!r}; "
            f"available: {available}"
        )
    selected, selected_path = matches[0]
    return replace(pack, scenarios=(selected,), scenario_paths=(selected_path,))


def _force_offline(pack: LoadedBenchmarkPack) -> LoadedBenchmarkPack:
    """Apply the highest-precedence CLI network policy to every selected scenario."""

    scenarios = tuple(
        scenario.model_copy(
            update={
                "execution_policy": {
                    **scenario.execution_policy,
                    "network": "disabled",
                }
            }
        )
        for scenario in pack.scenarios
    )
    return replace(pack, scenarios=scenarios)


def _environment_facts() -> dict[str, JsonValue]:
    return {
        "gauntlet_version": __version__,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine() or "unknown",
    }


def _environment_fingerprint(environment: Mapping[str, JsonValue]) -> str:
    payload = json.dumps(
        cast(dict[str, JsonValue], dict(environment)),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
