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

## Milestone 5 Review Fix - Environment Secret Literal Boundary

- **Branch:** `codex/m5-mvp-acceptance`
- **Status:** Ready for Claude re-review
- **Review:** `reviews/M5.md` at Claude commit `6e83d51` requested one change against Codex commit `85408aa`.
- **WP-5.3 fix:** Added the documented `SECRET_LITERAL_MIN_LENGTH = 8` boundary for all environment-derived secret literals, including explicitly named environment values. Values shorter than eight characters are not literal redaction candidates; configured regex patterns remain unchanged. This avoids unsafe collisions with ordinary dates, counters, and ports while retaining fail-closed redaction for credential-length values.
- **Regression coverage:** A full evaluation with `MY_API_KEY=20` now completes with intact persisted timestamps and an unredacted legitimate `20`. The existing whole-run persisted-artifact scan now proves that a secret at the exact eight-character boundary is removed from every artifact. The fail-closed key-collision fixture was lengthened to remain above the new boundary without changing what it tests.
- **Commit:** this commit (`WP-5.3 fix: ignore short environment secret literals`)

## Fresh Windows Gate After the Fix

Environment: `.venv-m5-fix-gate`, created after the review fix from CPython 3.12.10. The full gate inherited the ordinary process environment; no environment variables were scrubbed or replaced. Gate artifacts and pytest temporary directories were placed only in ignored paths and are not part of the commit.

1. `python -m venv .venv-m5-fix-gate`
   - Exit code: `0`
2. `.venv-m5-fix-gate/Scripts/python -m pip install -e ".[dev]"`
   - Exit code: `0`
   - Result: built and installed editable `gauntlet-0.1.0` with all runtime and development dependencies.
3. `.venv-m5-fix-gate/Scripts/gauntlet --version`
   - Exit code: `0`
   - Output: `gauntlet 0.1.0`
4. Full suite: `.venv-m5-fix-gate/Scripts/python -m pytest -p no:cacheprovider --basetemp <ignored-workspace-temp>`
   - Exit code: `0`
   - Output: **`268 passed in 171.20s`** on Windows, Python 3.12.10, pytest 9.1.1.
5. Focused review regressions plus preserved fail-closed collision behavior:
   - Exit code: `0`
   - Output: **`3 passed in 2.15s`**.
6. `.venv-m5-fix-gate/Scripts/python -m ruff check .`
   - Exit code: `0`
   - Output: `All checks passed!`
7. `.venv-m5-fix-gate/Scripts/python -m ruff format --check .`
   - Exit code: `0`
   - Output: `76 files already formatted`
8. `.venv-m5-fix-gate/Scripts/python -m mypy src tests`
   - Exit code: `0`
   - Output: `Success: no issues found in 65 source files`
9. Explicit inherited gate: one adapter integration test, five M3 security tests, and four M4 scoring/comparison tests.
   - Exit code: `0`
   - Output: **`10 passed in 10.61s`**.
10. Benchmark validation CLI:
    - Flagship pack: exit `0`; output `Valid benchmark gauntlet.agent.mvp version 0.1.0 (schema 1, 15 scenarios)`.
    - Invalid fixture: PowerShell `$LASTEXITCODE` was **`2`**; output identified the missing manifest `title` field without a traceback.
11. `gauntlet doctor --artifact-root .tmp/m5-fix-doctor-20260716-a`
    - Exit code: `0`
    - Output: all seven required offline checks passed.
12. `gauntlet inspect examples`
    - Exit code: `0`
    - Output: detected the Python package and configured/recommended `sample_agent.app:run` without importing user code.
13. Ten-point checklist: `.venv-m5-fix-gate/Scripts/python -m pytest -v -p no:cacheprovider --basetemp .tmp/m5-fix-acceptance-20260716-a tests/test_mvp_acceptance.py`
    - Exit code: `0`
    - Output: **`10 passed in 76.47s`**.
14. Real sample evaluation: `gauntlet evaluate examples --benchmark gauntlet.agent.mvp --seed 42 --repeat 3 --offline --artifact-root .tmp/m5-fix-demo-20260716-a --verbose`
    - Exit code: `0`
    - Output: run `run_20260715_223636_38ddfbef`, recommendation `ready`, score `98.78/100`, reproducibility `byte_identical (3 repeats)`, with the Markdown report path printed.

