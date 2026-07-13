# ADR-003: Injected Tool Registry for Agent Evaluation

**Status:** Accepted
**Date:** 2026-07-13

## Context

Deterministic agent evaluation requires GAUNTLET to observe and control tool calls. Arbitrary interception is unreliable when a system under test constructs tools internally, imports global clients, or calls external services directly. The MVP therefore needs an honest integration boundary that supports fixture-driven tool behavior without claiming transparent interception of every Python agent.

## Decision Drivers

- deterministic, offline execution of tool-using scenarios
- complete trace and evidence capture for tool calls and results
- a framework-agnostic adapter contract
- explicit behavior instead of fragile monkey-patching
- prevention of accidental live service calls during fixture mode
- a small integration surface for sample and real projects

## Options Considered

### Option A: Require an injected tool registry

Advantages:

- makes the evaluated tool set explicit and controllable
- supports deterministic stubs and ordered fixture responses
- enables complete call tracing at the harness boundary
- works across frameworks through thin adapters or shims

Disadvantages:

- systems with hard-wired tools need an integration shim
- the evaluated path may differ from an application's default wiring
- application authors must expose a supported construction boundary

### Option B: Monkey-patch known framework clients

Advantages:

- may require fewer changes for supported frameworks
- can intercept some existing agents transparently

Disadvantages:

- is framework- and version-specific
- is fragile around aliases, cached objects, and indirect imports
- creates incomplete evidence when a call bypasses the patch

### Option C: Intercept network or operating-system calls

Advantages:

- can observe some hard-wired external calls without application changes

Disadvantages:

- cannot recover tool semantics reliably from arbitrary traffic
- adds substantial platform and security complexity
- does not cover local tools or in-process side effects consistently

## Decision

The MVP adapter contract requires the system under test to accept a tool registry supplied by the GAUNTLET harness. In deterministic fixture mode, the registry contains harness-controlled stub tools whose behavior comes from scenario fixtures such as `tool_sequence`. All evaluated tool calls must pass through that registry so the harness can record the requested tool, arguments, ordered response, error, timing, and policy result.

An agent that hard-wires its tools is not directly compatible with deterministic fixture mode. It must provide a thin shim that constructs the agent using the injected registry. GAUNTLET will document this boundary and report the adapter and fixture mode used; it will not claim universal transparent interception.

## Consequences

### Positive

- deterministic tool behavior is feasible without live credentials or services
- tool-call evidence is complete at a stable, framework-neutral boundary
- failure, retry, recovery, and forbidden-call scenarios can be reproduced
- the limitation for hard-wired agents is explicit and testable

### Negative

- adopters may need to refactor construction code or write a shim
- direct calls outside the registry are unsupported in deterministic fixture mode
- tool registry and fixture schemas become compatibility-sensitive contracts

### Risks

- an adapter could claim compliance while the agent retains an alternate live tool path
- fixtures may model external behavior too simplistically
- mutable tool state can leak between scenarios unless reset is enforced

## Validation

- the sample agent receives all tools through the injected registry
- integration tests prove fixture responses are consumed in the declared order
- traces contain every tool request, response, error, and retry used for assertions
- a hard-wired sample variant fails capability validation with an actionable shim message
- fixture mode tests prevent or flag calls that bypass the provided registry

## Revisit Trigger

Reconsider the boundary when a safe, general interception technique is demonstrated across supported frameworks, or when a first-party framework integration is approved. Public plugin entry-point discovery and framework-specific interception remain deferred beyond the MVP.
