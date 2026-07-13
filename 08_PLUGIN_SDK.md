# Plugin SDK

## Objective

Allow domain-specific evaluation logic without coupling the core to frameworks.

## Plugin Types

- project detector
- system adapter
- benchmark provider
- metric collector
- assertion type
- report extension
- security scanner

## Plugin Manifest

```yaml
id: gauntlet.plugins.langgraph
version: 0.1.0
api_version: 1
entry_point: gauntlet_langgraph.plugin:LangGraphPlugin
capabilities:
  - detect_project
  - create_adapter
dependencies:
  python: ">=3.11"
  gauntlet: ">=0.1,<0.2"
```

## Core Protocols

```python
from typing import Protocol, Any

class SystemAdapter(Protocol):
    def reset(self) -> None: ...
    def invoke(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def trace(self) -> list[dict[str, Any]]: ...
    def usage(self) -> dict[str, Any]: ...

class MetricCollector(Protocol):
    id: str
    def collect(self, context: "ScenarioContext") -> dict[str, float]: ...

class Assertion(Protocol):
    type: str
    def evaluate(self, context: "ScenarioContext", config: dict[str, Any]) -> "AssertionResult": ...
```

## Plugin Rules

- plugins must declare capabilities
- plugins must declare compatible API versions
- plugins must not mutate global configuration
- plugin failures must not corrupt the run
- plugin output must be serializable
- plugin logs must be namespaced
- plugin dependencies must be explicit

## Discovery

Use Python entry points for installed plugins.

Support local development plugins through explicit paths.

## Versioning

The core SDK follows semantic versioning.

Breaking plugin API changes require a new API version.

## Reference Plugin

Implement one reference plugin named `gauntlet-agent-core`.

It should be framework agnostic and use a Python callable adapter.