## Review-Fix Deviations

- Specs `00`-`16`, `reviews/`, and all accepted M5 product areas were left unchanged. No deferred feature was added.
- Two focused pytest attempts using `C:\tmp` were discarded before collection because the Windows sandbox denied creation of the requested base-temp directory. The successful focused and full gates used new workspace-owned temp directories.
- The first Ruff invocation was discarded because it ran before the unignored full-suite pytest directory was removed and therefore inspected intentionally invalid Python fixtures generated by tests. After deleting that verified test-only directory, both Ruff checks passed on the clean repository tree.

## Blocked

None.

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

---

## Milestone 2 Review Fix - Preserve the Active Virtualenv Interpreter

- **Branch:** `codex/m2-benchmark-adapters`
- **Status:** Ready for Claude re-review
- **Review:** `reviews/M2.md` at Claude commit `405a698` requested one change against Codex commit `5d1b9f5`.
- **Finding addressed:** `PythonCallableAdapter` now launches the child with `sys.executable` unchanged. It no longer resolves the POSIX virtualenv `bin/python` symlink to a base interpreter that may not contain GAUNTLET.
- **Scope:** Exactly one production line changed, plus this required handoff update. All other M2 implementation remains accepted as-is.
- **Commit:** this commit (`WP-2.2 fix: preserve virtualenv interpreter`)

## Fresh Windows Gate After the Fix

Fresh environment: `.tmp/gauntlet-m2-fix-405a698`, created from CPython 3.12.10 after applying the review fix. Pytest temporary data used the separately named workspace `.tmp/gauntlet-m2-fix-pytest-405a698` path. Neither directory is part of the commit.

1. `python -m venv .tmp/gauntlet-m2-fix-405a698`
   - Exit code: `0`
   - Output: `Python 3.12.10`
2. `.tmp/gauntlet-m2-fix-405a698/Scripts/python -m pip install -e ".[dev]"`
   - Exit code: `0`
   - Result: built editable `gauntlet-0.1.0` and successfully installed all runtime and development dependencies.
3. `.tmp/gauntlet-m2-fix-405a698/Scripts/gauntlet --version`
   - Exit code: `0`
   - Output: `gauntlet 0.1.0`
4. `.tmp/gauntlet-m2-fix-405a698/Scripts/python -m pytest -p no:cacheprovider --basetemp <workspace>/.tmp/gauntlet-m2-fix-pytest-405a698`
   - Exit code: `0`
   - Output: `107 passed in 10.24s` on Windows, Python 3.12.10, pytest 9.1.1.
5. `.tmp/gauntlet-m2-fix-405a698/Scripts/python -m ruff check .`
   - Exit code: `0`
   - Output: `All checks passed!`
6. `.tmp/gauntlet-m2-fix-405a698/Scripts/python -m ruff format --check .`
   - Exit code: `0`
   - Output: `47 files already formatted`
7. `.tmp/gauntlet-m2-fix-405a698/Scripts/python -m mypy src tests`
   - Exit code: `0`
   - Output: `Success: no issues found in 36 source files`
8. Named adapter integration: `pytest tests/test_sample_agent.py::test_sample_agent_real_subprocess_captures_full_dependent_trace`
   - Exit code: `0`
   - Output: `1 passed in 0.49s`
9. Benchmark validation CLI:
   - Valid fixture: exit `0`; output `Valid benchmark gauntlet.test.minimal version 0.1.0 (schema 1, 1 scenarios)`.
   - Invalid fixture: exit `2`; actionable output identified the missing manifest `title` field, with no traceback.

## Deviations

- None. The implementation change is exactly the one-line correction required by `reviews/M2.md`; no unrelated code, test, spec, plan, or review file was changed.
- The POSIX failure cannot be reproduced by the Windows gate because Windows virtualenv interpreters are regular files. Claude's review documents its independent Linux scratch verification; Claude will re-run Linux against this committed Codex head.

