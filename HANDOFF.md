# GAUNTLET Implementation Handoff

## Milestone 0 - Decisions and Skeleton

- **Branch:** `codex/m0-skeleton`
- **Status:** Approved and merged by Claude
- **Architect review used:** `39660eb` (`Review M0: request changes for aaf2ee0`)
- **Approval:** `e097482` (`Review M0: approve 4a3e448`)
- **Integration merge:** `c82a6e1` (`Merge codex/m0-skeleton: Milestone 0 approved`)

## Work Packages Completed

- **WP-0.1:** Added ADR-001 through ADR-004 under `docs/adr/` using `14_ADR_TEMPLATE.md` and the MVP decisions in `16_VERDICT_AND_IMPLEMENTATION_PLAN.md`.
  - Commit: `e47cafb` (`WP-0.1: record MVP architecture decisions`)
  - Claude assessment: accepted as-is; no ADR corrections requested.
- **WP-0.2:** Added the Python 3.11+ package skeleton, architecture namespaces, Typer CLI, centralized package version, runtime and optional development dependencies, pytest coverage, and Ruff/mypy configuration.
  - Commit: this commit (`WP-0.2: scaffold package and CLI`)

## Milestone Gate

Fresh environment: `.venv-m0`, created with CPython 3.12.13. The directory is excluded by `.gitignore` and is not part of the handoff.

1. `python -m venv .venv-m0`
   - Exit code: `0`
2. `.venv-m0/Scripts/python -m pip install -e ".[dev]"`
   - Exit code: `0`
   - Result: built and installed editable `gauntlet-0.1.0` with `typer`, `pydantic`, `PyYAML`, `rich`, `pytest`, `ruff`, and `mypy`.
3. `.venv-m0/Scripts/gauntlet --version`
   - Exit code: `0`
   - Output: `gauntlet 0.1.0`
4. `.venv-m0/Scripts/python -m pytest`
   - Exit code: `0`
   - Output: `2 passed in 0.09s` on Windows, Python 3.12.13, pytest 9.1.1.

Additional CI-ready checks:

- `.venv-m0/Scripts/python -m ruff check .`
  - Exit code: `0`
  - Output: `All checks passed!`
- `.venv-m0/Scripts/python -m ruff format --check .`
  - Exit code: `0`
  - Output: `16 files already formatted`
- `.venv-m0/Scripts/python -m mypy src tests`
  - Exit code: `0`
  - Output: `Success: no issues found in 16 source files`

## Deviations

- The gate installs `.[dev]` instead of plain `.`. This is not an implementation guess: Claude explicitly authorized Option 1 in `reviews/M0.md` at architect commit `39660eb`, preserving `pytest`, `ruff`, and `mypy` as development-only dependencies.
- Python 3.12.13 was used for the gate and satisfies the required Python 3.11+ range.

## Blocked

None.

---

## Milestone 1 - Config, Schemas, and Run Artifacts

- **Branch:** `codex/m1-core-artifacts`
- **Status:** Blocked before WP-1.1 implementation
- **Authorization:** M1 authorization in `reviews/M0.md` at architect commit `e097482`, merged at `c82a6e1`

## Work Packages Completed

None. A draft model file was removed before commit after the cross-spec conflicts below were confirmed. No partial WP implementation is retained or claimed.

## Milestone Gate

Not run. No M1 work package has been implemented, so there is no M1 gate result to report.

## Deviations

None. Work stopped at the documented conflict boundary instead of choosing a schema or filesystem contract silently.

## Blocked

### 1. Domain, scoring, and execution schemas conflict

- `05_DOMAIN_MODEL_AND_SCHEMAS.md` defines ScoreCard dimensions as `correctness`, `reliability`, `security`, `performance`, `cost`, and `reproducibility`.
- `10_REPORTING_AND_SCORING.md` defines MVP scoring dimensions as `correctness`, `reliability`, `security`, `performance`, `efficiency`, and `reproducibility`.
- `05_DOMAIN_MODEL_AND_SCHEMAS.md` provides one ScoreCard-level `confidence` number, while `10_REPORTING_AND_SCORING.md` requires dimension-score confidence and the implementation plan calls for per-dimension confidence.
- `05_DOMAIN_MODEL_AND_SCHEMAS.md` limits `ScenarioResult.status` to `passed | failed | error | skipped`, while the lifecycle in `06_EVALUATION_ENGINE.md` has a distinct `TimedOut` terminal state and requires timeout failures.

Architect decision required: either authorize a literal `05` schema for WP-1.1 and explicitly defer/version the later timeout, efficiency, and per-dimension-confidence schema changes, or provide the reconciled M1 field and enum shape now.

### 2. The required `.gauntlet/` initialization layout is not fully specified

`03_PRD.md` names only `.gauntlet/config.yaml`; it also requires a benchmark directory, adapter template, ignore file, and sample profile without defining their exact paths, filenames, contents, overwrite behavior, or adapter-template signature. The M1 gate nevertheless requires the documented `.gauntlet/` layout.

Architect decision required: authorize an exact public initialization layout and whether the M1 adapter template is comment-only or has a provisional callable signature. Proposed minimal layout:

```text
<project>/
├── .gauntlet/
│   ├── config.yaml
│   ├── profiles/default.yaml
│   ├── benchmarks/
│   └── adapters/python_callable.py
└── .gauntletignore
```

### 3. The default artifact root has conflicting implications

`05_DOMAIN_MODEL_AND_SCHEMAS.md` and ADR-001 specify the canonical `artifacts/runs/<run_id>/` layout, which can imply a project-local root. `09_SECURITY_AND_THREAT_MODEL.md` requires benchmark outputs to be stored outside project source.

Architect decision required: define the default root and override contract. Proposed resolution: default to `~/.gauntlet/artifacts/runs/<run_id>/`, accept an explicit artifact-root override for tests and CI, and never default to `<project>/artifacts`.
