# GAUNTLET Release Readiness and Security Boundaries

**Scope:** MVP (`gauntlet` 0.1.0) at the RC1 integration point.
**Author:** Claude (Fable), acting as architect/reviewer across M0–M5 and
implementation owner for RC1. Every claim below is backed by a test, a gate
transcript in `HANDOFF.md`, or a review record in `reviews/`.

## What GAUNTLET can do today

- `gauntlet evaluate PATH` runs a Python-callable agent through the packaged
  15-scenario `gauntlet.agent.mvp` benchmark in an isolated child process with
  deterministic stubbed tools, and produces `results.json`, `scorecard.json`,
  `findings.json`, `canonical.json`, and a human-readable `report.md` under a
  run directory that is outside the project source by default (the artifact
  root is overridable; placing it inside the project makes repeat runs fail
  closed, as documented under known risks).
- Scores are policy-driven (`agent_mvp_default`: weighted dimensions, security
  caps, scenario minimums) and every applied rule is cited in the scorecard.
  Release recommendations are `ready`, `ready_with_warnings`, `not_ready`, or
  `evaluation_inconclusive`.
- With `--repeat 3 --seed N`, deterministic fixture-mode runs are compared
  byte-for-byte; the reproducibility claim appears only when canonical outputs
  are actually identical (ADR-004).
- `gauntlet compare RUN_A RUN_B` reports score/failure/latency/cost deltas,
  flags evidence-backed regressions (exit 1), and explicitly distinguishes
  configuration, benchmark-version, and environment changes from regressions.
- `gauntlet init`, `inspect`, `doctor`, `benchmark validate`, and `runs
  list/show` support the workflow end to end. Documented exit codes: 0 pass,
  1 policy failure/regression, 2 configuration error, 3 execution error,
  4 security boundary, 5 incomplete artifacts.

## What was independently verified

Every milestone was implemented by one agent (Codex) and gated by a second
(Claude) on a different operating system before merge:

- M0–M5 each passed a fresh-environment gate on **Windows (CPython 3.12)** and
  **Linux (CPython 3.11)**: editable install, full pytest, ruff, strict mypy,
  and milestone-specific acceptance checks. Final suite: **268 tests**.
- The 10-point MVP acceptance suite (`tests/test_mvp_acceptance.py`) passes,
  including three byte-identical deterministic evaluations and a
  policy-failure run that exits 1 while retaining completed artifacts.
- Two genuine cross-platform defects were caught by the second-OS gate and
  fixed with regression tests: a venv-escaping interpreter resolution in the
  adapter (M2) and secret-redaction corruption from short environment values
  (M5). Details in `reviews/M2.md` and `reviews/M5.md`.
- RC1 added an executed wheel-installation proof: the built wheel, installed
  alone into a clean venv, passes version/doctor/packaged-pack validation and
  completes a bounded offline sample evaluation (`scripts/release_gate.py`,
  "RELEASE GATE PASSED").

## Trust and isolation boundaries — read before evaluating untrusted code

- **Subprocess isolation is a failure boundary, not a security sandbox**
  (ADR-002). The child process runs with the same OS user permissions as the
  parent. GAUNTLET protects itself against crashes, hangs, protocol
  corruption, and dishonest output — it does **not** contain genuinely
  malicious code. Do not evaluate untrusted projects without an external
  sandbox (container/VM).
- **`--offline` and `network: disabled` are environment isolation, not socket
  denial.** The child receives a minimal allowlisted environment (no proxy
  settings, API keys, or credentials), so calls that depend on inherited
  credentials or proxy configuration lose that configuration — but **all
  socket access remains possible**: an unauthenticated public request, a URL
  supplied in scenario input, or any hard-coded endpoint can still connect
  unless an external firewall, container, or VM blocks it. OS-level network
  denial is deferred (Docker isolation is post-MVP).
- The child's stdout is reserved for the framed JSONL protocol; agent prints
  and tracebacks are redirected to captured, bounded stderr. Malicious or
  malformed protocol output terminates the child and is recorded as evidence
  — verified by tests and by an end-to-end probe during the RC1 audit.
- Timeouts are parent-enforced with terminate→kill escalation and child
  reaping; a hanging agent cannot stall an evaluation.

## Evidence integrity and redaction

- Secrets are redacted **in memory before anything is written**. Redaction
  literals come from secret-named environment variables (≥8 characters, see
  `SECRET_LITERAL_MIN_LENGTH`) plus configurable patterns; redacted-key
  collisions fail closed rather than persisting ambiguous data.
- Every evidence artifact is content-addressed: its `sha256` covers the exact
  redacted bytes on disk, so integrity checking never requires secrets to have
  existed on disk. Tampered evidence is detected on load.
- Assertion results must reference verified evidence IDs; findings cannot cite
  nonexistent evidence. Token/cost metrics are only reported when the adapter
  actually observed them — absent data is never estimated or zero-filled.

## Known risks and deferred features

- **Deferred by design:** public plugin entry-point discovery, Docker/OS-level
  isolation, LLM-judge evaluation, LangGraph integration, HTML reports.
- Only the `python_callable` adapter exists; agents that hard-wire their tools
  need a thin shim to accept the injected tool registry (ADR-003).
- Placing the artifact root inside the evaluated project makes repeat runs
  fail with exit 4 ("evaluated source changed") because run artifacts change
  the project fingerprint. This is honest fail-closed behavior; keep the
  artifact root outside the project (the default already is).
- Windows coverage in CI uses GitHub-hosted runners; the ACL failures
  encountered during development were not reproduced outside the Codex
  Windows sandbox environment, and no end-user report of them exists.
- macOS is untested (expected to behave like Linux; both POSIX paths are
  exercised), and the scoring normalization curves/recommendation bands are
  project-defined defaults recorded in the policy file, not spec-mandated.

## Commands required before a release

Run from a clean checkout; every command must exit 0:

```bash
python -m venv .venv && . .venv/bin/activate   # Scripts\activate on Windows
pip install -e ".[dev]"
gauntlet --version
python -m pytest
ruff check . && ruff format --check .
mypy src tests
gauntlet benchmark validate benchmarks/agent_mvp
gauntlet doctor --artifact-root "$(mktemp -d)"
python scripts/release_gate.py                  # wheel build + install proof
```

CI (`.github/workflows/ci.yml`) runs the same set on ubuntu/windows ×
Python 3.11/3.12 plus the release gate on both OSes.

## Verdict

**Ready for an initial (0.1.0) release**, with the trust boundaries above
stated in the release notes. Evidence: 268 passing tests including the
10-point MVP acceptance suite on two operating systems and two Python
versions; an executed wheel-installation gate; six milestone reviews
(M0 through M5) with independent second-OS verification; and an RC1
adversarial audit that probed subprocess lifecycle, environment isolation,
redaction, path containment, protocol corruption, CLI error handling, and
comparison honesty **without finding a reproducible runtime defect**. The
first real CI run then exposed two release-infrastructure defects (a
test-only ANSI sensitivity and a gate-script path comparison), both fixed
with regression verification — as were the two runtime defects caught by
second-OS review earlier in development. Known remaining risks are
documented above.