## Blocked

None.

---

## Milestone 3 - Execution Engine and Evidence

- **Branch:** `codex/m3-execution-evidence`
- **Status:** Ready for Claude review
- **Authorization:** M3 authorization in `reviews/M2.md`, based on the approved M2 integration commit `ebeabff`

## Work Packages Completed

- **WP-3.1:** Added the scenario lifecycle executor with parent-deadline timeout mapping, deterministic seeded fixture attempts, retryable-child-only retries, unconditional cleanup, normalized `ScenarioResult` output, and input-order-preserving bounded local concurrency.
  - Commit: `3f5b4d1` (`WP-3.1: add bounded scenario executor`)
- **WP-3.2:** Added content-addressed evidence envelopes for outputs, traces, fixtures, lifecycle metrics, tool calls, stderr, and exceptions. Literal and regex secrets are redacted in memory before any write; hashes cover the exact redacted persisted bytes; evidence is tamper-checked and linked back to each result.
  - Commit: `4ad6556` (`WP-3.2: add redacted content-addressed evidence`)
- **WP-3.3:** Added strict definitions and deterministic evaluation for all nine MVP assertions: `tool_called`, `max_tool_calls`, `output_contains`, `output_field_equals`, `schema_valid`, `no_forbidden_calls`, `max_steps`, `no_hallucinated_success`, and `completed_before_timeout`. Every assertion result carries non-empty, verified evidence references.
  - Commit: this commit (`WP-3.3: add evidence-linked assertion engine`)

## Milestone Gate

Fresh environment: `.venv-m3-gate`, created from CPython 3.12.10 after all three WP commits. Pytest temporary data used the workspace-owned `.tmp` directory with cache disabled. Both paths are ignored and are not part of the handoff commit.

1. `python -m venv .venv-m3-gate`
   - Exit code: `0`
   - Output: `Python 3.12.10`
2. `.venv-m3-gate/Scripts/python -m pip install -e ".[dev]"`
   - Exit code: `0`
   - Result: built editable `gauntlet-0.1.0` and successfully installed every runtime and development dependency, including `jsonschema` and `types-jsonschema`.
3. `.venv-m3-gate/Scripts/gauntlet --version`
   - Exit code: `0`
   - Output: `gauntlet 0.1.0`
4. `.venv-m3-gate/Scripts/python -m pytest -p no:cacheprovider --basetemp .tmp/m3-gate-pytest`
   - Exit code: `0`
   - Output: `163 passed in 8.71s` on Windows, Python 3.12.10, pytest 9.1.1.
5. `.venv-m3-gate/Scripts/ruff check .`
   - Exit code: `0`
   - Output: `All checks passed!`
6. `.venv-m3-gate/Scripts/ruff format --check .`
   - Exit code: `0`
   - Output: `55 files already formatted`
7. `.venv-m3-gate/Scripts/mypy src tests`
   - Exit code: `0`
   - Output: `Success: no issues found in 42 source files`
8. Explicit M3 security gate (five named tests: persisted-evidence secret scan, real hanging-child timeout/reap, network/secret environment exclusion, malicious-stdout containment, and evidence refs on all nine assertion results):
   - Exit code: `0`
   - Output: `5 passed in 1.42s`
9. Named adapter integration: `pytest tests/test_sample_agent.py::test_sample_agent_real_subprocess_captures_full_dependent_trace`
   - Exit code: `0`
   - Output: `1 passed in 0.46s`
10. Benchmark validation CLI:
    - Valid fixture: exit `0`; output `Valid benchmark gauntlet.test.minimal version 0.1.0 (schema 1, 1 scenarios)`.
    - Invalid fixture: explicit PowerShell `$LASTEXITCODE` capture returned `2`; actionable output identified the missing manifest `title` field with no traceback.

## Deviations

