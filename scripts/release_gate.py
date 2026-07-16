"""Release gate: prove the built wheel is a complete, working distribution.

This script is the executable answer to "would a `pip install gauntlet`
actually work for a user?" It never touches the source tree at runtime:

1. Build both the wheel and the source distribution with `python -m build`.
2. Verify the wheel contains the runtime assets users depend on: the
   `gauntlet` console entry point, the packaged `agent_mvp_default` scoring
   policy, and all 15 flagship benchmark scenarios.
3. Create a brand-new virtual environment and install ONLY the built wheel.
4. From that environment — under a hermetic runtime environment that strips
   inherited `PYTHONPATH`/`PYTHONHOME`, sets `PYTHONNOUSERSITE=1`, and runs
   from a neutral working directory so the source checkout can never satisfy
   an import or asset lookup — run:
   - an installed-origin probe proving `gauntlet.__file__` and
     `builtin_agent_mvp_path()` both resolve inside the install environment
   - `gauntlet --version`
   - `gauntlet doctor` (offline environment checks, packaged assets)
   - `gauntlet benchmark validate` on the packaged flagship pack
   - one bounded offline sample evaluation of a generated throwaway project

Exit code 0 means every step passed; any failure exits non-zero with the
failing step's real output. No step may be skipped or its failure hidden.
The gate must pass even when the parent process carries a hostile
`PYTHONPATH` pointing at the repository `src/` tree; CI runs that
contamination regression explicitly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FLAGSHIP_SCENARIO_COUNT = 15

SMOKE_AGENT = '''"""Minimal release-gate agent: answers directly without tools."""


def run(payload, *, tools):
    return {"answer": "42", "completed": True}
'''

SMOKE_MANIFEST = """id: gauntlet.release.smoke
version: 0.1.0
title: Release Smoke Pack
description: One-scenario offline smoke evaluation for the wheel release gate.
schema_version: 1
required_capabilities:
  - invoke
  - trace_tool_calls
dimensions:
  - correctness
scenarios:
  - scenarios/smoke.yaml
scoring_policy: scoring.yaml
"""

SMOKE_SCENARIO = """id: release.smoke
title: Direct answer smoke scenario
description: The agent must answer directly without any tool call.
category: correctness
difficulty: 1
tags:
  - smoke
required_capabilities:
  - invoke
  - trace_tool_calls
input:
  user: Return the configured answer.
fixtures:
  tool_sequence: []
execution_policy:
  timeout_seconds: 30
assertions:
  - type: output_field_equals
    field: answer
    value: "42"
  - type: max_tool_calls
    value: 0
metrics:
  - task_success
"""

SMOKE_POLICY = """id: release-smoke-policy
weights:
  correctness: 1.0
caps:
  critical_security_finding: 49
  task_success_below_50_percent: 59
minimums:
  scenarios_completed: 1
"""

SMOKE_CONFIG = """project:
  name: release-smoke
adapter:
  type: python_callable
  target: smoke_agent:run
evaluation:
  benchmark_packs:
    - gauntlet.release.smoke
  seed: 7
  repeat: 1
  timeout_seconds: 60
execution:
  network: disabled
  isolation: subprocess
reporting:
  formats: [json, markdown]
scoring:
  policy: release-smoke-policy
