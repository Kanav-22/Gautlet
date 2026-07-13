"""Load complete benchmark packs through the public WP-1.1 schemas."""

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml
from pydantic import ValidationError

from gauntlet.core import BenchmarkPackManifest, Scenario

SUPPORTED_BENCHMARK_SCHEMA_VERSIONS = frozenset({1})
_MANIFEST_FILENAMES = ("manifest.yaml", "manifest.yml")
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})


class BenchmarkPackError(ValueError):
    """A benchmark pack could not be loaded or validated."""


class BenchmarkCapabilityError(BenchmarkPackError):
    """A pack's declared capabilities are inconsistent or unavailable."""


@dataclass(frozen=True, slots=True)
class BenchmarkPackIdentity:
    """Version identity retained with every loaded benchmark pack."""

    id: str
    version: str
    schema_version: int


@dataclass(frozen=True, slots=True)
class LoadedBenchmarkPack:
    """A validated manifest and its ordered, versioned scenario set."""

    root: Path
    manifest_path: Path
    manifest: BenchmarkPackManifest
    identity: BenchmarkPackIdentity
    scenarios: tuple[Scenario, ...]
    scenario_paths: tuple[Path, ...]
    scoring_policy_path: Path


def _load_yaml_mapping(path: Path, *, document_name: str) -> dict[str, object]:
    """Read one UTF-8 YAML mapping with failures normalized for CLI reporting."""
    if path.suffix.lower() not in _YAML_SUFFIXES:
        raise BenchmarkPackError(f"{document_name} must be a .yaml or .yml file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise BenchmarkPackError(f"Unable to read {document_name} {path}: {error}") from error
    try:
        raw = cast(object, yaml.safe_load(text))
    except yaml.YAMLError as error:
        raise BenchmarkPackError(f"Invalid YAML in {document_name} {path}: {error}") from error
    if not isinstance(raw, dict):
        raise BenchmarkPackError(f"{document_name} {path} must contain a YAML mapping at its root")
    if not all(isinstance(key, str) for key in raw):
        raise BenchmarkPackError(f"{document_name} {path} contains a non-string field name")
    return cast(dict[str, object], raw)


def _format_schema_error(
    path: Path,
    *,
    document_name: str,
    error: ValidationError,
) -> BenchmarkPackError:
    """Convert Pydantic details into a compact path-aware validation failure."""
    details: list[str] = []
    for issue in error.errors(include_url=False):
        location = ".".join(str(part) for part in issue["loc"]) or "<root>"
        details.append(f"{location}: {issue['msg']}")
    return BenchmarkPackError(
        f"{document_name} schema validation failed for {path}: {'; '.join(details)}"
    )


def _locate_manifest(path: Path) -> tuple[Path, Path]:
    """Return the resolved manifest and pack root for a file or directory input."""
    expanded = path.expanduser()
    try:
        resolved = expanded.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BenchmarkPackError(
            f"Benchmark path does not exist or is unreadable: {expanded}"
        ) from error

    if resolved.is_file():
        return resolved, resolved.parent
    if not resolved.is_dir():
        raise BenchmarkPackError(f"Benchmark path is not a file or directory: {resolved}")

    manifests = [resolved / name for name in _MANIFEST_FILENAMES if (resolved / name).is_file()]
    if not manifests:
        expected = " or ".join(_MANIFEST_FILENAMES)
        raise BenchmarkPackError(f"Benchmark directory {resolved} has no {expected}")
    if len(manifests) > 1:
        names = ", ".join(path.name for path in manifests)
        raise BenchmarkPackError(
            f"Benchmark directory {resolved} has multiple manifests ({names}); keep exactly one"
        )
    return manifests[0].resolve(), resolved


def _resolve_pack_file(
    root: Path,
    reference: str,
    *,
    reference_name: str,
    yaml_only: bool,
) -> Path:
    """Resolve a manifest-owned file without permitting pack-root escape."""
    if not reference.strip():
        raise BenchmarkPackError(f"{reference_name} reference must not be empty")
    relative = Path(reference)
    if relative.is_absolute():
        raise BenchmarkPackError(f"{reference_name} reference must be relative: {reference!r}")
    try:
        resolved = (root / relative).resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise BenchmarkPackError(
            f"Unable to resolve {reference_name} reference {reference!r}: {error}"
        ) from error
    if not resolved.is_relative_to(root):
        raise BenchmarkPackError(
            f"{reference_name} reference escapes the benchmark directory: {reference!r}"
        )
    if yaml_only and resolved.suffix.lower() not in _YAML_SUFFIXES:
        raise BenchmarkPackError(
            f"{reference_name} reference must be a .yaml or .yml file: {reference!r}"
        )
    if not resolved.is_file():
        raise BenchmarkPackError(f"{reference_name} file does not exist: {reference!r}")
    return resolved


def _validate_capability_list(capabilities: list[str], *, owner: str) -> set[str]:
    """Reject blank and duplicate capability declarations."""
    blank = [capability for capability in capabilities if not capability.strip()]
    if blank:
        raise BenchmarkCapabilityError(f"{owner} declares an empty capability name")
    unique = set(capabilities)
    if len(unique) != len(capabilities):
        duplicates = sorted(
            capability for capability in unique if capabilities.count(capability) > 1
        )
        raise BenchmarkCapabilityError(
            f"{owner} declares duplicate capabilities: {', '.join(duplicates)}"
        )
    return unique


def _validate_manifest(manifest: BenchmarkPackManifest, *, path: Path) -> set[str]:
    """Validate loader-level identity, version, and capability invariants."""
    if not manifest.id.strip():
        raise BenchmarkPackError(f"Benchmark manifest {path} has an empty id")
    if not manifest.version.strip():
        raise BenchmarkPackError(f"Benchmark manifest {path} has an empty version")
    if manifest.schema_version not in SUPPORTED_BENCHMARK_SCHEMA_VERSIONS:
        supported = ", ".join(
            str(version) for version in sorted(SUPPORTED_BENCHMARK_SCHEMA_VERSIONS)
        )
        raise BenchmarkPackError(
            f"Benchmark {manifest.id!r} uses unsupported schema_version "
            f"{manifest.schema_version}; supported: {supported}"
        )
    if not manifest.scenarios:
        raise BenchmarkPackError(f"Benchmark {manifest.id!r} must list at least one scenario")
    return _validate_capability_list(
        manifest.required_capabilities,
        owner=f"Benchmark {manifest.id!r}",
    )


def load_benchmark_pack(
    path: Path | str,
    *,
    available_capabilities: Collection[str] | None = None,
) -> LoadedBenchmarkPack:
    """Load and validate a benchmark pack and optionally negotiate capabilities.

    ``available_capabilities`` is supplied by an adapter or registry when one is
    known. Omitting it validates the pack's internal declarations without
    claiming that a particular adapter can run the pack.
    """
    manifest_path, root = _locate_manifest(Path(path))
    raw_manifest = _load_yaml_mapping(manifest_path, document_name="Benchmark manifest")
    try:
        manifest = BenchmarkPackManifest.model_validate(raw_manifest)
    except ValidationError as error:
        raise _format_schema_error(
            manifest_path,
            document_name="Benchmark manifest",
            error=error,
        ) from error

    manifest_capabilities = _validate_manifest(manifest, path=manifest_path)
    if available_capabilities is not None:
        available = set(available_capabilities)
        missing = sorted(manifest_capabilities - available)
        if missing:
            raise BenchmarkCapabilityError(
                f"Benchmark {manifest.id!r} requires unavailable capabilities: "
                f"{', '.join(missing)}; available: {', '.join(sorted(available)) or '<none>'}"
            )

    scenarios: list[Scenario] = []
    scenario_paths: list[Path] = []
    seen_paths: set[Path] = set()
    seen_ids: set[str] = set()
    for index, reference in enumerate(manifest.scenarios):
        scenario_path = _resolve_pack_file(
            root,
            reference,
            reference_name=f"Scenario #{index + 1}",
            yaml_only=True,
        )
        if scenario_path in seen_paths:
            raise BenchmarkPackError(
                f"Benchmark {manifest.id!r} lists scenario {reference!r} twice"
            )
        seen_paths.add(scenario_path)

        raw_scenario = _load_yaml_mapping(scenario_path, document_name="Scenario")
        try:
            scenario = Scenario.model_validate(raw_scenario)
        except ValidationError as error:
            raise _format_schema_error(
                scenario_path,
                document_name="Scenario",
                error=error,
            ) from error
        if not scenario.id.strip():
            raise BenchmarkPackError(f"Scenario {scenario_path} has an empty id")
        if scenario.id in seen_ids:
            raise BenchmarkPackError(
                f"Benchmark {manifest.id!r} contains duplicate scenario id {scenario.id!r}"
            )
        seen_ids.add(scenario.id)

        scenario_capabilities = _validate_capability_list(
            scenario.required_capabilities,
            owner=f"Scenario {scenario.id!r}",
        )
        undeclared = sorted(scenario_capabilities - manifest_capabilities)
        if undeclared:
            raise BenchmarkCapabilityError(
                f"Scenario {scenario.id!r} requires capabilities not declared by benchmark "
                f"{manifest.id!r}: {', '.join(undeclared)}"
            )
        scenarios.append(scenario)
        scenario_paths.append(scenario_path)

    scoring_policy_path = _resolve_pack_file(
        root,
        manifest.scoring_policy,
        reference_name="Scoring policy",
        yaml_only=True,
    )
    identity = BenchmarkPackIdentity(
        id=manifest.id,
        version=manifest.version,
        schema_version=manifest.schema_version,
    )
    return LoadedBenchmarkPack(
        root=root,
        manifest_path=manifest_path,
        manifest=manifest,
        identity=identity,
        scenarios=tuple(scenarios),
        scenario_paths=tuple(scenario_paths),
        scoring_policy_path=scoring_policy_path,
    )
