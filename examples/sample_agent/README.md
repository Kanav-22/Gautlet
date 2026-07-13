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
