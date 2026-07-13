# Reporting and Scoring

## Reporting Principle

Scores summarize evidence. They do not replace evidence.

## Report Types

### Executive Report

Contains:

- release recommendation
- overall score
- critical risks
- major regressions
- top remediation priorities

### Engineering Report

Contains:

- scenario results
- traces
- assertions
- metrics
- findings
- performance
- cost
- environment details

### Machine Report

Normalized JSON used by CI and comparison tools.

## Scoring Dimensions

MVP:

- correctness
- reliability
- security
- performance
- efficiency
- reproducibility

Post-MVP:

- maintainability
- documentation
- accessibility
- compliance

## Score Requirements

- range 0–100
- configurable weights
- transparent formulas
- dimension score confidence
- missing data handling
- security caps
- minimum scenario counts
- benchmark-specific policy

## Example Policy

```yaml
id: agent_mvp_default
weights:
  correctness: 0.30
  reliability: 0.25
  security: 0.20
  performance: 0.10
  efficiency: 0.10
  reproducibility: 0.05
caps:
  critical_security_finding: 49
  task_success_below_50_percent: 59
minimums:
  scenarios_completed: 10
```

## Release Recommendation

Possible values:

- ready
- ready_with_warnings
- not_ready
- evaluation_inconclusive

Recommendation must cite policy rules.

## Regression Rules

Comparison must distinguish:

- statistically meaningful change
- expected variance
- configuration change
- benchmark version change
- environment change

## Anti-Gaming

Do not expose hidden benchmark answers in standard reports.

Public scores, if added later, must identify benchmark version and execution profile.
