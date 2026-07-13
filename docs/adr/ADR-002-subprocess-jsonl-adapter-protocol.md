# ADR-002: Subprocess JSONL Adapter Protocol

**Status:** Accepted
**Date:** 2026-07-13

## Context

GAUNTLET must evaluate Python agentic systems without importing framework-specific objects into the core process. The adapter boundary needs to carry lifecycle requests, invocation inputs, outputs, trace data, usage data, and structured errors while allowing the orchestrator to enforce timeouts and terminate a failed system under test. The MVP boundary is process isolation, not a hardened security sandbox.

## Decision Drivers

- failure isolation between GAUNTLET and the system under test
- a framework-neutral, language-neutral message boundary
- deterministic capture of requests, responses, and protocol failures
- low dependency and operational cost on one laptop
- support for parent-enforced timeouts and process termination
- a path to stronger isolation without changing core evaluation semantics

## Options Considered

### Option A: In-process Python calls

Advantages:

- lowest invocation overhead
- simple debugging and direct object access

Disadvantages:

- agent crashes, global state, and imports can corrupt the evaluator
- timeout enforcement and cleanup are unreliable
- couples the core to Python and framework-specific behavior

### Option B: Child process with JSON Lines over standard streams

Advantages:

- separates interpreter state and failure boundaries
- uses a simple streaming format with standard-library support
- lets the parent enforce timeout and termination policy
- avoids running a network service

Disadvantages:

- serialization limits payloads to explicit JSON-compatible data
- process startup and message handling add overhead
- standard output must be reserved for protocol messages
- process isolation alone does not contain malicious code

### Option C: Local HTTP, RPC, or container transport

Advantages:

- supports richer service and cross-language integration patterns
- can evolve toward remote or container execution

Disadvantages:

- adds ports, service lifecycle, dependencies, and failure modes
- exceeds MVP needs and laptop constraints
- a container transport could overstate the current isolation guarantee

## Decision

The MVP adapter runs the system under test in a child process and communicates through UTF-8 JSON Lines on standard input and standard output. Each line is one complete JSON object. Protocol messages carry a protocol version, message type, correlation identifier, and JSON-compatible payload or structured error. Standard output is reserved for protocol traffic; system diagnostics are captured separately from standard error.

The parent GAUNTLET process owns process creation, request deadlines, termination, and cleanup. The protocol supports the adapter operations `reset`, `invoke`, `trace`, and `usage`. The isolation level used for a run is recorded in its evidence and report. This decision does not claim that a subprocess is a security sandbox.

## Consequences

### Positive

- core code remains independent of agent frameworks and interpreter state
- crashes and hangs have an enforceable process boundary
- requests and responses can be persisted as auditable evidence
- the transport can be implemented without a server or hosted dependency

### Negative

- adapters must translate values into stable JSON schemas
- accidental output on standard output can corrupt the protocol
- large artifacts need file references or bounded messages rather than raw streaming
- subprocess startup affects very short scenario latency measurements

### Risks

- malformed, oversized, or adversarial messages could exhaust parser resources
- a child process still has the operating-system permissions of its parent unless restricted
- protocol evolution can break adapters without explicit compatibility checks

## Validation

- integration tests exercise `reset`, `invoke`, `trace`, and `usage` through a real child process
- malformed JSON, unknown versions, unknown message types, and structured child errors fail predictably
- timeout tests prove the parent terminates a hanging child and captures evidence
- diagnostic output cannot be mistaken for protocol output
- reports identify the isolation level as subprocess isolation

## Revisit Trigger

Reconsider the transport when an approved use case requires non-local adapters, sustained high message volume, or stronger isolation such as containers. Any replacement must preserve versioned messages, correlation, evidence capture, and parent-controlled lifecycle semantics.
