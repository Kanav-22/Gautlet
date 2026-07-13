# Security and Threat Model

## Security Objective

GAUNTLET executes potentially unsafe systems and adversarial inputs. The evaluator itself must not become the attack surface.

## Protected Assets

- host machine
- project secrets
- benchmark integrity
- hidden fixtures
- report authenticity
- user files
- network credentials
- evaluation history

## Threat Actors

- malicious project under test
- malicious plugin
- malicious benchmark pack
- prompt-injected agent
- compromised dependency
- accidental destructive command
- benchmark author leaking hidden answers

## Primary Threats

### Arbitrary Code Execution

Projects under test may execute destructive code.

Mitigation:

- subprocess isolation minimum
- restricted working directory
- explicit environment allowlist
- timeout
- optional Docker sandbox
- no host secret inheritance by default

### Secret Leakage

Mitigation:

- secret redaction
- environment filtering
- evidence scanning
- prevent reports from storing raw secrets

### Benchmark Tampering

Mitigation:

- manifest hashes
- immutable run copy
- pack versioning
- optional signature support later

### Prompt Injection

External benchmark content may attempt to control the evaluator or system under test.

Mitigation:

- separate control instructions from scenario payload
- treat retrieved content as untrusted data
- deterministic policy checks
- record instruction boundaries

### Malicious Plugins

Mitigation:

- explicit trust warning
- plugin permission declarations
- optional isolated plugin execution
- installation provenance in reports

## Secure Defaults

- network disabled unless required
- secrets excluded
- destructive filesystem access denied
- shell disabled unless explicitly enabled
- benchmark outputs stored outside project source
- no automatic upload

## Security Findings

Security findings must include:

- affected component
- attack path
- reproducibility steps
- evidence
- severity
- remediation
- confidence