- No scope deviation: specs `00`-`16`, `reviews/`, `adapters/`, and all deferred features remain untouched.
- Added runtime `jsonschema` and development `types-jsonschema` dependencies so `schema_valid` implements JSON Schema Draft 2020-12 rather than an incomplete custom subset. Non-local `$ref` values are rejected to preserve offline evaluation.
- The specs do not define retry-policy keys. The executor accepts `execution_policy.max_retries` (default `0`) and retries only `AdapterChildError` values already marked `retryable`, always with a fresh child and rewound fixtures.
- The authorized network gate is implemented as network-policy environment isolation: proxy settings, API keys, and secrets are excluded from the child. Consistent with ADR-002, subprocess isolation is not presented as OS-level socket denial.
- `no_hallucinated_success` uses deterministic fixture-consumption proof: an output claiming `completed: true` must be backed by all declared `tool_sequence` fixtures consumed in order. This avoids framework-specific field-name heuristics.
- Content-addressed filenames exposed a Windows legacy path-length boundary in deeply nested pytest paths. Artifact I/O now uses Windows extended-length paths internally while retaining portable relative evidence paths; short retries handle transient Windows file-handle contention.

## Blocked

None.

---

## Milestone 4 - Metrics, Scoring, Reports, and Comparison

- **Branch:** `codex/m4-scoring-reports`
- **Status:** Ready for Claude review
- **Authorization:** M4 authorization in `reviews/M3.md`, based on the approved M3 integration commit `3cde977`

## Work Packages Completed

- **WP-4.1:** Added evidence-linked scenario metric collection for task success, latency, steps, tool calls, retries, recovery steps, exceptions, and exact adapter-reported usage. Missing token or cost counters remain absent rather than being estimated.
  - Commit: `87482b5` (`WP-4.1: add evidence-linked metric collectors`)
- **WP-4.2:** Added the policy-driven scoring engine and `agent_mvp_default` policy with weighted dimensions, confidence, policy caps and minimums, release recommendations, and citations for every applied rule.
  - Commit: `22d8db7` (`WP-4.2: add policy-driven scoring engine`)
- **WP-4.3:** Added the evaluation-to-report pipeline and atomic, redacted publication of `results.json`, `scorecard.json`, `findings.json`, normalized configuration, and `report.md`, with the completed manifest published only after every required artifact succeeds.
  - Commit: `cf24c10` (`WP-4.3: add scored report pipeline`)
- **WP-4.4:** Added strict run comparison plus `gauntlet compare`, with explicit comparability context, score/failure/latency/cost deltas, deterministic regression assessment, and documented exit behavior for regressions, invalid input, incomplete runs, and insufficient live-service evidence.
  - Commit: this commit (`WP-4.4: add context-aware run comparison`)

## Milestone Gate

Fresh environment: `.venv-m4-gate`, created from CPython 3.12.10 after the four work packages were implemented. Successful pytest runs used unique workspace-owned `.tmp` directories with cache disabled. Both paths are ignored and are not part of the handoff commit.

1. `python -m venv .venv-m4-gate`
   - Exit code: `0`
   - Output: `Python 3.12.10`
2. `.venv-m4-gate/Scripts/python -m pip install -e ".[dev]"`
   - Exit code: `0`
   - Result: built editable `gauntlet-0.1.0` and successfully installed all runtime and development dependencies.
3. `.venv-m4-gate/Scripts/gauntlet --version`
   - Exit code: `0`
   - Output: `gauntlet 0.1.0`
4. `.venv-m4-gate/Scripts/python -m pytest -p no:cacheprovider --basetemp .tmp/gauntlet-m4-gate-20260715-2`
   - Exit code: `0`
   - Output: `203 passed in 21.21s` on Windows, Python 3.12.10, pytest 9.1.1.
   - Environment note: the first unchanged attempt with `--basetemp C:\tmp\gauntlet-m4-gate-20260715-1` exited `1` after `137 passed, 66 errors in 10.34s`; every error was a pytest `tmp_path` setup `PermissionError` because the Windows sandbox denied creation of that base directory. Rerouting only pytest temporary data to the ignored workspace path produced the complete green result above.
5. `.venv-m4-gate/Scripts/ruff check .`
   - Exit code: `0`
   - Output: `All checks passed!`
6. `.venv-m4-gate/Scripts/ruff format --check .`
   - Exit code: `0`
   - Output: `64 files already formatted`
