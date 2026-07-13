# Implementation Roadmap

## Phase 0 — Architecture Validation

Deliver:

- dependency map
- contradiction report
- ADRs
- refined repository plan
- threat-model review

## Phase 1 — Core Skeleton

Deliver:

- package
- CLI
- configuration
- schemas
- run artifact layout
- logging
- versioning

## Phase 2 — Benchmark and Adapter Contracts

Deliver:

- benchmark loader
- manifest validation
- scenario schema
- Python callable adapter
- reference benchmark pack

## Phase 3 — Execution and Evidence

Deliver:

- subprocess runner
- timeout
- trace collection
- evidence store
- redaction
- deterministic fixtures

## Phase 4 — Metrics and Scoring

Deliver:

- assertion engine
- metric collectors
- scoring policy
- confidence handling
- release recommendation

## Phase 5 — Reports and Comparison

Deliver:

- JSON report
- Markdown report
- regression comparison
- example reports

## Phase 6 — Agent Evaluation MVP

Deliver:

- agent scenario pack
- tool trace support
- failure recovery tests
- injection tests
- loop detection
- reproducibility tests

## Phase 7 — Hardening

Deliver:

- Windows validation
- Linux validation
- security tests
- plugin failure isolation
- performance profiling
- documentation verification

## Phase 8 — First Real Project Evaluation

Evaluate one existing agentic codebase.

Document:

- adapter effort
- benchmark gaps
- false positives
- false negatives
- performance
- next priorities

## Scope Discipline

Do not implement:

- dashboard
- cloud registry
- marketplace
- public leaderboard

until the CLI MVP is reliable.
