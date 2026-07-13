# Product Requirements Document

## 1. Primary User

A solo AI engineer who wants rigorous evaluation without enterprise infrastructure.

## 2. Core User Journey

1. User installs GAUNTLET.
2. User initializes configuration in a project.
3. GAUNTLET discovers the project type.
4. User selects or accepts an evaluation profile.
5. GAUNTLET loads compatible benchmark packs.
6. Scenarios execute in a controlled environment.
7. Metrics and evidence are collected.
8. Scores are calculated.
9. Reports are generated.
10. Results can be compared with a previous run.

## 3. MVP Functional Requirements

### FR-001 Project Initialization

```bash
gauntlet init
```

Creates:

- `.gauntlet/config.yaml`
- benchmark directory
- adapter template
- ignore file
- sample profile

### FR-002 Project Inspection

```bash
gauntlet inspect .
```

Returns:

- detected framework
- probable entry points
- supported adapters
- available plugins
- warnings
- estimated evaluation cost

### FR-003 Evaluation

```bash
gauntlet evaluate .
```

Must:

- resolve configuration
- validate environment
- load benchmark pack
- execute scenarios
- collect evidence
- score dimensions
- write run artifacts
- generate reports

### FR-004 Regression Comparison

```bash
gauntlet compare RUN_A RUN_B
```

Must report:

- score changes
- fixed failures
- new failures
- latency changes
- cost changes
- behavioural regressions

### FR-005 Benchmark Validation

```bash
gauntlet benchmark validate PATH
```

Checks schema, fixtures, scoring definitions, expected artifacts, and compatibility.

### FR-006 Machine-Readable Output

Every run must produce normalized JSON.

### FR-007 Human-Readable Output

Every run must produce Markdown. HTML is post-MVP.

## 4. Non-Functional Requirements

- Python 3.11+
- Windows, Linux, and macOS support
- local execution without mandatory accounts
- resumable long-running evaluations
- stable run identifiers
- deterministic seeded mode
- structured logging
- bounded resource consumption
- secure secret handling
- plugin version tracking

## 5. MVP Supported Systems

At least one adapter must support:

- Python agent callable
- tool invocation
- multi-step execution
- structured final output

LangGraph support is desirable but should not compromise a framework-agnostic core.

## 6. MVP Exclusions

- public leaderboard
- paid benchmark marketplace
- hosted multi-tenant cloud
- enterprise RBAC
- remote execution fleet
- automated model fine-tuning
- official certification badges

## 7. Release Readiness

MVP is complete only when it evaluates a non-trivial sample agent and produces repeatable output across three runs under deterministic fixture mode.
