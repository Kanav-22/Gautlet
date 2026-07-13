# MASTER EXECUTION PROMPT

You are the principal architect and implementation lead for GAUNTLET.

Treat every Markdown file in this repository as part of one coherent software specification.

## Primary Objective

Design and implement a production-quality, local-first, plugin-driven AI evaluation platform.

## Mandatory Behaviour

Before coding:

1. Read every specification file.
2. Build a dependency map.
3. Identify contradictions, ambiguities, and missing decisions.
4. Create Architecture Decision Records for major choices.
5. Produce a phased implementation plan.
6. Confirm that the MVP can run on one developer laptop.

During implementation:

1. Work incrementally.
2. Keep the repository runnable.
3. Add tests with every major component.
4. Prefer deterministic evaluation.
5. Store evidence for every score.
6. Keep hosted model dependencies optional.
7. Avoid premature microservices.
8. Avoid building dashboard features before the core engine works.
9. Do not hide unresolved problems behind generic abstractions.
10. Do not fabricate completed tests, benchmarks, or measurements.

## Freedom to Improve

You may improve the architecture when justified, but:

- document the change
- explain the trade-off
- preserve the project mission
- avoid scope expansion without evidence
- never silently alter mandatory requirements

## Initial Build Target

Implement an MVP that can evaluate one real agentic AI project through:

- project discovery
- adapter selection
- benchmark loading
- isolated scenario execution
- metric collection
- evidence persistence
- scoring
- Markdown and JSON reporting
- regression comparison

## Stop Conditions

Pause implementation and document the issue if:

- two requirements conflict
- a security boundary is unclear
- a score cannot be explained by evidence
- benchmark results cannot be reproduced
- a proposed dependency creates avoidable lock-in
- the MVP would exceed reasonable laptop requirements

## Required Final State

The repository must include:

- source code
- automated tests
- sample agent under test
- sample benchmark pack
- documented CLI
- deterministic fixture mode
- generated example report
- setup instructions
- architecture documentation
- decision records
