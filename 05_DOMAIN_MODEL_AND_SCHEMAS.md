# Domain Model and Schemas

## Core Entities

### EvaluationRun

```yaml
id: string
project_id: string
profile_id: string
benchmark_pack_ids: [string]
started_at: datetime
finished_at: datetime|null
status: pending|running|completed|failed|cancelled
seed: integer|null
environment_fingerprint: string
gauntlet_version: string
plugin_versions: object
summary: object
```

### Scenario

```yaml
id: string
title: string
description: string
category: string
difficulty: integer
tags: [string]
required_capabilities: [string]
input: object
fixtures: object
execution_policy: object
assertions: [object]
metrics: [string]
```

### ScenarioResult

```yaml
scenario_id: string
status: passed|failed|error|skipped
started_at: datetime
finished_at: datetime
duration_ms: integer
output: object|null
error: object|null
metrics: object
evidence_refs: [string]
findings: [string]
```

### Evidence

```yaml
id: string
type: trace|stdout|stderr|tool_call|artifact|metric|exception|judge_output
path: string
content_hash: string
redacted: boolean
metadata: object
```

### Finding

```yaml
id: string
severity: info|low|medium|high|critical
dimension: correctness|reliability|security|performance|cost|reproducibility|maintainability
title: string
description: string
evidence_refs: [string]
remediation: string|null
confidence: number
```

### ScoreCard

```yaml
overall: number
dimensions:
  correctness: number
  reliability: number
  security: number
  performance: number
  cost: number
  reproducibility: number
confidence: number
policy_id: string
```

## Run Artifact Layout

```text
artifacts/runs/<run_id>/
├── manifest.json
├── environment.json
├── config.resolved.yaml
├── results.json
├── scorecard.json
├── findings.json
├── report.md
├── logs/
├── traces/
├── evidence/
└── scenarios/
```

## Benchmark Pack Manifest

```yaml
id: gauntlet.agent.mvp
version: 0.1.0
title: Agent MVP Evaluation Pack
description: Core agentic system evaluation
schema_version: 1
required_capabilities:
  - invoke
  - trace_tool_calls
dimensions:
  - correctness
  - reliability
  - security
scenarios:
  - scenarios/basic_tool_use.yaml
  - scenarios/tool_failure_recovery.yaml
scoring_policy: scoring.yaml
```

## Configuration Precedence

1. CLI flags
2. environment variables
3. project config
4. profile defaults
5. package defaults

Resolved configuration must be saved with every run.
