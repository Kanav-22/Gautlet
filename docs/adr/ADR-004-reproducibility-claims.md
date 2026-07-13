# ADR-004: Reproducibility Claims by Execution Mode

**Status:** Accepted
**Date:** 2026-07-13

## Context

GAUNTLET evaluates both deterministic fixture-driven systems and systems that may depend on live models or services. A single reproducibility claim would be misleading: fixture mode can control tools, inputs, configuration, and seeds, while live execution retains sampling and provider variance. Reports must distinguish evidence-backed repeatability from observed variance.

## Decision Drivers

- truthful release-readiness claims backed by stored evidence
- exact repeatability for the deterministic MVP demonstration
- useful comparison of nondeterministic systems without false certainty
- explicit capture of seed, configuration, versions, and environment
- offline validation without a mandatory hosted model
- stable regression artifacts and explainable confidence

## Options Considered

### Option A: Claim reproducibility for every successful run

Advantages:

- presents one simple product claim

Disadvantages:

- is false for many live models and external services
- hides variance and weakens confidence in regression results
- conflicts with evidence-first reporting

### Option B: Make claims conditional on execution mode

Advantages:

- requires exact repeatability where GAUNTLET controls the inputs
- reports measured variance where GAUNTLET does not control sampling
- gives users an explicit basis for interpreting comparisons

Disadvantages:

- requires mode-aware metrics, reporting, and acceptance tests
- deterministic comparisons must define and exclude documented run-identity fields
- live-mode confidence depends on the number and diversity of repeats

### Option C: Make no reproducibility claims

Advantages:

- avoids overstating guarantees
- reduces implementation effort

Disadvantages:

- abandons a core product dimension
- provides no release signal for deterministic systems
- makes regressions harder to distinguish from variance

## Decision

Reproducibility claims are mode-specific.

In deterministic fixture mode, repeated execution with the same benchmark version, adapter version, resolved configuration, seed, fixtures, and environment fingerprint must produce byte-identical canonical evaluation outputs and evidence payloads. Run identity and lifecycle metadata, such as run IDs and wall-clock timestamps, are retained for auditability but are explicitly excluded from the canonical repeatability comparison. A mismatch is reported as a non-reproducible result; GAUNTLET must not issue a positive reproducibility claim for that evaluation.

In live-model or live-service mode, GAUNTLET never claims determinism. With `--repeat N`, it reports the observed distribution and variance of relevant outcomes and records the repeat count, configuration, model or service identifiers, and environment fingerprint. A single live run is reported as insufficient evidence for a reproducibility claim.

## Consequences

### Positive

- reports make only claims supported by the execution conditions and evidence
- deterministic fixtures provide a strict offline acceptance target
- live evaluations remain useful without disguising expected variance as regression
- environment or configuration changes can be separated from result changes

### Negative

- canonical serialization and stable ordering are required for deterministic artifacts
- repeat runs increase execution time and cost in live mode
- thresholds for statistically meaningful live-mode changes remain policy work

### Risks

- undocumented volatile fields could create false repeatability failures
- too few live repeats could produce misleading variance estimates
- provider-side changes may occur without a visible model identifier change

## Validation

- acceptance runs execute the same deterministic fixture evaluation three times and compare canonical outputs and evidence byte for byte
- changing the seed, fixture, benchmark version, resolved configuration, adapter version, or environment fingerprint invalidates direct repeatability comparison
- an intentionally nondeterministic fixture produces a `non-reproducible result` finding
- live-mode reports include repeat count and variance and contain no determinism claim
- regression comparison distinguishes measured result changes from configuration, benchmark, and environment changes

## Revisit Trigger

Revisit live-mode confidence and minimum repeat policies when GAUNTLET has representative empirical data for supported systems. Revisit the canonical comparison set if required audit metadata cannot be separated from deterministic evaluation content without weakening evidence integrity.
