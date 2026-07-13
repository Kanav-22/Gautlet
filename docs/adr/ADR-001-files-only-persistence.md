# ADR-001: Files-Only Persistence for MVP Run Data

**Status:** Accepted
**Date:** 2026-07-13

## Context

GAUNTLET needs durable, inspectable storage for evaluation manifests, resolved configuration, evidence, results, scorecards, findings, and reports. The MVP must run locally on one developer laptop, remain usable without a service dependency, and preserve the documented `artifacts/runs/<run_id>/` layout. The product also needs `runs list` and `runs show` behavior, but the expected MVP data volume does not yet justify a separate index.

## Decision Drivers

- local-first operation with no database service
- evidence that users can inspect, copy, archive, and version independently
- low installation and maintenance cost for one developer
- portability across supported filesystems and CI environments
- a single canonical representation for run artifacts
- avoidance of premature indexing infrastructure

## Options Considered

### Option A: Files are canonical and run listing scans manifests

Advantages:

- matches the documented artifact layout directly
- requires no database lifecycle, migration, or recovery tooling
- keeps every run self-contained and easy to inspect or archive
- works offline and minimizes dependencies

Disadvantages:

- listing performance grows linearly with the number of runs
- cross-run queries require reading multiple manifests
- concurrent writers require careful directory and file handling

### Option B: Files plus a SQLite run index

Advantages:

- supports fast listing, filtering, and cross-run queries
- remains local and has little operational overhead

Disadvantages:

- introduces schema migrations and index consistency concerns
- creates two representations that can drift or require rebuilding
- adds complexity before MVP query volume is known

### Option C: A database is the canonical store

Advantages:

- supports transactions and rich queries
- can scale to many runs and concurrent clients

Disadvantages:

- conflicts with the simple, inspectable artifact contract
- adds avoidable deployment and maintenance cost
- makes portable evidence bundles harder to preserve

## Decision

For the MVP, the filesystem is the only persistence mechanism and the run directory is the canonical record. Each run is stored under `artifacts/runs/<run_id>/` using the layout defined in `05_DOMAIN_MODEL_AND_SCHEMAS.md`. Commands that enumerate runs scan `artifacts/runs/*/manifest.json`; commands that inspect a run read that run's files directly.

No SQLite or remote database is required for MVP operation. A future index may be derived from canonical run files, but losing or rebuilding that index must never lose evaluation evidence.

## Consequences

### Positive

- installation remains service-free and offline-capable
- run artifacts stay transparent, portable, and independently auditable
- backup and retention can use ordinary filesystem tools
- implementation work can focus on evidence correctness rather than index maintenance

### Negative

- listing and filtering become slower as run count increases
- atomic creation and update rules must be implemented carefully
- global queries are intentionally limited in the MVP

### Risks

- partial writes could leave an incomplete run directory after interruption
- external file modification can make a run internally inconsistent
- large run collections may make manifest scanning noticeably slow

## Validation

- artifact-store tests create a run with every required file in the documented layout
- `gauntlet runs list` discovers runs solely from valid manifests
- `gauntlet runs show` reads a selected run without an index
- interrupted or incomplete run fixtures are handled explicitly rather than reported as completed
- backup-and-restore testing confirms a copied run directory remains readable

## Revisit Trigger

Reconsider a derived SQLite index when measured manifest-scan latency is unacceptable for realistic local run counts, or when required filtering cannot be delivered safely by scanning. Reconsider the canonical storage model only if a future multi-user or high-concurrency product requirement is approved.
