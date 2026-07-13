# CLI and Developer Experience

## Commands

```bash
gauntlet init
gauntlet inspect PATH
gauntlet evaluate PATH
gauntlet compare RUN_A RUN_B
gauntlet runs list
gauntlet runs show RUN_ID
gauntlet benchmark validate PATH
gauntlet plugins list
gauntlet doctor
```

## Evaluate Examples

```bash
gauntlet evaluate .
gauntlet evaluate . --profile strict
gauntlet evaluate . --benchmark gauntlet.agent.mvp
gauntlet evaluate . --scenario agent.tool_failure_recovery
gauntlet evaluate . --seed 42
gauntlet evaluate . --repeat 3
gauntlet evaluate . --offline
```

## Exit Codes

- 0 evaluation completed and release policy passed
- 1 evaluation completed and release policy failed
- 2 configuration error
- 3 execution error
- 4 security boundary violation
- 5 incomplete evaluation

## UX Requirements

- clear progress
- quiet mode
- verbose mode
- no misleading percentage when total work is unknown
- actionable errors
- path to report printed on completion
- colour optional
- CI-safe non-interactive mode

## Configuration Example

```yaml
project:
  name: sample-agent
adapter:
  type: python_callable
  target: sample_agent.app:run
evaluation:
  benchmark_packs:
    - gauntlet.agent.mvp
  seed: 42
  repeat: 1
  timeout_seconds: 60
execution:
  network: disabled
  isolation: subprocess
reporting:
  formats: [json, markdown]
scoring:
  policy: agent_mvp_default
```

## Developer Experience Goal

A technically capable user should reach the first successful evaluation within fifteen minutes of installation.
