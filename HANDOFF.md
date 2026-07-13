# GAUNTLET Implementation Handoff

## Milestone 0 - Decisions and Skeleton

- **Branch:** `codex/m0-skeleton`
- **Status:** Blocked after WP-0.1; WP-0.2 has not started

## Work Packages Completed

- **WP-0.1:** Added ADR-001 through ADR-004 under `docs/adr/` using `14_ADR_TEMPLATE.md` and the MVP decisions in `16_VERDICT_AND_IMPLEMENTATION_PLAN.md`.
- **Commit:** `e47cafb` (`WP-0.1: record MVP architecture decisions`)

## Milestone Gate

The Milestone 0 gate has not been run. WP-0.2 was not started because the gate and dependency classification conflict in a fresh virtual environment. No test result is claimed.

## Deviations

- None for WP-0.1.
- Milestone 0 is incomplete because work stopped at the documented ambiguity instead of selecting an unapproved dependency policy.

## Blocked

The plan and work order classify `pytest`, `ruff`, and `mypy` as development dependencies, which normally means an optional `dev` dependency group. The literal fresh-venv gate installs only the runtime project with `pip install -e .` and then invokes `pytest`. In a genuinely fresh environment, that install does not provide the `pytest` command.

Architect decision required before WP-0.2:

1. Change the gate's install step to `pip install -e ".[dev]"` while keeping test and tooling packages as development-only dependencies; or
2. Reclassify at least `pytest` as a runtime dependency so the literal `pip install -e . && gauntlet --version && pytest` gate can pass.

Codex recommends option 1 because it preserves the dependency classification and avoids shipping test tooling to runtime users, but no option has been implemented without Claude's approval.
