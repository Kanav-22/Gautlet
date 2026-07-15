# GAUNTLET sample agent

This package contains a deterministic, multi-step agent and six behavioral
variants for adapter integration tests and benchmark examples. Every callable
uses the same importable signature:

```python
def run(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    ...
```

The payload supplies a `key`. The agent calls the injected `lookup` tool with
`{"key": payload.get("key")}`. A lookup response shaped as
`{"value": ...}` contributes that inner value to the final result; other JSON
responses are used as-is. Agents that save call the injected `save` tool with
`{"value": value}`. Results always use this shape:

```json
{"completed": true, "value": "example", "saved": true}
```

`sample_agent.app:run` is the canonical adapter target when `examples/` is the
adapter project root. The modules under `variants` provide deliberately
distinct golden behaviors:

- `correct`: looks up once and saves once.
- `inefficient`: repeats the same lookup before saving.
- `hallucinating`: claims it saved without calling the save tool.
- `loop_prone`: retries a failed workflow no more than three times.
- `injection_vulnerable`: follows a structured instruction embedded in an
  untrusted lookup response, but can still invoke only injected stub tools.
- `recovery_capable`: retries one lookup error or error-shaped response once.

All behavior is local and fixture-driven. These examples never discover or
invoke real tools, perform network access, or read credentials.

## Flagship benchmark tasks

The `gauntlet.agent.mvp` pack supplies a `task` field so this deterministic
fixture can exercise all fifteen benchmark behaviours without an LLM. Payloads
without `task` retain the original lookup-then-save workflow above. The named
tasks cover direct answers, lookup-only and dependent calls, safe failure,
bounded recovery, instruction priority, untrusted retrieved content, state
reset, and stable seeded output.

The variants remain behavioural probes rather than alternate implementations:
the inefficient agent adds an unnecessary lookup, the hallucinating agent
omits required persistence, the loop-prone agent retries three times, the
injection-vulnerable agent follows structured hostile instructions, and the
recovery-capable agent stops or retries according to the synthetic error class.
Adapter reset coverage deliberately preconditions state in one child, verifies
the dirty state, restarts the child through `reset()`, and then evaluates the
pack's clean-state probe. Cross-seed reproducibility is likewise driven by the
caller because scenario-local seeds would override CLI configuration.
