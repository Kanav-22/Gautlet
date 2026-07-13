# GAUNTLET Specification Repository

## What This Repository Is

This repository is the authoritative design and execution specification for **GAUNTLET**, an AI Quality Operating System.

GAUNTLET is not an AI builder. It is an evaluation, adversarial testing, benchmarking, evidence, and reporting platform for AI systems.

The intended primary consumer of this repository is a frontier implementation model with coding, planning, architecture, testing, and repository-editing capabilities.

## Mission

Create a reusable system that can answer:

> Is this AI system reliable, secure, reproducible, efficient, and ready to release?

## Initial Product Focus

The first production target is evaluation of **agentic AI systems**:

- tool-calling agents
- LangGraph workflows
- MCP-based agents
- autonomous task executors
- multi-step assistants
- RAG-enabled agents
- local and hosted LLM workflows

## Operating Constraints

Assume:

- one primary developer
- one laptop
- limited or zero budget
- open-source preference
- free tiers where practical
- no permanent dependence on a frontier model
- Python-first implementation

## Required Reading Order

1. `00_README.md`
2. `01_MASTER_EXECUTION_PROMPT.md`
3. `02_PRODUCT_VISION.md`
4. `03_PRD.md`
5. `04_SYSTEM_ARCHITECTURE.md`
6. `05_DOMAIN_MODEL_AND_SCHEMAS.md`
7. `06_EVALUATION_ENGINE.md`
8. `07_AGENT_EVALUATION_PACK.md`
9. `08_PLUGIN_SDK.md`
10. `09_SECURITY_AND_THREAT_MODEL.md`
11. `10_REPORTING_AND_SCORING.md`
12. `11_CLI_AND_DEVELOPER_EXPERIENCE.md`
13. `12_TESTING_AND_ACCEPTANCE.md`
14. `13_IMPLEMENTATION_ROADMAP.md`
15. `14_ADR_TEMPLATE.md`
16. `15_OPEN_QUESTIONS.md`

## Execution Rule

Do not begin large-scale implementation before the architecture, domain model, execution model, plugin contract, and scoring system are internally consistent.

## Definition of Success

A user can run:

```bash
gauntlet evaluate .
```

against a real agentic AI project and receive a reproducible, evidence-backed report covering:

- task success
- tool use
- recovery
- reliability
- security
- performance
- cost
- reproducibility
- release readiness
