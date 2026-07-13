# Testing and Acceptance

## Testing Pyramid

### Unit Tests

- configuration
- schemas
- scoring formulas
- assertion evaluators
- plugin compatibility
- redaction
- fingerprinting

### Integration Tests

- benchmark loading
- adapter invocation
- scenario execution
- evidence persistence
- report generation
- comparison

### End-to-End Tests

- initialize sample project
- evaluate sample agent
- produce report
- compare two runs

### Security Tests

- secret redaction
- timeout
- forbidden path access
- network denial
- malicious stdout
- plugin failure isolation

## Golden Fixtures

Create deterministic sample agents:

- correct agent
- inefficient agent
- hallucinating agent
- loop-prone agent
- injection-vulnerable agent
- recovery-capable agent

These fixtures allow known expected scores.

## MVP Acceptance Test

The implementation passes when:

1. fresh environment installation succeeds
2. `gauntlet doctor` passes
3. sample benchmark validates
4. sample agent evaluation completes
5. expected findings are produced
6. JSON conforms to schema
7. Markdown report is generated
8. repeated deterministic runs match
9. a deliberately degraded agent shows regression
10. CI exits non-zero when policy fails

## Quality Gates

- meaningful type hints
- linting
- unit test coverage target documented
- no secrets in repository
- no fabricated performance claims
- documentation matches actual CLI
- example commands are tested
