"""Tests for configuration source loading, precedence, and persistence."""

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from gauntlet.config import (
    ConfigLoadError,
    GauntletConfig,
    environment_overrides,
    get_package_defaults,
    resolve_config,
    save_resolved_config,
)


def project_config(**evaluation: object) -> dict[str, object]:
    config: dict[str, object] = {
        "project": {"name": "sample-agent"},
        "adapter": {"type": "python_callable", "target": "sample_agent.app:run"},
    }
    if evaluation:
        config["evaluation"] = evaluation
    return config


def resolve(
    *,
    project: Mapping[str, object] | Path | None = None,
    profile: Mapping[str, object] | Path | None = None,
    environ: dict[str, str] | None = None,
    cli: Mapping[str, object] | None = None,
    package: Mapping[str, object] | None = None,
) -> GauntletConfig:
    return resolve_config(
        project_config=project_config() if project is None else project,
        profile_defaults=profile,
        environ={} if environ is None else environ,
        cli_overrides=cli,
        package_defaults=get_package_defaults() if package is None else package,
    )


def test_five_level_precedence_chain() -> None:
    package = get_package_defaults()
    cast(dict[str, object], package["evaluation"])["seed"] = 1
    profile = {"evaluation": {"seed": 2}}
    project = project_config(seed=3)
    environ = {"GAUNTLET_EVALUATION__SEED": "4"}
    cli = {"evaluation": {"seed": 5}}

    assert (
        resolve(
            package=package,
            profile=profile,
            project=project,
            environ=environ,
            cli=cli,
        ).evaluation.seed
        == 5
    )
    assert (
        resolve(
            package=package,
            profile=profile,
            project=project,
            environ=environ,
        ).evaluation.seed
        == 4
    )
    assert resolve(package=package, profile=profile, project=project).evaluation.seed == 3
    assert resolve(package=package, profile=profile).evaluation.seed == 2
    assert resolve(package=package).evaluation.seed == 1


def test_nested_merge_preserves_siblings_and_replaces_lists_and_nulls() -> None:
    package = get_package_defaults()
    package_evaluation = cast(dict[str, object], package["evaluation"])
    package_evaluation["benchmark_packs"] = ["one", "two"]
    package_evaluation["seed"] = 99

    config = resolve(
        package=package,
        profile={"evaluation": {"repeat": 3}},
        project=project_config(
            benchmark_packs=["project-only"],
            seed=None,
            timeout_seconds=12.5,
        ),
    )

    assert config.evaluation.benchmark_packs == ["project-only"]
    assert config.evaluation.seed is None
    assert config.evaluation.repeat == 3
    assert config.evaluation.timeout_seconds == 12.5
    assert config.execution.network.value == "disabled"


def test_resolution_does_not_mutate_sources() -> None:
    package = get_package_defaults()
    profile = {"evaluation": {"seed": 2}}
    project = project_config(timeout_seconds=30)
    environ = {"GAUNTLET_EVALUATION__REPEAT": "2"}
    cli = {"reporting": {"formats": ["json"]}}
    sources = [package, profile, project, environ, cli]
    snapshots = deepcopy(sources)

    resolve(package=package, profile=profile, project=project, environ=environ, cli=cli)

    assert sources == snapshots


def test_environment_values_and_artifact_alias() -> None:
    overrides = environment_overrides(
        {
            "IGNORED_SECRET": "never-loaded",
            "GAUNTLET_EVALUATION__SEED": "42",
            "GAUNTLET_REPORTING__FORMATS": "[json]",
            "GAUNTLET_ARTIFACT_ROOT": "C:/tmp/gauntlet",
        }
    )

    assert overrides == {
        "evaluation": {"seed": 42},
        "reporting": {"formats": ["json"]},
        "artifacts": {"root": "C:/tmp/gauntlet"},
    }

    resolved = resolve(
        project={**project_config(), "artifacts": {"root": "project-root"}},
        environ={"GAUNTLET_ARTIFACT_ROOT": "environment-root"},
        cli={"artifacts": {"root": "cli-root"}},
    )
    assert resolved.artifacts.root == Path("cli-root")


@pytest.mark.parametrize(
    "name",
    [
        "GAUNTLET_EVALUATION_SEED",
        "GAUNTLET_EVALUATION__",
        "GAUNTLET_EVALUATION___SEED",
        "GAUNTLET_evaluation__seed",
    ],
)
def test_malformed_environment_names_are_rejected(name: str) -> None:
    with pytest.raises(ConfigLoadError, match="Invalid GAUNTLET environment key"):
        environment_overrides({name: "1"})


@pytest.mark.parametrize(
    "environ",
    [
        {
            "GAUNTLET_ARTIFACT_ROOT": "one",
            "GAUNTLET_ARTIFACTS__ROOT": "two",
        },
        {
            "GAUNTLET_EVALUATION__SEED": "1",
            "GAUNTLET_EVALUATION__SEED__VALUE": "2",
        },
    ],
)
def test_colliding_environment_paths_are_rejected(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigLoadError, match="GAUNTLET environment path"):
        environment_overrides(environ)


def test_yaml_project_and_profile_paths_are_loaded(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    project = tmp_path / "project.yaml"
    profile.write_text("evaluation:\n  repeat: 4\n", encoding="utf-8")
    project.write_text(
        "project:\n"
        "  name: file-agent\n"
        "adapter:\n"
        "  type: python_callable\n"
        "  target: file_agent:run\n",
        encoding="utf-8",
    )

    config = resolve(profile=profile, project=project)

    assert config.project.name == "file-agent"
    assert config.adapter.target == "file_agent:run"
    assert config.evaluation.repeat == 4


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("- not\n- a\n- mapping\n", "must contain a YAML mapping"),
        ("evaluation: [unterminated\n", "Unable to load project config"),
    ],
)
def test_invalid_yaml_sources_are_actionable(
    tmp_path: Path,
    contents: str,
    message: str,
) -> None:
    project = tmp_path / "invalid.yaml"
    project.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigLoadError, match=message):
        resolve(project=project)


def test_missing_non_string_and_unsupported_sources_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError, match="Unable to load project config"):
        resolve(project=tmp_path / "missing.yaml")

    with pytest.raises(ConfigLoadError, match="non-string key"):
        resolve_config(
            project_config=cast(dict[str, object], {1: "invalid"}),
            environ={},
        )

    with pytest.raises(ConfigLoadError, match="must be a mapping"):
        resolve_config(project_config=cast(Any, "project.yaml"), environ={})


def test_invalid_resolved_config_is_wrapped() -> None:
    project = project_config()
    project["unknown"] = True

    with pytest.raises(ConfigLoadError, match="Resolved configuration is invalid"):
        resolve(project=project)


def test_resolved_config_is_atomically_saved_and_round_trips(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    first = resolve(project=project_config(seed=7))
    second = resolve(project=project_config(seed=8))

    destination = save_resolved_config(first, run_dir)
    assert destination == run_dir / "config.resolved.yaml"
    assert (
        GauntletConfig.model_validate(yaml.safe_load(destination.read_text(encoding="utf-8")))
        == first
    )
    assert not (run_dir / ".config.resolved.yaml.tmp").exists()

    assert save_resolved_config(second, run_dir) == destination
    assert (
        GauntletConfig.model_validate(yaml.safe_load(destination.read_text(encoding="utf-8")))
        == second
    )
    assert not (run_dir / ".config.resolved.yaml.tmp").exists()
