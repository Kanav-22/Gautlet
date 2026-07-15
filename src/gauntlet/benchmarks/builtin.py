"""Resolve local benchmark packs without plugin discovery or network I/O."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from gauntlet.benchmarks.loader import (
    BenchmarkPackError,
    LoadedBenchmarkPack,
    load_benchmark_pack,
)

BUILTIN_AGENT_MVP_ID = "gauntlet.agent.mvp"
_BUILTIN_RESOURCE_DIRECTORY = "agent_mvp"


def _source_checkout_candidate() -> Path:
    """Return the repository pack location when this module is imported from src/."""

    return Path(__file__).resolve().parents[3] / "benchmarks" / "agent_mvp"


def _validated_builtin_candidate(path: Path, *, label: str) -> tuple[Path | None, str | None]:
    """Validate one possible built-in location and preserve an actionable diagnostic."""

    try:
        loaded = load_benchmark_pack(path)
    except BenchmarkPackError as error:
        return None, f"{label} {path}: {error}"
    if loaded.identity.id != BUILTIN_AGENT_MVP_ID:
        return (
            None,
            f"{label} {path}: expected id {BUILTIN_AGENT_MVP_ID!r}, found {loaded.identity.id!r}",
        )
    return loaded.root, None


def builtin_agent_mvp_path() -> Path:
    """Return the real installed or source-checkout path for the flagship pack.

    Installed wheels are unpacked by Python installers, so their resource is a
    real filesystem directory. A non-filesystem ``Traversable`` is not returned
    after an ``as_file`` context because that path would be temporary.
    """

    diagnostics: list[str] = []
    packaged_candidate: Path | None = None
    try:
        resource = files("gauntlet.benchmarks").joinpath(_BUILTIN_RESOURCE_DIRECTORY)
        packaged_candidate = Path(str(resource))
    except (ModuleNotFoundError, OSError, RuntimeError, TypeError) as error:
        diagnostics.append(f"packaged resource lookup failed: {error}")
    else:
        resolved, diagnostic = _validated_builtin_candidate(
            packaged_candidate,
            label="packaged resource",
        )
        if resolved is not None:
            return resolved
        assert diagnostic is not None
        diagnostics.append(diagnostic)

    source_candidate = _source_checkout_candidate()
    if packaged_candidate is None or source_candidate.resolve(strict=False) != (
        packaged_candidate.resolve(strict=False)
    ):
        resolved, diagnostic = _validated_builtin_candidate(
            source_candidate,
            label="source checkout fallback",
        )
        if resolved is not None:
            return resolved
        assert diagnostic is not None
        diagnostics.append(diagnostic)

    checked = "; ".join(diagnostics) or "no candidate locations were available"
    raise BenchmarkPackError(
        f"Built-in benchmark {BUILTIN_AGENT_MVP_ID!r} is unavailable. Checked: {checked}. "
        "Reinstall GAUNTLET or provide an explicit benchmark path."
    )


def _project_root(path: Path) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BenchmarkPackError(f"Project root does not exist or is unreadable: {path}") from error
    if not resolved.is_dir():
        raise BenchmarkPackError(f"Project root must be a directory: {resolved}")
    return resolved


def _contained_candidate(root: Path, relative: Path, *, selector: str) -> Path:
    try:
        candidate = (root / relative).resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise BenchmarkPackError(
            f"Unable to resolve benchmark selector {selector!r}: {error}"
        ) from error
    if not candidate.is_relative_to(root):
        raise BenchmarkPackError(
            f"Relative benchmark selector {selector!r} escapes project root {root}. "
            "Use an explicit absolute path for an external pack."
        )
    return candidate


def _is_explicit_relative(selector: str, path: Path) -> bool:
    return (
        selector.startswith(".")
        or "/" in selector
        or "\\" in selector
        or path.suffix.lower() in {".yaml", ".yml"}
    )


def resolve_benchmark_reference(project_root: Path, selector: str) -> LoadedBenchmarkPack:
    """Resolve an explicit, built-in, or project-local benchmark reference.

    Resolution is local-only and deterministic. Explicit absolute paths may
    point outside the project; relative paths must stay within it. The reserved
    built-in ID cannot be shadowed by a same-named project directory.
    """

    root = _project_root(project_root)
    normalized = selector.strip()
    if not normalized:
        raise BenchmarkPackError(
            "Benchmark selector must not be blank; provide a pack path or "
            f"{BUILTIN_AGENT_MVP_ID!r}."
        )
    try:
        selector_path = Path(normalized).expanduser()
    except (OSError, RuntimeError) as error:
        raise BenchmarkPackError(f"Invalid benchmark selector {selector!r}: {error}") from error

    if selector_path.is_absolute():
        try:
            exists = selector_path.exists()
        except OSError as error:
            raise BenchmarkPackError(
                f"Explicit benchmark path is unreadable: {selector_path}"
            ) from error
        if not exists:
            raise BenchmarkPackError(f"Explicit benchmark path does not exist: {selector_path}")
        return load_benchmark_pack(selector_path)

    if _is_explicit_relative(normalized, selector_path):
        candidate = _contained_candidate(root, selector_path, selector=normalized)
        if not candidate.exists():
            raise BenchmarkPackError(
                f"Explicit benchmark path {normalized!r} does not exist under project root "
                f"{root}: {candidate}"
            )
        return load_benchmark_pack(candidate)

    if normalized == BUILTIN_AGENT_MVP_ID:
        return load_benchmark_pack(builtin_agent_mvp_path())

    candidates = (
        _contained_candidate(root, selector_path, selector=normalized),
        _contained_candidate(
            root / ".gauntlet" / "benchmarks",
            selector_path,
            selector=normalized,
        ),
        _contained_candidate(
            root / "benchmarks",
            selector_path,
            selector=normalized,
        ),
    )
    for candidate in candidates:
        if candidate.exists():
            return load_benchmark_pack(candidate)

    checked = ", ".join(str(candidate) for candidate in candidates)
    raise BenchmarkPackError(
        f"Benchmark selector {normalized!r} could not be resolved. Checked: {checked}. "
        f"Use {BUILTIN_AGENT_MVP_ID!r} or provide an explicit pack path."
    )