"""


def hermetic_runtime_env() -> dict[str, str]:
    """Return the parent environment stripped of Python import-path leakage.

    Inherited ``PYTHONPATH``/``PYTHONHOME`` could let the source checkout (or
    anything else on the host) satisfy imports that must come from the
    installed wheel, and user site-packages could shadow wheel contents.
    """

    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def run_step(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> str:
    """Run one gate step, echo its outcome, and fail loudly on error."""

    print(f"--- {name} ---", flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    if output:
        print(output, flush=True)
    if completed.returncode != 0:
        print(f"RELEASE GATE FAILED at step: {name} (exit {completed.returncode})", flush=True)
        raise SystemExit(1)
    return output


def verify_wheel_contents(wheel: Path) -> None:
    """Fail unless the wheel carries the runtime assets users need."""

    print("--- verify wheel runtime assets ---", flush=True)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    problems: list[str] = []
    if "gauntlet/scoring/policies/agent_mvp_default.yaml" not in names:
        problems.append("packaged scoring policy agent_mvp_default.yaml is missing")
    if "gauntlet/benchmarks/agent_mvp/manifest.yaml" not in names:
        problems.append("packaged flagship benchmark manifest is missing")
    scenario_files = [
        name
        for name in names
        if name.startswith("gauntlet/benchmarks/agent_mvp/scenarios/") and name.endswith(".yaml")
    ]
    if len(scenario_files) != FLAGSHIP_SCENARIO_COUNT:
        problems.append(
            f"expected {FLAGSHIP_SCENARIO_COUNT} packaged flagship scenarios, "
            f"found {len(scenario_files)}"
        )
    entry_points = next((name for name in names if name.endswith("entry_points.txt")), None)
    if entry_points is None:
        problems.append("wheel has no entry_points.txt")
    else:
        with zipfile.ZipFile(wheel) as archive:
            declared = archive.read(entry_points).decode("utf-8")
        if "gauntlet = gauntlet.cli:app" not in declared:
            problems.append("gauntlet console entry point is not declared")
    if problems:
        for problem in problems:
            print(f"MISSING: {problem}", flush=True)
        print("RELEASE GATE FAILED at step: verify wheel runtime assets", flush=True)
        raise SystemExit(1)
    print(
        f"wheel contains the entry point, the packaged policy, and "
        f"{FLAGSHIP_SCENARIO_COUNT} flagship scenarios",
        flush=True,
    )


def write_smoke_workspace(workspace: Path) -> tuple[Path, Path, Path]:
    """Create the throwaway project, pack, and artifact root for the sample run."""

    project = workspace / "project"
    (project / ".gauntlet").mkdir(parents=True)
    (project / "smoke_agent.py").write_text(SMOKE_AGENT, encoding="utf-8")
    (project / ".gauntlet" / "config.yaml").write_text(SMOKE_CONFIG, encoding="utf-8")

    pack = workspace / "pack"
    (pack / "scenarios").mkdir(parents=True)
    (pack / "manifest.yaml").write_text(SMOKE_MANIFEST, encoding="utf-8")
    (pack / "scenarios" / "smoke.yaml").write_text(SMOKE_SCENARIO, encoding="utf-8")
    (pack / "scoring.yaml").write_text(SMOKE_POLICY, encoding="utf-8")

    artifacts = workspace / "artifacts"
    return project, pack, artifacts


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gauntlet-release-gate-") as raw_workspace:
        workspace = Path(raw_workspace)

        build_env_dir = workspace / "build-env"
        venv.create(build_env_dir, with_pip=True)
        build_python = _venv_python(build_env_dir)
        run_step(
            "install build frontend",
            [str(build_python), "-m", "pip", "install", "--quiet", "build"],
            cwd=workspace,
        )

        dist = workspace / "dist"
        run_step(
            "build wheel and sdist",
            [str(build_python), "-m", "build", "--outdir", str(dist), str(REPO_ROOT)],
            cwd=workspace,
        )
        wheels = sorted(dist.glob("*.whl"))
        sdists = sorted(dist.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            print(f"expected exactly one wheel and one sdist, found {wheels} / {sdists}")
            raise SystemExit(1)
        verify_wheel_contents(wheels[0])

        install_env_dir = workspace / "install-env"
        venv.create(install_env_dir, with_pip=True)
        install_python = _venv_python(install_env_dir)
        gauntlet_cli = _venv_executable(install_env_dir, "gauntlet")
        runtime_env = hermetic_runtime_env()
        run_step(
            "install built wheel into a clean environment",
            [str(install_python), "-m", "pip", "install", "--quiet", str(wheels[0])],
            cwd=workspace,
            env=runtime_env,
        )

        # Every remaining step runs from the temporary workspace under the
        # hermetic environment so the source checkout can never satisfy an
        # import or asset lookup, deliberately or by accident.
        install_root = install_env_dir.resolve()
        origin_probe = subprocess.run(
            [
                str(install_python),
                "-c",
                "import gauntlet;"
                "from gauntlet.benchmarks.builtin import builtin_agent_mvp_path;"
                "print(gauntlet.__file__);"
                "print(builtin_agent_mvp_path())",
            ],
            cwd=workspace,
            env=runtime_env,
            capture_output=True,
            text=True,
            check=False,
        )
        print("--- installed-origin probe ---", flush=True)
        if origin_probe.returncode != 0:
            print(origin_probe.stdout + origin_probe.stderr)
            print("RELEASE GATE FAILED at step: installed-origin probe")
            raise SystemExit(1)
        probe_lines = origin_probe.stdout.strip().splitlines()
        if len(probe_lines) != 2:
            print(origin_probe.stdout)
            print("RELEASE GATE FAILED at step: installed-origin probe")
            raise SystemExit(1)
        module_origin = Path(probe_lines[0]).resolve()
        # Resolve both sides: Windows runners mix 8.3 short paths (RUNNER~1)
        # with long paths (runneradmin) for the same temp directory.
        packaged_path = Path(probe_lines[1]).resolve()
        for label, resolved in (
            ("gauntlet.__file__", module_origin),
            ("builtin_agent_mvp_path()", packaged_path),
        ):
            if not resolved.is_relative_to(install_root):
                print(f"{label} resolved outside the wheel install: {resolved}")
                print("RELEASE GATE FAILED at step: installed-origin probe")
                raise SystemExit(1)
        print(f"gauntlet imports from {module_origin}", flush=True)
        print(f"flagship pack resolves to {packaged_path}", flush=True)

        run_step(
            "gauntlet --version",
            [str(gauntlet_cli), "--version"],
            cwd=workspace,
            env=runtime_env,
        )
        run_step(
            "gauntlet doctor",
            [
                str(gauntlet_cli),
                "doctor",
                "--artifact-root",
                str(workspace / "doctor-root"),
            ],
            cwd=workspace,
            env=runtime_env,
        )
        run_step(
            "validate packaged flagship benchmark",
            [str(gauntlet_cli), "benchmark", "validate", str(packaged_path)],
            cwd=workspace,
            env=runtime_env,
        )

        project, pack, artifacts = write_smoke_workspace(workspace)
        output = run_step(
            "bounded offline sample evaluation from the installed wheel",
            [
                str(gauntlet_cli),
                "evaluate",
                str(project),
                "--benchmark",
                str(pack),
                "--seed",
                "7",
                "--offline",
                "--artifact-root",
                str(artifacts),
            ],
            cwd=workspace,
            env=runtime_env,
        )
        if "ready" not in output:
            print("sample evaluation did not reach a ready recommendation")
            print("RELEASE GATE FAILED at step: bounded offline sample evaluation")
            raise SystemExit(1)

    print("RELEASE GATE PASSED", flush=True)


def _venv_python(env_dir: Path) -> Path:
    return _venv_executable(env_dir, "python")


def _venv_executable(env_dir: Path, name: str) -> Path:
    if sys.platform == "win32":
        return env_dir / "Scripts" / f"{name}.exe"
    return env_dir / "bin" / name


if __name__ == "__main__":
    main()
