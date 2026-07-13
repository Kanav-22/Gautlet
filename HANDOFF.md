# GAUNTLET Implementation Handoff

## Milestone 0 - Decisions and Skeleton

- **Branch:** `codex/m0-skeleton`
- **Status:** Ready for Claude re-review
- **Architect review used:** `39660eb` (`Review M0: request changes for aaf2ee0`)

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