7. `.venv-m4-gate/Scripts/mypy src tests`
   - Exit code: `0`
   - Output: `Success: no issues found in 53 source files`
8. Named adapter integration: `pytest tests/test_sample_agent.py::test_sample_agent_real_subprocess_captures_full_dependent_trace`
   - Exit code: `0`
   - Output: `1 passed in 0.63s`
9. Explicit inherited security gate (persisted-evidence secret scan, real hanging-child timeout/reap, network-credential environment exclusion, malicious-stdout containment, and evidence refs on all nine assertion results):
   - Exit code: `0`
   - Output: `5 passed in 1.58s`
10. Explicit M4 acceptance gate (correct golden agent outscores the degraded variant, hand-computed policy score, deterministic regression detection, and configuration-change distinction):
    - Exit code: `0`
    - Output: `4 passed in 9.89s`
11. Focused whole-run redaction and public comparison CLI checks:
    - Exit code: `0`
    - Output: `2 passed in 1.34s`
12. Benchmark validation CLI:
    - Valid fixture: exit `0`; output `Valid benchmark gauntlet.test.minimal version 0.1.0 (schema 1, 1 scenarios)`.
    - Invalid fixture: explicit PowerShell `$LASTEXITCODE` capture returned `2`; actionable output identified the missing manifest `title` field with no traceback.

## Deviations

- No unauthorized scope change: specs `00`-`16`, `reviews/`, `adapters/`, `execution/`, and all deferred features remain untouched.
- The specification supplies policy weights, caps, and minimums but not metric normalization curves or recommendation score bands. The policy therefore records deterministic conservative formulas plus `ready_score: 80` and `passing_score: 60` instead of hiding those choices in code.
- Reproducibility is never inferred from one successful run. It requires explicit repeat-comparison evidence; missing repeat evidence lowers confidence and can make the evaluation inconclusive, preserving ADR-004.
- Regression significance thresholds are unspecified. Deterministic fixture comparisons use exact new-failure or score-decrease evidence; live-service runs return insufficient data, while latency and cost remain visible raw deltas and do not trigger invented regressions.
- Compare-specific exit codes were not specified. `gauntlet compare` uses `1` for a proven regression, `0` for no regression or a documented context change, `2` for invalid input, and `5` for corrupt/incomplete artifacts or insufficient live-service evidence.
- Benchmark provenance and the configuration fingerprint are persisted in the report summary because the existing `EvaluationRun` model has no comparison-critical version fields.
- Token and cost metrics are published only when every contributing adapter attempt supplies valid canonical counters. Missing usage remains absent, never zero-filled or estimated.
- WP-4.3 extends the evidence store with exact usage evidence and atomic Markdown persistence so metrics stay traceable and `report.md` follows the same publish-safely discipline as JSON/YAML artifacts.
- M4 provides the authorized library-level evaluation pipeline. The complete `gauntlet evaluate` CLI workflow remains in M5.
- `.tmp/` is ignored solely to provide Windows-sandbox-safe pytest paths; it does not change runtime artifact placement.

## Blocked

None.

---

## Milestone 5 - Agent MVP Pack and Acceptance

- **Branch:** `codex/m5-mvp-acceptance`
- **Status:** Ready for Claude review
- **Authorization:** M5 authorization in `reviews/M4.md`, based on the approved M4 integration commit `f1e2643`

## Work Packages Completed

- **WP-5.1:** Added the packaged `gauntlet.agent.mvp` benchmark with all 15 authorized scenarios, the `agent_mvp_default` scoring policy, and meaningful pass/fail discrimination across all six golden agent variants.
  - Commit: `ae20b71` (`WP-5.1: add flagship agent MVP benchmark`)
- **WP-5.2:** Completed `gauntlet evaluate`, static `inspect`, and offline `doctor`; added safe built-in benchmark resolution, project-local root and `src/` callable handling, actual repeat execution, canonical comparison artifacts, evidence-backed finding generation, live-repeat reporting, and documented exit codes.
  - Commit: `2a454d2` (`WP-5.2: add evaluation CLI and diagnostics`)
