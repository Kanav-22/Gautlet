# Verdict and Implementation Plan

**Status:** Proposed
**Date:** 2026-07-13
**Scope:** Review of spec files `00`–`15` + `spec_manifest.json`; phased build plan with delegable work packages.

---

## Part 1 — Verdict

### Summary

**Build it.** The spec is coherent, internally consistent, and unusually disciplined for an idea-stage repo. The constraints (solo developer, one laptop, zero budget, Python-first, modular monolith) are realistic and the scope-discipline sections actively fight the usual failure mode of platform projects. The core bet — that *evidence-backed, reproducible, regression-oriented evaluation of agentic systems* is underserved — is correct.

### Strengths

1. **Evidence-over-opinion is the right differentiator.** Existing tools (DeepEval, promptfoo, Inspect AI, LangSmith evals) lean heavily on LLM-as-judge and per-prompt assertions. A release-readiness audit where every score traces to stored evidence, with security caps and regression comparison, is a genuinely distinct position.
2. **Deterministic-first ordering is correct.** Layer 1 (assertions) → Layer 2 (metrics) → Layer 3 (optional judge) keeps the MVP free of mandatory LLM dependencies, which also satisfies the zero-budget constraint.
3. **The adapter + fixture model is the right abstraction.** `invoke/reset/trace/usage` plus stubbed tool sequences lets one benchmark pack exercise any framework, and stubbed tools make scenarios cheap, fast, offline, and reproducible.
4. **Scope guardrails are explicit.** Non-goals, MVP exclusions, and "do not build dashboards before the engine works" are all written down. That is rare and valuable.

### Risks and gaps (what the spec underspecifies)

1. **Tool interception is the hard technical problem, and the spec glosses it.** Fixture injection (`tool_sequence`) assumes GAUNTLET can intercept the agent's tool calls. For an arbitrary Python agent that is non-trivial. *Resolution for MVP:* the adapter contract must require the system under test to accept an injected tool registry (the harness provides the tools). Agents that hard-wire their own tools need a thin shim, documented in the adapter template. This is an honest MVP boundary, not a hack — record it as ADR-003.
2. **Crowded adjacent market.** Inspect AI and DeepEval overlap on scenario execution. GAUNTLET must not compete on "runs evals" but on *audit artifacts*: evidence store, scorecard policy, release recommendation, regression diff. The report and `compare` command are the product; treat them as first-class from Phase 5, not polish.
3. **Subprocess isolation is a weak sandbox.** Fine for the MVP threat model (accidental damage, honest measurement) but it does not stop a genuinely malicious project. The spec already says this implicitly; reports must state the isolation level used so scores are never overclaimed.
4. **Reproducibility of nondeterministic systems is an open research problem.** For MVP, restrict the claim: deterministic fixture mode must be bit-repeatable; live-model mode reports variance across `--repeat N` runs and never claims determinism. Answers open questions 6–7 narrowly.
5. **Solo-developer surface area.** Even trimmed, this is CLI + engine + sandbox + scoring + reporting + a benchmark pack. The plugin SDK as a *public* contract should be deferred: build the internal interfaces (Protocols) now, publish entry-point discovery later. Same for LangGraph, Docker, and judge evaluation — all post-MVP.

### Proposed resolutions to key open questions (ADR candidates)

| # | Question | MVP decision |
|---|----------|--------------|
| 1 | SQLite vs files for run index | Files only; `runs list` scans `artifacts/runs/*/manifest.json`. SQLite when listing gets slow. |
| 3 | Adapter ↔ core communication | Subprocess with JSON-lines over stdin/stdout; harness injects stub tools into the child process. |
| 4 | Docker in MVP | No. Subprocess isolation only; Docker is Phase 7+. |
| 6–7 | Reproducibility claims | Fixture mode: exact repeatability required. Live mode: report variance, never a determinism claim. |
| 9 | Score with incomplete evidence | Show dimension scores with per-dimension confidence; overall becomes `evaluation_inconclusive` below policy minimums. |
| 10 | Security caps | Hard cap from policy (`critical_security_finding: 49`), applied after weighting, cited in the recommendation. |
| 12 | Subprocess sandbox acceptable? | Yes for MVP threat model; isolation level recorded in every report. |
| 15 | LangGraph plugin | Example integration, not first-party. Core stays framework-agnostic. |

---

## Part 2 — Implementation Plan

Compressed from the 9-phase roadmap into 6 milestones. Each milestone keeps the repo runnable and tested. Work packages (WP-*) are sized to be delegable to a coding agent (Codex) with this repo as context; review checkpoints are where a human (or second agent) verifies before the next milestone starts.

### Milestone 0 — Decisions and skeleton (½ day)

- **WP-0.1** Write ADR-001 (files-only persistence), ADR-002 (subprocess JSONL adapter protocol), ADR-003 (injected tool registry), ADR-004 (reproducibility claims) using `14_ADR_TEMPLATE.md`, in `docs/adr/`.
- **WP-0.2** Scaffold per `04_SYSTEM_ARCHITECTURE.md`: `pyproject.toml` (Python 3.11+, `typer`, `pydantic`, `pyyaml`, `rich`; dev: `pytest`, `ruff`, `mypy`), `src/gauntlet/` package tree, `tests/`, CI-ready lint/test commands, `gauntlet --version` working.

**Gate:** `pip install -e . && gauntlet --version && pytest` all pass in a fresh venv.

### Milestone 1 — Config, schemas, run artifacts (1–2 days)

