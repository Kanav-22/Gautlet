"""Static, side-effect-free discovery for Python-callable projects."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

import yaml

_IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".gauntlet",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
_LIKELY_CALLABLE_NAMES = frozenset({"agent", "invoke", "main", "run"})
_MAX_SOURCE_BYTES = 1024 * 1024
_FRAMEWORK_IMPORTS = {
    "autogen": "AutoGen",
    "crewai": "CrewAI",
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "llama_index": "LlamaIndex",
    "openai": "OpenAI SDK",
    "semantic_kernel": "Semantic Kernel",
}


class InspectionInputError(ValueError):
    """The requested inspection path cannot be inspected safely."""


class InspectionLevel(StrEnum):
    """Stable severity levels for inspection findings."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class PythonProjectKind(StrEnum):
    """Coarse project shapes detectable without importing user code."""

    UNKNOWN = "unknown"
    MODULE = "python_module"
    PACKAGE = "python_package"
    PROJECT = "python_project"


@dataclass(frozen=True, slots=True)
class InspectionFinding:
    """One deterministic, actionable result from static inspection."""

    code: str
    level: InspectionLevel
    message: str
    action: str | None = None
    path: Path | None = None
    line: int | None = None


@dataclass(frozen=True, slots=True)
class CallableCandidate:
    """A module-level callable that may satisfy the Python adapter contract."""

    target: str
    path: Path
    line: int
    async_callable: bool
    accepts_payload: bool
    accepts_tools: bool


@dataclass(frozen=True, slots=True)
class InspectionResult:
    """Static project inventory and adapter recommendation."""

    root: Path
    project_kind: PythonProjectKind
    packages: tuple[str, ...]
    framework_hints: tuple[str, ...]
    callables: tuple[CallableCandidate, ...]
    configured_target: str | None
    recommended_target: str | None
    recommended_adapter: str
    findings: tuple[InspectionFinding, ...]

    @property
    def ok(self) -> bool:
        """Whether inspection found no blocking errors."""

        return all(finding.level is not InspectionLevel.ERROR for finding in self.findings)


@dataclass(frozen=True, slots=True)
class _ParsedSource:
    path: Path
    module: str
    tree: ast.Module


def _source_files(root: Path, requested_file: Path | None) -> tuple[Path, ...]:
    if requested_file is not None:
        if requested_file.suffix.lower() != ".py":
            raise InspectionInputError(f"Inspection file must be Python source: {requested_file}")
        return (requested_file,)

    files: list[Path] = []
    for candidate in root.rglob("*.py"):
        try:
            relative = candidate.relative_to(root)
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        if any(part in _IGNORED_DIRECTORIES for part in relative.parts[:-1]):
            continue
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        files.append(resolved)
    return tuple(sorted(set(files), key=lambda path: path.relative_to(root).as_posix()))


def _module_name(root: Path, path: Path) -> str | None:
    source_root = root / "src"
    base = source_root if source_root.is_dir() and path.is_relative_to(source_root) else root
    try:
        relative = path.relative_to(base)
    except ValueError:
        return None
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def _read_and_parse(path: Path) -> ast.Module:
    try:
        if path.stat().st_size > _MAX_SOURCE_BYTES:
            raise InspectionInputError(
                f"Python source exceeds the {_MAX_SOURCE_BYTES}-byte inspection limit: {path}"
            )
        source = path.read_text(encoding="utf-8")
    except InspectionInputError:
        raise
    except (OSError, UnicodeError) as error:
        raise InspectionInputError(f"Unable to read Python source {path}: {error}") from error
    try:
        return ast.parse(source, filename=str(path), type_comments=True)
    except SyntaxError as error:
        location = f"line {error.lineno}" if error.lineno is not None else "unknown line"
        raise InspectionInputError(
            f"Invalid Python syntax in {path} at {location}: {error.msg}"
        ) from error


