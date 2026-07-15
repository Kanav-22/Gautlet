"""GAUNTLET command-line application."""

import json
import os
from pathlib import Path
from typing import Annotated, Never

import typer

from gauntlet import __version__
from gauntlet.benchmarks import BenchmarkPackError, load_benchmark_pack
from gauntlet.config.loader import ARTIFACT_ROOT_ENV, ConfigLoadError
from gauntlet.discovery import (
    DoctorStatus,
    InspectionInputError,
    InspectionLevel,
    inspect_project,
    run_doctor,
)
from gauntlet.evidence.store import (
    ArtifactCorruptionError,
    InvalidRunIdError,
    RunArtifactStore,
    RunNotFoundError,
)
from gauntlet.orchestration import ProjectEvaluationError, evaluate_project
from gauntlet.reporting import (
    ComparisonArtifactError,
    ComparisonInputError,
    EvaluationPipelineError,
    RegressionAssessment,
    RunComparisonService,
    format_run_comparison,
)

app = typer.Typer(
    help="Evaluate agentic AI systems with reproducible evidence.",
    no_args_is_help=True,
)

runs_app = typer.Typer(help="Inspect locally stored evaluation runs.", no_args_is_help=True)
app.add_typer(runs_app, name="runs")
benchmark_app = typer.Typer(help="Validate and inspect benchmark packs.", no_args_is_help=True)
app.add_typer(benchmark_app, name="benchmark")


def _version_callback(value: bool) -> None:
    """Print the installed version for the eager root option."""
    if value:
        typer.echo(f"gauntlet {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed GAUNTLET version and exit.",
        ),
    ] = None,
) -> None:
    """Evaluate agentic AI systems."""


def _configuration_error(message: str) -> Never:
    """Report an actionable configuration error and exit consistently."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=2)


def _incomplete_error(message: str) -> Never:
    """Report corrupt or incomplete run data using the documented exit code."""
    typer.echo(f"Incomplete: {message}", err=True)
    raise typer.Exit(code=5)


def _artifact_store(artifact_root: Path | None) -> RunArtifactStore:
    """Resolve the CLI and environment artifact-root overrides."""
    if artifact_root is not None:
        return RunArtifactStore(artifact_root)
    environment_root = os.environ.get(ARTIFACT_ROOT_ENV)
    if environment_root is not None:
        return RunArtifactStore(Path(environment_root))
    return RunArtifactStore()


def _project_templates(project_name: str) -> dict[Path, str]:
    """Return the complete user-owned scaffold created by gauntlet init."""
    config = f"""project:
  name: {json.dumps(project_name)}
adapter:
  type: python_callable
  target: sample_agent.app:run
evaluation:
  benchmark_packs:
    - gauntlet.agent.mvp
  seed: 42
  repeat: 1
  timeout_seconds: 60
execution:
  network: disabled
  isolation: subprocess
reporting:
  formats: [json, markdown]
scoring:
  policy: agent_mvp_default
"""
    profile = """# Project-local defaults for the default profile.
# Add only values that should override the package defaults.
{}
"""
    benchmark_readme = """# Project benchmark packs

Place project-owned benchmark pack directories in this folder.
GAUNTLET's benchmark contract is finalized in Milestone 2.
"""
    adapter = '''"""Project-owned Python callable adapter shim.

All evaluated tool calls must use the injected registry. Agents with hard-wired
tools need a thin shim that exposes this boundary.
"""

from typing import Any, Protocol


class ToolRegistry(Protocol):
    """Minimal interface supplied by the GAUNTLET fixture harness."""

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any: ...


def run(
    payload: dict[str, Any],
    *,
    tools: ToolRegistry,
) -> dict[str, Any]:
    """Receive one evaluation payload and use only the injected tools."""
    raise NotImplementedError("Implement the project adapter before evaluation")
'''
    ignore = """# Files excluded from GAUNTLET project discovery and evidence capture.
