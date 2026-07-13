"""Configuration loading, precedence resolution, and persistence."""

import os
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import TypeAlias, cast

import yaml
from pydantic import ValidationError

from gauntlet.config.models import GauntletConfig

RawConfig: TypeAlias = dict[str, object]
ConfigSource: TypeAlias = Mapping[str, object] | Path | None
ENV_PREFIX = "GAUNTLET_"
ARTIFACT_ROOT_ENV = "GAUNTLET_ARTIFACT_ROOT"
ENV_SEGMENT_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")


class ConfigLoadError(ValueError):
    """Raised when a configuration source cannot be resolved safely."""


def get_package_defaults() -> RawConfig:
    """Return the lowest-precedence package defaults."""
    return {
        "evaluation": {
            "benchmark_packs": ["gauntlet.agent.mvp"],
            "seed": None,
            "repeat": 1,
            "timeout_seconds": 60,
        },
        "execution": {"network": "disabled", "isolation": "subprocess"},
        "reporting": {"formats": ["json", "markdown"]},
        "scoring": {"policy": "agent_mvp_default"},
        "artifacts": {"root": str(Path.home() / ".gauntlet" / "artifacts")},
    }


def _normalize_mapping(value: Mapping[object, object], source_name: str) -> RawConfig:
    normalized: RawConfig = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ConfigLoadError(f"{source_name} contains a non-string key: {key!r}")
        if isinstance(item, Mapping):
            normalized[key] = _normalize_mapping(item, source_name)
        else:
            normalized[key] = deepcopy(item)
    return normalized


def _load_source(source: ConfigSource, source_name: str) -> RawConfig:
    if source is None:
        return {}
    if isinstance(source, Path):
        try:
            loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ConfigLoadError(f"Unable to load {source_name} from {source}: {error}") from error
        if loaded is None:
            return {}
        if not isinstance(loaded, Mapping):
            raise ConfigLoadError(f"{source_name} must contain a YAML mapping: {source}")
        return _normalize_mapping(loaded, source_name)
    if not isinstance(source, Mapping):
        raise ConfigLoadError(
            f"{source_name} must be a mapping, pathlib.Path, or None; got {type(source).__name__}"
        )
    return _normalize_mapping(cast(Mapping[object, object], source), source_name)


def _deep_merge(base: Mapping[str, object], override: Mapping[str, object]) -> RawConfig:
    merged = deepcopy(dict(base))
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(
                _normalize_mapping(current, "lower-precedence configuration"),
                _normalize_mapping(value, "higher-precedence configuration"),
            )
        else:
            merged[key] = deepcopy(value)
    return merged


def _parse_environment_value(value: str) -> object:
    if value == "":
        return ""
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError as error:
        raise ConfigLoadError(
            f"Invalid YAML value in GAUNTLET environment override: {error}"
        ) from error


def _environment_path(name: str) -> tuple[str, ...]:
    if name == ARTIFACT_ROOT_ENV:
        return ("artifacts", "root")
    suffix = name.removeprefix(ENV_PREFIX)
    raw_parts = tuple(suffix.split("__"))
    if (
        len(raw_parts) < 2
        or any(not part for part in raw_parts)
        or any(ENV_SEGMENT_PATTERN.fullmatch(part) is None for part in raw_parts)
    ):
        raise ConfigLoadError(
            f"Invalid GAUNTLET environment key {name!r}; use GAUNTLET_SECTION__FIELD"
        )
    return tuple(part.lower() for part in raw_parts)


def _validate_environment_paths(paths: list[tuple[str, ...]]) -> None:
    ordered = sorted(paths)
    for index, path in enumerate(ordered):
        for other in ordered[index + 1 :]:
            if path == other:
                raise ConfigLoadError(f"Duplicate GAUNTLET environment path: {'/'.join(path)}")
            if len(path) < len(other) and other[: len(path)] == path:
                raise ConfigLoadError(
                    "Conflicting GAUNTLET environment paths: "
                    f"{'/'.join(path)} and {'/'.join(other)}"
                )


def _set_nested(target: RawConfig, path: tuple[str, ...], value: object) -> None:
    cursor = target
    for segment in path[:-1]:
        child = cursor.setdefault(segment, {})
        if not isinstance(child, dict):
            raise ConfigLoadError(f"Conflicting GAUNTLET environment path at {segment!r}")
        cursor = child
    cursor[path[-1]] = value


def environment_overrides(environ: Mapping[str, str]) -> RawConfig:
    """Translate supported GAUNTLET environment variables into a config tree."""
    entries: list[tuple[tuple[str, ...], object]] = []
    for name, value in environ.items():
        if name.startswith(ENV_PREFIX):
            entries.append((_environment_path(name), _parse_environment_value(value)))
    _validate_environment_paths([path for path, _ in entries])

    overrides: RawConfig = {}
    for path, override_value in entries:
        _set_nested(overrides, path, override_value)
    return overrides


def resolve_config(
    *,
    project_config: ConfigSource,
    profile_defaults: ConfigSource = None,
    environ: Mapping[str, str] | None = None,
    cli_overrides: Mapping[str, object] | None = None,
    package_defaults: Mapping[str, object] | None = None,
) -> GauntletConfig:
    """Resolve and validate all five configuration precedence layers."""
    layers = (
        _load_source(
            get_package_defaults() if package_defaults is None else package_defaults,
            "package defaults",
        ),
        _load_source(profile_defaults, "profile defaults"),
        _load_source(project_config, "project config"),
        environment_overrides(os.environ if environ is None else environ),
        _load_source(cli_overrides, "CLI overrides"),
    )
    merged: RawConfig = {}
    for layer in layers:
        merged = _deep_merge(merged, layer)
    try:
        return GauntletConfig.model_validate(merged)
    except ValidationError as error:
        raise ConfigLoadError(f"Resolved configuration is invalid: {error}") from error


def save_resolved_config(config: GauntletConfig, run_dir: Path) -> Path:
    """Atomically persist the validated configuration for one run."""
    run_dir.mkdir(parents=True, exist_ok=True)
    destination = run_dir / "config.resolved.yaml"
    temporary = run_dir / ".config.resolved.yaml.tmp"
    serialized = yaml.safe_dump(
        config.model_dump(mode="json"),
        sort_keys=True,
        allow_unicode=True,
    )
    try:
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(destination)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ConfigLoadError(
            f"Unable to save resolved configuration to {destination}: {error}"
        ) from error
    return destination
