"""GAUNTLET command-line application."""

import json
import os
from pathlib import Path
from typing import Annotated, Never

import typer

from gauntlet import __version__
from gauntlet.benchmarks import BenchmarkPackError, load_benchmark_pack
from gauntlet.config.loader import ARTIFACT_ROOT_ENV
from gauntlet.evidence.store import (
    ArtifactCorruptionError,
    InvalidRunIdError,
    RunArtifactStore,
    RunNotFoundError,
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
