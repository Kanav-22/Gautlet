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
- **Status:** Ready for Claude review
- **Authorization:** M1 authorization in `reviews/M0.md` at architect commit `e097482`, merged at `c82a6e1`

## Work Packages Completed

- **WP-1.1:** Added strict Pydantic domain/config models, the reconciled dimension and timeout vocabulary, deterministic JSON Schema export, and schema round-trip/boundary tests.
  - Commit: 63b3031 (WP-1.1: add validated domain schemas)
- **WP-1.2:** Added safe YAML/mapping loading, deep five-level precedence resolution, typed environment overrides including GAUNTLET_ARTIFACT_ROOT, atomic config.resolved.yaml persistence, and comprehensive precedence tests.
  - Commit: 42cec75 (WP-1.2: add layered configuration resolution)
- **WP-1.3:** Added the files-only run artifact store, stable UTC run IDs, typed/atomic artifact writers, safe scan/show behavior, exact idempotent init scaffold, runs list/show CLI commands, ADR-005, ADR-006, and focused tests.
  - Commit: this commit (WP-1.3: add run artifacts and project CLI)

## Milestone Gate

Fresh environment: .venv-m1, created with CPython 3.12.13. The directory is excluded by .gitignore and is not part of the handoff.

1. python -m venv .venv-m1
   - Exit code: 0
2. .venv-m1/Scripts/python -m pip install -e ".[dev]"
   - Exit code: 0
   - Result: installed editable gauntlet-0.1.0 with runtime dependencies plus pytest, ruff, mypy, and types-PyYAML.
3. .venv-m1/Scripts/gauntlet --version
   - Exit code: 0
   - Output: gauntlet 0.1.0
4. .venv-m1/Scripts/python -m pytest -p no:cacheprovider --basetemp C:\tmp\gauntlet-m1-fresh-pytest-0900
   - Exit code: 0
   - Output: 61 passed in 0.65s
5. .venv-m1/Scripts/python -m ruff check .
   - Exit code: 0
   - Output: All checks passed!
6. .venv-m1/Scripts/python -m ruff format --check .
   - Exit code: 0
   - Output: 25 files already formatted
7. .venv-m1/Scripts/python -m mypy src tests
   - Exit code: 0
   - Output: Success: no issues found in 25 source files
8. gauntlet init C:\tmp\gauntlet-m1-init-gate-20260713-0905
   - Exit code: 0
   - Output: created=5, overwritten=0, skipped=0
   - Exact files: .gauntlet/config.yaml, .gauntlet/profiles/default.yaml, .gauntlet/benchmarks/README.md, .gauntlet/adapters/python_callable.py, .gauntletignore
   - Exact directories: .gauntlet, .gauntlet/profiles, .gauntlet/benchmarks, .gauntlet/adapters

## Deviations

- No implementation scope deviations: no spec or plan files changed and no deferred item was implemented.
- Added types-PyYAML to the development extra because strict mypy requires installed PyYAML stubs once WP-1.2 imports yaml.
- The Windows gate routes pytest temporary files to C:\tmp and disables its cache because the Codex sandbox left deny ACLs on the default pytest temp/cache paths; the complete 61-test suite itself passed.

## Resolved Architect Decisions

The original three blockers below were resolved by Claude in reviews/M1.md at architect commit e59adf0. Their evidence is retained here; implementation follows those decisions.

### 1. Domain, scoring, and execution schemas conflict

- `05_DOMAIN_MODEL_AND_SCHEMAS.md` defines ScoreCard dimensions as `correctness`, `reliability`, `security`, `performance`, `cost`, and `reproducibility`.
- `10_REPORTING_AND_SCORING.md` defines MVP scoring dimensions as `correctness`, `reliability`, `security`, `performance`, `efficiency`, and `reproducibility`.
- `05_DOMAIN_MODEL_AND_SCHEMAS.md` provides one ScoreCard-level `confidence` number, while `10_REPORTING_AND_SCORING.md` requires dimension-score confidence and the implementation plan calls for per-dimension confidence.
- `05_DOMAIN_MODEL_AND_SCHEMAS.md` limits `ScenarioResult.status` to `passed | failed | error | skipped`, while the lifecycle in `06_EVALUATION_ENGINE.md` has a distinct `TimedOut` terminal state and requires timeout failures.

Resolution: Claude authorized the reconciled superset, per-dimension score/confidence mapping, overall confidence, and timed_out status. Implemented in WP-1.1 and recorded in ADR-006.

### 2. The required `.gauntlet/` initialization layout is not fully specified

`03_PRD.md` names only `.gauntlet/config.yaml`; it also requires a benchmark directory, adapter template, ignore file, and sample profile without defining their exact paths, filenames, contents, overwrite behavior, or adapter-template signature. The M1 gate nevertheless requires the documented `.gauntlet/` layout.

Resolution: Claude authorized the exact five-file layout, non-destructive idempotence, --force overwrite behavior, and the provisional run(payload: dict) -> dict adapter stub. Implemented in WP-1.3.

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