def _signature_support(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[bool, bool]:
    positional = [*node.args.posonlyargs, *node.args.args]
    accepts_payload = bool(positional or node.args.vararg is not None)
    names = {argument.arg for argument in positional}
    names.update(argument.arg for argument in node.args.kwonlyargs)
    accepts_tools = "tools" in names or node.args.kwarg is not None
    return accepts_payload, accepts_tools


def _candidate_nodes(tree: ast.Module) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
    candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        _, accepts_tools = _signature_support(node)
        if node.name in _LIKELY_CALLABLE_NAMES or accepts_tools:
            candidates.append(node)
    return tuple(candidates)


def _frameworks(tree: ast.Module) -> set[str]:
    frameworks: set[str] = set()
    for node in ast.walk(tree):
        imported: list[str] = []
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
        for module in imported:
            framework = _FRAMEWORK_IMPORTS.get(module.partition(".")[0])
            if framework is not None:
                frameworks.add(framework)
    return frameworks


def _configured_target(root: Path) -> tuple[str | None, InspectionFinding | None]:
    config_path = root / ".gauntlet" / "config.yaml"
    if not config_path.is_file():
        return None, InspectionFinding(
            code="missing_gauntlet_config",
            level=InspectionLevel.WARNING,
            message="No .gauntlet/config.yaml was found.",
            action="Run 'gauntlet init' or add a project-local configuration file.",
            path=config_path,
        )
    try:
        raw = cast(object, yaml.safe_load(config_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        return None, InspectionFinding(
            code="invalid_gauntlet_config",
            level=InspectionLevel.ERROR,
            message=f"Unable to read .gauntlet/config.yaml: {error}",
            action="Repair the YAML configuration and run inspect again.",
            path=config_path,
        )
    if not isinstance(raw, dict):
        return None, InspectionFinding(
            code="invalid_gauntlet_config",
            level=InspectionLevel.ERROR,
            message=".gauntlet/config.yaml must contain a YAML mapping.",
            action="Repair the YAML configuration and run inspect again.",
            path=config_path,
        )
    adapter = raw.get("adapter")
    target = adapter.get("target") if isinstance(adapter, dict) else None
    if not isinstance(target, str) or not target.strip():
        return None, InspectionFinding(
            code="missing_adapter_target",
            level=InspectionLevel.ERROR,
            message="The project configuration has no non-blank adapter.target.",
            action="Set adapter.target to 'module:callable'.",
            path=config_path,
        )
    return target.strip(), None


def _valid_target_syntax(target: str) -> bool:
    module, separator, attributes = target.partition(":")
    return bool(
        separator
        and module
        and attributes
        and all(part.isidentifier() for part in module.split("."))
        and all(part.isidentifier() for part in attributes.split("."))
    )


def _project_kind(
    root: Path, packages: tuple[str, ...], sources: tuple[_ParsedSource, ...]
) -> PythonProjectKind:
    if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file():
        return PythonProjectKind.PROJECT
    if packages:
        return PythonProjectKind.PACKAGE
    if sources:
        return PythonProjectKind.MODULE
    return PythonProjectKind.UNKNOWN


def _package_names(root: Path, sources: list[_ParsedSource]) -> tuple[str, ...]:
    names: set[str] = set()
    for source in sources:
        top_level = source.module.partition(".")[0]
        if (root / top_level / "__init__.py").is_file() or (
            root / "src" / top_level / "__init__.py"
        ).is_file():
            names.add(top_level)
    return tuple(sorted(names))


def inspect_project(path: Path | str) -> InspectionResult:
    """Inspect Python source without importing or executing the target project."""

    requested = Path(path).expanduser()
    try:
        resolved = requested.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InspectionInputError(
            f"Inspection path does not exist or is unreadable: {requested}"
        ) from error
    if not resolved.is_dir() and not resolved.is_file():
        raise InspectionInputError(f"Inspection path is not a file or directory: {resolved}")
    requested_file = resolved if resolved.is_file() else None
    root = resolved.parent if requested_file is not None else resolved

    findings: list[InspectionFinding] = []
    configured_target, config_finding = _configured_target(root)
    if config_finding is not None:
        findings.append(config_finding)
    if configured_target is not None and not _valid_target_syntax(configured_target):
        findings.append(
            InspectionFinding(
                code="invalid_adapter_target",
                level=InspectionLevel.ERROR,
                message=f"Configured adapter target {configured_target!r} is not 'module:callable'.",
                action="Use dotted Python identifiers on both sides of one colon.",
                path=root / ".gauntlet" / "config.yaml",
            )
        )

    parsed: list[_ParsedSource] = []
    for source_path in _source_files(root, requested_file):
        module = _module_name(root, source_path)
        if module is None:
            findings.append(
                InspectionFinding(
                    code="unimportable_source_path",
                    level=InspectionLevel.WARNING,
                    message=f"Python source is not on a conventional import path: {source_path.relative_to(root)}",
                    action="Use identifier-safe package and module names, optionally under src/.",
                    path=source_path,
                )
            )
            continue
        try:
            tree = _read_and_parse(source_path)
        except InspectionInputError as error:
            findings.append(
                InspectionFinding(
                    code="uninspectable_source",
                    level=InspectionLevel.WARNING,
                    message=str(error),
                    action="Repair or exclude this source file, then run inspect again.",
                    path=source_path,
                )
            )
            continue
        parsed.append(_ParsedSource(path=source_path, module=module, tree=tree))

    candidates: list[CallableCandidate] = []
    frameworks: set[str] = set()
    for source in parsed:
        frameworks.update(_frameworks(source.tree))
        for node in _candidate_nodes(source.tree):
            accepts_payload, accepts_tools = _signature_support(node)
            candidates.append(
                CallableCandidate(
                    target=f"{source.module}:{node.name}",
                    path=source.path,
                    line=node.lineno,
                    async_callable=isinstance(node, ast.AsyncFunctionDef),
                    accepts_payload=accepts_payload,
                    accepts_tools=accepts_tools,
                )
            )

    candidates.sort(
        key=lambda candidate: (
            candidate.target != configured_target,
            not candidate.accepts_payload,
            not candidate.accepts_tools,
            candidate.target.rpartition(":")[2] != "run",
            candidate.target,
        )
    )
    candidate_targets = {candidate.target for candidate in candidates}
    recommended = next(
        (
            candidate.target
            for candidate in candidates
            if candidate.accepts_payload
            and candidate.accepts_tools
            and not candidate.async_callable
        ),
        None,
    )

    if configured_target is not None and _valid_target_syntax(configured_target):
        if configured_target in candidate_targets:
            match = next(
                candidate for candidate in candidates if candidate.target == configured_target
            )
            findings.append(
                InspectionFinding(
                    code="configured_target_found",
                    level=InspectionLevel.INFO,
                    message=f"Configured Python callable found statically: {configured_target}.",
                    path=match.path,
                    line=match.line,
                )
            )
            if not match.accepts_payload or not match.accepts_tools or match.async_callable:
                findings.append(
                    InspectionFinding(
                        code="incompatible_adapter_signature",
                        level=InspectionLevel.ERROR,
                        message=(
                            f"Configured target {configured_target} does not have the synchronous "
                            "run(payload, *, tools) adapter shape."
                        ),
                        action="Expose a synchronous shim that accepts one payload and the injected tools registry.",
                        path=match.path,
                        line=match.line,
                    )
                )
            else:
                recommended = configured_target
        else:
            findings.append(
                InspectionFinding(
                    code="configured_target_not_found",
                    level=InspectionLevel.ERROR,
                    message=f"Configured Python callable was not found statically: {configured_target}.",
                    action="Correct adapter.target or expose the callable in project source.",
                    path=root / ".gauntlet" / "config.yaml",
                )
            )

    if not parsed:
        findings.append(
            InspectionFinding(
                code="no_python_source",
                level=InspectionLevel.ERROR,
                message="No inspectable Python source was found.",
                action="Point inspect at a Python project, package, or .py file.",
                path=root,
            )
        )
    elif not candidates:
        findings.append(
            InspectionFinding(
                code="no_callable_candidate",
                level=InspectionLevel.WARNING,
                message="No likely Python-callable adapter entry point was found.",
                action="Expose a synchronous run(payload, *, tools) shim and configure adapter.target.",
                path=root,
            )
        )

    for framework in sorted(frameworks):
        findings.append(
            InspectionFinding(
                code="framework_import_detected",
                level=InspectionLevel.INFO,
                message=f"Static imports suggest {framework} is used.",
                action="Keep GAUNTLET framework-agnostic by exposing a thin python_callable shim.",
                path=root,
            )
        )

    findings.sort(
        key=lambda finding: (
            {InspectionLevel.ERROR: 0, InspectionLevel.WARNING: 1, InspectionLevel.INFO: 2}[
                finding.level
            ],
            finding.code,
            str(finding.path or ""),
            finding.line or 0,
            finding.message,
        )
    )
    package_names = _package_names(root, parsed)
    parsed_sources = tuple(parsed)
    return InspectionResult(
        root=root,
        project_kind=_project_kind(root, package_names, parsed_sources),
        packages=package_names,
        framework_hints=tuple(sorted(frameworks)),
        callables=tuple(candidates),
        configured_target=configured_target,
        recommended_target=recommended,
        recommended_adapter="python_callable",
        findings=tuple(findings),
    )
