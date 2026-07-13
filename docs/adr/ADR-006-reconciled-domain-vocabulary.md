# ADR-006: Reconciled Evaluation Dimensions and Timeout Status

**Status:** Accepted  
**Date:** 2026-07-13

## Context

05_DOMAIN_MODEL_AND_SCHEMAS.md and 10_REPORTING_AND_SCORING.md use overlapping dimension lists, while 06_EVALUATION_ENGINE.md describes timeout behavior without one normalized scenario-result value. Domain schemas, scoring, findings, and stored artifacts need one vocabulary before later milestones build on them.

The reconciled shape is authorized by the architect decision in reviews/M1.md at e59adf0.

## Decision Drivers

- one validated vocabulary across schemas and reports
- preservation of every dimension required by the specifications
- explicit timeout outcomes rather than ambiguous errors
- forward compatibility for finding-only dimensions
- deterministic scorecard serialization

## Options Considered

### Option A: Reconciled superset and explicit timeout status

Advantages:

- preserves all documented concepts without silent loss
- gives execution and reporting an unambiguous timeout value
- supports typed score-to-confidence pairs per scored dimension

Disadvantages:

- the schema contains a dimension not scored in the MVP
- consumers must distinguish scored dimensions from finding dimensions

### Option B: Use only the narrow MVP scoring vocabulary

Advantages:

- minimizes the initial enum and scorecard surface
- exactly mirrors the dimensions currently scored

Disadvantages:

- cannot type maintainability findings without another vocabulary
- collapses timeout into a less precise result status

## Decision

DimensionName is the superset correctness, reliability, security, performance, efficiency, cost, reproducibility, and maintainability. Maintainability is valid for findings but is not an MVP scored dimension. ScoreCard.dimensions maps each scored DimensionName to a DimensionScore containing score from 0 to 100 and confidence from 0 to 1; the scorecard retains overall confidence. ScenarioResult.status includes timed_out.

## Consequences

### Positive

- findings, results, and scorecards share one stable vocabulary
- timeouts are preserved explicitly through reporting
- per-dimension confidence is validated with each score

### Negative

- not every enum member must appear in an MVP scorecard
- scoring policy must select its allowed scored dimensions

### Risks

- clients may incorrectly assume all dimensions are always scored
- older fixtures may need migration to the reconciled shape

## Validation

- schema tests cover every dimension, timed_out, score bounds, and confidence bounds
- JSON Schema export tests preserve the reconciled enum and dimension-score mapping
- WP-4.2 policy tests will enforce that maintainability is not scored by the MVP policy

## Revisit Trigger

Revisit when maintainability becomes an approved scored dimension, or when a versioned schema migration introduces a new canonical dimension or result status.