Resolution: Claude authorized ~/.gauntlet/artifacts as the default with CLI, environment, project, profile, and package precedence. Implemented in WP-1.2/WP-1.3 and recorded in ADR-005.


The approximate blocker-era proposal below is retained for decision history; the exact generated file list in the gate output above is authoritative.
## Blocked

None.

---

## Milestone 2 - Benchmark and Adapter Contracts

- **Branch:** `codex/m2-benchmark-adapters`
- **Status:** Ready for Claude review
- **Authorization:** M2 authorization in `reviews/M1.md` at architect commit `56180d3`, merged into the integration base at `2f19035`

## Work Packages Completed

- **WP-2.1:** Added strict benchmark-pack discovery and loading, manifest/scenario schema validation, version identity retention, contained path resolution, capability negotiation, minimal valid/invalid fixtures, and `gauntlet benchmark validate PATH` with required exit codes.
  - Commit: `a6ab4bc` (`WP-2.1: add benchmark pack validation`)
- **WP-2.2:** Added the versioned UTF-8 JSONL `reset`/`invoke`/`trace`/`usage` protocol, persistent Python-callable subprocess adapter, restricted constructed environment, parent deadlines and process reaping, structured errors, deterministic injected `tool_sequence` registry, stable traces, honest usage counters, and finalized injected-tool scaffold.
  - Commit: `81ce18a` (`WP-2.2: add subprocess callable adapter`)
- **WP-2.3:** Added `examples/sample_agent/`, the correct canonical target, all six deterministic golden variants (correct, inefficient, hallucinating, loop-prone, injection-vulnerable, recovery-capable), and full behavioral/integration coverage.
  - Commit: this commit (`WP-2.3: add sample agent variants`)

## Milestone Gate

Fresh environment: `.tmp/gauntlet-m2-final-019f599c`, created from CPython 3.12.10 outside the repository. Pytest temporary data used the workspace-owned `.tmp/gauntlet-m2-final-pytest-019f599c` path. Neither directory is part of the handoff commit.

1. `python -m venv .tmp/gauntlet-m2-final-019f599c`
   - Exit code: `0`
   - Output: `Python 3.12.10`
2. `.tmp/gauntlet-m2-final-019f599c/Scripts/python -m pip install -e ".[dev]"`
   - Exit code: `0`
   - Result: built editable `gauntlet-0.1.0` and successfully installed all runtime and development dependencies.
3. `.tmp/gauntlet-m2-final-019f599c/Scripts/gauntlet --version`
   - Exit code: `0`
   - Output: `gauntlet 0.1.0`
4. `.tmp/gauntlet-m2-final-019f599c/Scripts/python -m pytest -p no:cacheprovider --basetemp <workspace>/.tmp/gauntlet-m2-final-pytest-019f599c`
   - Exit code: `0`
   - Output: `107 passed in 7.37s` on Windows, Python 3.12.10, pytest 9.1.1.
5. `.tmp/gauntlet-m2-final-019f599c/Scripts/python -m ruff check .`
   - Exit code: `0`
   - Output: `All checks passed!`
6. `.tmp/gauntlet-m2-final-019f599c/Scripts/python -m ruff format --check .`
   - Exit code: `0`
   - Output: `47 files already formatted`
7. `.tmp/gauntlet-m2-final-019f599c/Scripts/python -m mypy src tests`
   - Exit code: `0`
   - Output: `Success: no issues found in 36 source files`
8. Named adapter integration: `pytest tests/test_sample_agent.py::test_sample_agent_real_subprocess_captures_full_dependent_trace`
   - Exit code: `0`
   - Output: `1 passed in 0.47s`
   - Result: invoked `sample_agent.app:run` through the real subprocess adapter and asserted the complete dependent `lookup` -> `save` trace and usage.
9. Benchmark validation CLI:
   - Valid fixture: exit `0`; output `Valid benchmark gauntlet.test.minimal version 0.1.0 (schema 1, 1 scenarios)`.
   - Invalid fixture: exit `2`; actionable output identified the missing manifest `title` field, with no traceback.

## Deviations

- No implementation scope deviation: specs `00`-`16` and `reviews/` were not modified, and no deferred plugin discovery, Docker isolation, LLM judge, LangGraph integration, or HTML reporting was implemented.
- The recorded Windows gate routes venv and pytest temporary data to the writable workspace `.tmp` root because sandbox-managed default/C:\tmp paths had ACL failures. This changes only gate paths, not implementation behavior.
- An earlier candidate gate venv was discarded before validation after terminal yielding caused overlapping pip retries. Every result above comes only from the separately named final venv and its single tracked install process.
- The adapter implements the accepted subprocess-isolation boundary and restricted environment allowlist; it does not claim that subprocess isolation disables network access or forms a hardened security sandbox, consistent with ADR-002.

## Blocked

None.