- **WP-5.3:** Added the exact ten-point MVP acceptance suite, committed sample configuration and a real generated redacted report, and documented the tested quickstart and isolation boundary.
  - Commit: this commit (`WP-5.3: prove MVP acceptance and quickstart`)

## Milestone Gate

Fresh environment: `.venv-m5-gate`, created from CPython 3.12.10 after the complete M5 implementation. Pytest temporary data used unique ignored workspace-owned `.tmp` directories with cache disabled. Neither location is part of this commit.

1. `python -m venv .venv-m5-gate`
   - Exit code: `0`
2. `.venv-m5-gate/Scripts/python -m pip install -e ".[dev]"`
   - Exit code: `0`
   - Result: built editable `gauntlet-0.1.0` and installed all runtime and development dependencies in the new environment.
3. `.venv-m5-gate/Scripts/gauntlet --version`
   - Exit code: `0`
   - Output: `gauntlet 0.1.0`
4. `.venv-m5-gate/Scripts/python -m pytest -p no:cacheprovider --basetemp .tmp/m5-fresh-full`
   - Exit code: `0`
   - Output: `267 passed in 153.39s` on Windows, Python 3.12.10, pytest 9.1.1.
5. `.venv-m5-gate/Scripts/python -m ruff check .`
   - Exit code: `0`
   - Output: `All checks passed!`
6. `.venv-m5-gate/Scripts/python -m ruff format --check .`
   - Exit code: `0`
   - Output: `76 files already formatted`
7. `.venv-m5-gate/Scripts/python -m mypy src tests`
   - Exit code: `0`
   - Output: `Success: no issues found in 65 source files`
8. Explicit inherited gate: one real adapter integration, five M3 security checks, and four M4 scoring/comparison checks.
   - Exit code: `0`
   - Output: `10 passed in 14.95s`
9. Benchmark validation CLI:
   - Flagship pack: exit `0`; output `Valid benchmark gauntlet.agent.mvp version 0.1.0 (schema 1, 15 scenarios)`.
   - Invalid fixture: explicit PowerShell `$LASTEXITCODE` returned `2`; the actionable error identified the missing manifest `title` field with no traceback.
10. `gauntlet doctor --artifact-root .tmp/m5-gate-doctor`
    - Exit code: `0`
    - Output: seven required checks passed: Python, OS, imports, child process, atomic artifact root, packaged policy, and packaged 15-scenario benchmark.
11. `gauntlet inspect examples`
    - Exit code: `0`
    - Result: detected a Python package, configured and recommended `sample_agent.app:run`, and reported source findings without importing user code.
12. Real sample evaluation:
    - Command: `gauntlet evaluate examples --benchmark gauntlet.agent.mvp --seed 42 --repeat 3 --offline --artifact-root .tmp/m5-fresh-demo --verbose`
    - Exit code: `0`
    - Output: run `run_20260715_183239_b1f2d1a3`, recommendation `ready`, score `99.16/100`, reproducibility `byte_identical (3 repeats)`, with a Markdown report path printed.

## Ten-Point MVP Acceptance

`.venv-m5-gate/Scripts/python -m pytest -v -p no:cacheprovider --basetemp .tmp/m5-fresh-acceptance tests/test_mvp_acceptance.py` exited `0`: **10 passed in 75.87s**.

1. Fresh-install distribution metadata and the `gauntlet` console entry point passed; the fresh-venv install above is the authoritative installation proof.
2. Offline `doctor` passed all seven required checks with an explicit writable artifact root.
3. The packaged flagship benchmark validated as version `0.1.0` with exactly 15 scenarios.
4. Three separate full correct-agent evaluations completed with 15 passed scenarios, completed manifests, ready recommendations, and all required report artifacts.
5. The degraded candidate produced critical security findings linked to persisted evidence and back to scenario results.
6. Manifest, result, scorecard, finding, and canonical JSON validated; every evidence envelope was strict-parsed, filename/hash verified, store-loaded, and linked.
7. The 15-row Markdown report was generated, disclosed the subprocess boundary, omitted raw fixtures, and redacted a synthetic secret from every persisted artifact.
8. Three separate deterministic evaluations produced byte-identical `canonical.json` files; each also contained three equal semantic repeats and an evidence-backed `byte_identical` claim.
9. Replacing one stable adapter target's implementation with the injection-vulnerable variant produced a comparable regression; `gauntlet compare` exited `1`.
10. The degraded full-pack evaluation exited `1` for policy failure while retaining a completed manifest and every required report artifact.