- **WP-1.1** Pydantic models for every entity in `05_DOMAIN_MODEL_AND_SCHEMAS.md` (EvaluationRun, Scenario, ScenarioResult, Evidence, Finding, ScoreCard) + benchmark manifest + config, with JSON-schema export.
- **WP-1.2** Config loader with the 5-level precedence chain; resolved config saved per run.
- **WP-1.3** Run artifact store implementing the `artifacts/runs/<run_id>/` layout; stable run IDs (`run_YYYYMMDD_HHMMSS_<short-hash>`); `gauntlet init`, `gauntlet runs list`, `gauntlet runs show`.

**Gate:** unit tests for schema round-trips and precedence; `gauntlet init` produces the documented `.gauntlet/` layout.

### Milestone 2 — Benchmark loading and adapter contract (1–2 days)

- **WP-2.1** Benchmark pack loader: manifest + scenario YAML validation, capability checks, version tracking; `gauntlet benchmark validate PATH`.
- **WP-2.2** Adapter protocol (`reset/invoke/trace/usage`) and the `python_callable` adapter: child process launched with a restricted env allowlist, JSONL protocol, stub tool registry injected from scenario fixtures (`tool_sequence`), trace and usage capture.
- **WP-2.3** Sample agent in `examples/sample_agent/` — deterministic, tool-using, multi-step — plus the golden-fixture agent variants from `12_TESTING_AND_ACCEPTANCE.md` (correct, inefficient, hallucinating, loop-prone, injection-vulnerable, recovery-capable).

**Gate:** integration test invokes the sample agent through the adapter in a subprocess and captures a full trace.

### Milestone 3 — Execution engine and evidence (2–3 days)

- **WP-3.1** Scenario executor implementing the lifecycle state machine from `06_EVALUATION_ENGINE.md`: timeout, retry policy, cleanup, seeded fixture mode, bounded concurrency.
- **WP-3.2** Evidence store: content-hashed artifacts (trace, stdout/stderr, tool calls, exceptions) with secret redaction pass before persistence.
- **WP-3.3** Assertion engine — MVP assertion types: `tool_called`, `max_tool_calls`, `output_contains`, `output_field_equals`, `schema_valid`, `no_forbidden_calls`, `max_steps`, `no_hallucinated_success`, `completed_before_timeout`.

**Gate:** security tests (redaction, timeout kill, network-disabled env, malicious stdout) pass; every assertion result links to evidence refs.

### Milestone 4 — Metrics, scoring, reports (2 days)

- **WP-4.1** Metric collectors: task_success, latency, tool_calls, retries, recovery_steps, steps, token/cost estimate (from usage), exceptions.
- **WP-4.2** Scoring engine: normalization, dimension weights, security caps, minimum-scenario handling, confidence, release recommendation (`ready | ready_with_warnings | not_ready | evaluation_inconclusive`) citing policy rules; policy file per `10_REPORTING_AND_SCORING.md` example.
- **WP-4.3** Report generator: normalized `results.json` + `scorecard.json` + `findings.json`, and `report.md` (executive summary, findings, scenario table, environment). Exit codes per `11_CLI_AND_DEVELOPER_EXPERIENCE.md`.
- **WP-4.4** `gauntlet compare RUN_A RUN_B`: score deltas, fixed/new failures, latency and cost changes, config/benchmark-version change detection.

**Gate:** deliberately degraded golden agent produces a lower score and `compare` flags the regression; scores match hand-computed expected values for golden fixtures.

### Milestone 5 — Agent MVP pack and acceptance (2–3 days)

- **WP-5.1** Author the 15 MVP scenarios from `07_AGENT_EVALUATION_PACK.md` as `benchmarks/agent_mvp/` (manifest, scenario YAMLs, scoring policy, fixtures).
- **WP-5.2** `gauntlet inspect` (framework/entry-point detection for python-callable projects) and `gauntlet doctor`.
- **WP-5.3** End-to-end acceptance: run the 10-point checklist in `12_TESTING_AND_ACCEPTANCE.md`, including three bit-identical deterministic runs; commit an example generated report; write `docs/quickstart.md` targeting the 15-minute goal.

**Gate:** full MVP acceptance list passes on Linux; `gauntlet evaluate .` on the sample agent is the working demo.

**Deferred (post-MVP, in spec order):** public plugin entry-point discovery, Docker isolation, judge evaluation (Layer 3), LangGraph example, HTML reports, Windows validation, first real-project evaluation (Phase 8).

### Working with Codex

- **Delegation unit = one WP.** Each WP above is self-contained, names its spec source files, and has a testable gate. Prompt template: *"Read `00_README.md`, `04_SYSTEM_ARCHITECTURE.md`, `<WP spec files>`, and `16_VERDICT_AND_IMPLEMENTATION_PLAN.md`. Implement WP-X.Y exactly as scoped. Add unit tests. Do not implement items listed as deferred. Keep the repo runnable."*
- **Sequence strictly by milestone; parallelize within one.** WPs inside a milestone are independent enough to run as parallel agent tasks (e.g. WP-2.1 and WP-2.3); milestones are not.
- **Review checkpoints are the gates.** After each milestone, a human or reviewing agent verifies the gate before the next milestone begins — this is where agent-generated drift from the spec gets caught cheaply.
- **Honesty rules from `01_MASTER_EXECUTION_PROMPT.md` apply to the agent:** no fabricated test results, no hiding unresolved problems behind abstractions, stop-and-document on spec conflicts.

### Effort estimate

Roughly **9–13 focused days** of agent-assisted work to MVP acceptance, dominated by Milestones 3 and 5. The critical path is WP-2.2 (adapter + tool interception) — validate it early, since every scenario depends on it.