.env
.env.*
*.key
*.pem
__pycache__/
.venv/
venv/
node_modules/
"""
    return {
        Path(".gauntlet/config.yaml"): config,
        Path(".gauntlet/profiles/default.yaml"): profile,
        Path(".gauntlet/benchmarks/README.md"): benchmark_readme,
        Path(".gauntlet/adapters/python_callable.py"): adapter,
        Path(".gauntletignore"): ignore,
    }


@benchmark_app.command("validate")
def validate_benchmark(
    path: Annotated[
        Path,
        typer.Argument(help="Benchmark directory or manifest YAML file."),
    ],
) -> None:
    """Validate a benchmark manifest, its scenarios, and pack references."""
    try:
        benchmark = load_benchmark_pack(path)
    except BenchmarkPackError as error:
        _configuration_error(str(error))
    typer.echo(
        f"Valid benchmark {benchmark.identity.id} version {benchmark.identity.version} "
        f"(schema {benchmark.identity.schema_version}, {len(benchmark.scenarios)} scenarios)"
    )


@app.command("inspect")
def inspect_path(
    path: Annotated[
        Path,
        typer.Argument(help="Python project, package, module, or source file to inspect."),
    ] = Path("."),
) -> None:
    """Inspect Python source statically without importing or executing user code."""

    try:
        result = inspect_project(path)
    except InspectionInputError as error:
        _configuration_error(str(error))

    typer.echo(f"Project: {result.root}")
    typer.echo(f"Project kind: {result.project_kind.value}")
    typer.echo(f"Supported adapter: {result.recommended_adapter}")
    typer.echo(f"Configured target: {result.configured_target or 'not configured'}")
    typer.echo(f"Recommended target: {result.recommended_target or 'not found'}")
    frameworks = ", ".join(result.framework_hints) or "none detected"
    typer.echo(f"Framework hints: {frameworks}")
    typer.echo("Available plugins: built-in MVP components only")
    typer.echo("Estimated evaluation cost: not reported until the adapter supplies usage")
    if result.callables:
        typer.echo("Callable candidates:")
        for candidate in result.callables:
            compatibility = (
                "compatible"
                if candidate.accepts_payload
                and candidate.accepts_tools
                and not candidate.async_callable
                else "needs a synchronous adapter shim"
            )
            typer.echo(f"  - {candidate.target} ({compatibility})")
    if result.findings:
        typer.echo("Findings:")
        for finding in result.findings:
            location = ""
            if finding.path is not None:
                location = f" [{finding.path}"
                if finding.line is not None:
                    location += f":{finding.line}"
                location += "]"
            typer.echo(
                f"  - {finding.level.value.upper()} {finding.code}: {finding.message}{location}"
            )
            if finding.action is not None:
                typer.echo(f"    Action: {finding.action}")
    if any(finding.level is InspectionLevel.ERROR for finding in result.findings):
        raise typer.Exit(code=2)


@app.command("doctor")
def doctor(
    artifact_root: Annotated[
        Path | None,
        typer.Option(
            "--artifact-root",
            help="Artifact root to verify; defaults to GAUNTLET_ARTIFACT_ROOT or the package default.",
        ),
    ] = None,
) -> None:
    """Run deterministic offline installation and environment checks."""

    selected_root = artifact_root
    if selected_root is None and ARTIFACT_ROOT_ENV in os.environ:
        selected_root = Path(os.environ[ARTIFACT_ROOT_ENV])
    result = run_doctor(artifact_root=selected_root)
    for check in result.checks:
        typer.echo(f"[{check.status.value}] {check.id}: {check.message}")
        if check.action is not None:
            typer.echo(f"  Action: {check.action}")
    if not result.ok:
        failed = sum(check.status is DoctorStatus.FAIL for check in result.checks)
        typer.echo(f"Doctor failed: {failed} required check(s) need attention.", err=True)
        raise typer.Exit(code=2)
    typer.echo("Doctor passed: all required offline checks succeeded.")


@app.command("evaluate")
def evaluate(
    path: Annotated[
        Path,
        typer.Argument(help="Initialized GAUNTLET project directory."),
    ] = Path("."),
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Project-local profile name."),
    ] = None,
    benchmark: Annotated[
        str | None,
        typer.Option("--benchmark", help="Benchmark ID or local pack path."),
    ] = None,
    scenario: Annotated[
        str | None,
        typer.Option("--scenario", help="Run one exact scenario ID."),
    ] = None,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Deterministic fixture seed."),
    ] = None,
    repeat: Annotated[
        int | None,
        typer.Option("--repeat", min=1, help="Number of complete benchmark repeats."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Force network-disabled execution."),
    ] = False,
    artifact_root: Annotated[
        Path | None,
        typer.Option("--artifact-root", help="Override the local run artifact root."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Print only the final report path or an error."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Print run identity and scoring details."),
    ] = False,
) -> None:
    """Evaluate a configured Python callable and publish evidence-backed reports."""

    if quiet and verbose:
        _configuration_error("--quiet and --verbose are mutually exclusive")
    if not quiet:
        typer.echo(f"Evaluating project {path}...")
    try:
        result = evaluate_project(
            path,
            profile=profile,
            benchmark=benchmark,
            scenario=scenario,
            seed=seed,
            repeat=repeat,
            offline=offline,
            artifact_root=artifact_root,
        )
    except (ProjectEvaluationError, ConfigLoadError, BenchmarkPackError) as error:
        _configuration_error(str(error))
    except EvaluationPipelineError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=int(error.exit_code)) from error

    if verbose:
        typer.echo(f"Run: {result.run.id}")
        typer.echo(f"Recommendation: {result.scoring.recommendation.value}")
        typer.echo(f"Overall score: {result.scoring.scorecard.overall:.2f}/100")
        typer.echo(
            "Reproducibility: "
            f"{result.reproducibility.claim.value} "
            f"({result.reproducibility.repeat_count} repeat(s))"
        )
    elif not quiet:
        typer.echo(
            f"Completed {result.run.id}: {result.scoring.recommendation.value}, "
            f"score {result.scoring.scorecard.overall:.2f}/100"
        )
    typer.echo(f"Report: {result.artifacts.markdown}")
    if result.exit_code.value != 0:
        raise typer.Exit(code=int(result.exit_code))


@app.command("init")
def initialize_project(
    path: Annotated[
        Path,
        typer.Argument(help="Project directory to initialize."),
    ] = Path("."),
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing scaffold files."),
    ] = False,
) -> None:
    """Create a minimal, project-local GAUNTLET scaffold."""
    target = path.resolve()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _configuration_error(f"Unable to create project directory {target}: {error}")
    if not target.is_dir():
        _configuration_error(f"Project path is not a directory: {target}")

    created = overwritten = skipped = 0
    for relative_path, content in _project_templates(target.name).items():
        destination = target / relative_path
        if destination.exists() and not force:
            typer.echo(f"skipped {relative_path.as_posix()}")
            skipped += 1
            continue
        existed = destination.exists()
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        except OSError as error:
            _configuration_error(f"Unable to write {destination}: {error}")
        if existed:
            typer.echo(f"overwritten {relative_path.as_posix()}")
            overwritten += 1
        else:
            typer.echo(f"created {relative_path.as_posix()}")
            created += 1

    typer.echo(
        f"Initialized {target} (created={created}, overwritten={overwritten}, skipped={skipped})"
    )


@runs_app.command("list")
def list_runs(
    artifact_root: Annotated[
        Path | None,
        typer.Option(
            "--artifact-root",
            help="Artifact root containing the runs directory.",
        ),
    ] = None,
) -> None:
    """List run manifests from the local artifact store."""
    result = _artifact_store(artifact_root).scan()
    if result.runs:
        typer.echo("RUN ID\tSTATUS\tPROJECT\tSTARTED")
        for run in result.runs:
            typer.echo(
                f"{run.id}\t{run.status.value}\t{run.project_id}\t{run.started_at.isoformat()}"
            )
    else:
        typer.echo("No runs found.")

    if result.problems:
        for problem in result.problems:
            typer.echo(f"Incomplete: {problem}", err=True)
        raise typer.Exit(code=5)


@runs_app.command("show")
def show_run(
    run_id: Annotated[str, typer.Argument(help="Stable GAUNTLET run identifier.")],
    artifact_root: Annotated[
        Path | None,
        typer.Option(
            "--artifact-root",
            help="Artifact root containing the runs directory.",
        ),
    ] = None,
) -> None:
    """Print one run manifest as JSON."""
    try:
        run = _artifact_store(artifact_root).load_manifest(run_id)
    except (InvalidRunIdError, RunNotFoundError) as error:
        _configuration_error(str(error))
    except ArtifactCorruptionError as error:
        _incomplete_error(str(error))
    typer.echo(json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command("compare")
def compare_runs(
    run_a: Annotated[str, typer.Argument(help="Baseline GAUNTLET run ID.")],
    run_b: Annotated[str, typer.Argument(help="Candidate GAUNTLET run ID.")],
    artifact_root: Annotated[
        Path | None,
        typer.Option(
            "--artifact-root",
            help="Artifact root containing both run directories.",
        ),
    ] = None,
) -> None:
    """Compare RUN_B against RUN_A with context-aware regression rules."""

    try:
        comparison = RunComparisonService(_artifact_store(artifact_root)).compare(run_a, run_b)
    except (InvalidRunIdError, RunNotFoundError, ComparisonInputError) as error:
        _configuration_error(str(error))
    except (ArtifactCorruptionError, ComparisonArtifactError) as error:
        _incomplete_error(str(error))
    typer.echo(format_run_comparison(comparison))
    if comparison.assessment is RegressionAssessment.REGRESSION:
        raise typer.Exit(code=1)
    if comparison.assessment is RegressionAssessment.INSUFFICIENT_DATA:
        raise typer.Exit(code=5)