## Deviations

- Specs `00`-`16` and `reviews/` were not modified. No public plugin discovery, Docker isolation, LLM judge, LangGraph integration, or HTML reporting was implemented.
- The flagship scenarios use a five-second parent deadline rather than the initially authored two seconds. Two real Windows acceptance passes observed sandboxed child startup/reset times above two seconds, causing false timeouts and non-reproducible correct runs. No spec fixes a two-second value; five seconds is still bounded, and the independent parent-timeout/reap security test remains unchanged and green.
- M5 extends `reporting/` despite the default constraint because actual repeat execution, ADR-004 canonical output, honest live distributions, evidence-backed findings, source/benchmark fingerprints, and complete-report publication are required by the authorized M5 acceptance gate. `adapters/`, `execution/`, and `scoring/` remain untouched.
- Canonical adapter identity fingerprints project Python plus bounded prompt/config resource types and rejects installed targets outside the evaluated project. Root and `src/` layouts are supported. This prevents behavior-changing local resources or site-package drift from silently comparing as identical.
- The M4 `EvaluationRequest.reproducibility` constructor field is retained as a compatibility shim. Supplying it now fails with explicit migration guidance because M5 derives reproducibility only from persisted canonical repeat evidence; callers should configure `repeat >= 2`.
- Supplied findings cannot cite nonexistent evidence, including by spoofing the reserved reproducibility finding ID. Generated finding links use stable repeat/scenario/role selectors in canonical output.
- `--offline` means fixture-mode credential/proxy environment isolation, not OS-level socket denial. Subprocess execution remains explicitly documented as process separation rather than a hardened hostile-code sandbox.
- The committed example report is the actual redacted output from a successful Windows sample run. Its observed latency and platform fields are examples, not performance or cross-platform claims.
- Scenario 5 models a catchable synthetic tool-timeout response; scenario 11 separately proves a real adapter reset; scenario 15 is stable across externally supplied seeds, while the ADR-004 release claim correctly compares identical configured seeds.

## Blocked

None.

---

## Claude/Fable RC1 - Release Hardening

- **Branch:** `claude/rc1-release-hardening` (from integration tip `17cf790`, the M5 merge)
- **Role:** Claude/Fable as implementation owner; Codex to review independently.
- **Commits (all eight RC1 commits, in order):**
  - `37d1448` `CLAUDE-RC.1: add cross-platform release gates`
  - `613fc1e` `CLAUDE-RC.2: prove wheel installation and runtime assets`
  - `5b7ddbf` `CLAUDE-RC.4: document release readiness and security boundaries`
  - `94b8e5d` `CLAUDE-RC: append RC1 handoff section`
  - `6f2d9e6` `CLAUDE-RC.3: harden CLI help test against forced ANSI styling`
  - `cd63b86` `CLAUDE-RC.3: harden release-gate path check for Windows short paths`
  - `fdab0d2` `CLAUDE-RC: record CI round-1 findings in RC1 handoff`
  - `84d756d` `CLAUDE-RC: record green CI run in RC1 handoff`

## Work Completed

- **RC.1:** `.github/workflows/ci.yml` — test matrix (ubuntu/windows x Python 3.11/3.12: editable install, full pytest, ruff check, format check, strict mypy, `gauntlet --version`, flagship benchmark validation, six named security/adapter tests as a dedicated visible step) plus a `release-gate` job on both OSes.
- **RC.2:** `scripts/release_gate.py` — builds wheel + sdist, statically verifies wheel runtime assets (entry point, packaged policy, 15 flagship scenarios), installs only the wheel into a clean venv, and runs version/doctor/packaged-pack validation plus a bounded offline sample evaluation of a generated throwaway project from a neutral working directory.
- **RC.4:** `docs/release/RELEASE_READINESS_AND_SECURITY.md` — capabilities, verification record, trust boundaries, redaction/integrity design, known risks, pre-release command set, and the release verdict.

## Genuine Bugs Found and Fixed

- **Two, both revealed by the first real CI run and fixed with verification:**
  1. `CLAUDE-RC.3` (test hardening): rich force-enables ANSI styling under `GITHUB_ACTIONS`, splitting option names in the `evaluate --help` panel, so `tests/test_cli_m5.py::test_evaluate_help_lists_every_m5_flag` failed on all four CI matrix cells. Reproduced locally with `GITHUB_ACTIONS=true`; fixed by stripping ANSI escapes before asserting. Full suite verified green both with and without `GITHUB_ACTIONS=true` (268 passed each way). No runtime impact.
  2. `CLAUDE-RC.3` (release-gate script): Windows runners mix 8.3 short paths (`RUNNER~1`) and long paths (`runneradmin`) for the same temp directory, so the gate's raw-substring containment check wrongly failed the packaged-pack step on `windows-latest`. Fixed with resolved-path `is_relative_to` comparison; Linux gate re-verified locally, Windows via CI rerun.
- **The pre-CI adversarial audit found no runtime defect.** It probed: CLI error containment (nonexistent project, unwritable artifact root, unknown benchmark selector — all actionable messages, exit 2, no tracebacks), `--repeat 0` rejection, benchmark manifest `../` and absolute-path escapes (both rejected, exit 2), end-to-end malicious child stdout including fake protocol frames (contained; evaluation completed correctly), and wheel packaging (no defect — the M5 force-include ships the pack; verified by installing and evaluating from the wheel alone). **CI then found the two release-infrastructure defects above** — a test-only environment sensitivity and a gate-script path-comparison error — which is why the two `CLAUDE-RC.3` commits (`6f2d9e6`, `cd63b86`) exist. Neither is a runtime defect in GAUNTLET itself.
- One documented behavior (not fixed, by design): an artifact root placed inside the evaluated project makes repeat runs exit 4 because run artifacts legitimately change the project fingerprint. Recorded as a known limitation in the release document.

## Local Gate Results (Linux, CPython 3.11.15, unscrubbed host environment)

- `python -m pytest -p no:cacheprovider`: exit 0 — `268 passed in 47.03s`
- `ruff check .`: exit 0 — `All checks passed!`
- `ruff format --check .`: exit 0 — `77 files already formatted`
- `mypy src tests`: exit 0 — `Success: no issues found in 65 source files`
- `gauntlet --version`: exit 0 — `gauntlet 0.1.0`
- `gauntlet benchmark validate benchmarks/agent_mvp`: exit 0 — 15 scenarios
- `python scripts/release_gate.py`: exit 0 — `RELEASE GATE PASSED`; sample evaluation from the installed wheel completed `ready, score 100.00/100`

## CI Results

- Run 1 (`29461736356`, head `94b8e5d`): release gate (ubuntu) passed on the first attempt; all four test-matrix jobs failed on the ANSI help-test issue above; release gate (windows) failed on the short-path issue above. Both diagnosed from the real CI logs and fixed.
- Run 2 (`29462307830`, head `fdab0d2`): **conclusion success** — all six jobs green (test matrix ubuntu/windows x py3.11/3.12 including the full suite, lint, format, type check, flagship validation, and the six named security/adapter tests; plus both release-gate jobs, proving the wheel install end-to-end on Linux and Windows).

## Deviations

- The two `CLAUDE-RC.3` commits fix release-infrastructure defects revealed by the first CI run, not runtime defects; the pre-CI audit alone would have produced none.
- `scripts/` is not added to the mypy `files` list to avoid touching shared configuration; the release-gate script is fully typed and ruff/format-clean regardless.

## Known Remaining Risks

- macOS untested (POSIX code paths are exercised on Linux).
- Subprocess isolation is a failure boundary, not a hostile-code sandbox; `--offline` is environment isolation, not socket denial (both documented in the release document and reports).
- Windows CI runs on GitHub-hosted runners; local Windows development environments with sandbox ACL restrictions may need pytest temp redirection as documented in earlier milestones.

## Blocked

None.
