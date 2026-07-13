# ADR-005: User-Global Artifact Root with Explicit Override Precedence

**Status:** Accepted  
**Date:** 2026-07-13

## Context

GAUNTLET run evidence can be large, contains execution details, and must not silently pollute or be committed with the evaluated project. The CLI also needs one predictable default location while allowing CI and individual projects to redirect artifacts.

This resolves the output-location conflict using 09_SECURITY_AND_THREAT_MODEL.md and the architect decision in reviews/M1.md at e59adf0.

## Decision Drivers

- local-first, service-free persistence
- evidence kept outside evaluated source trees by default
- deterministic configuration precedence
- CI and multi-project portability
- compatibility with the files-only persistence decision

## Options Considered

### Option A: User-global default with layered overrides

Advantages:

- keeps generated evidence outside projects by default
- provides a predictable cross-project location
- supports explicit CLI, environment, project, and profile customization

Disadvantages:

- users must look outside the project for artifacts
- shared storage requires run metadata to retain project identity

### Option B: Project-local artifact directory by default

Advantages:

- artifacts are visible beside project configuration
- moving the project can move its evidence with it

Disadvantages:

- generated evidence can pollute source control and project tooling
- each project needs ignore rules and storage management

## Decision

The package default artifact root is ~/.gauntlet/artifacts, never a directory inside the evaluated project. The effective root is resolved in this order, from highest to lowest precedence: CLI option, GAUNTLET_ARTIFACT_ROOT, project artifacts.root, profile default, then package default. Every run manifest retains project_id so runs remain attributable in shared storage.

## Consequences

### Positive

- project working trees stay free of run evidence by default
- users and CI can redirect storage without editing project files
- one precedence order applies consistently to artifact operations

### Negative

- users may need CLI commands to locate evidence
- shared roots need globally collision-resistant run IDs

### Risks

- inconsistent precedence implementations could split one run across roots
- user-global storage may grow without retention tooling

## Validation

- configuration tests prove all five precedence layers for artifacts.root
- CLI tests prove default, environment, and explicit --artifact-root behavior
- artifact-store tests prove manifests contain project_id

## Revisit Trigger

Reconsider the default only if measured user workflows require project-local ownership, or if an approved remote artifact service replaces local files as the primary store.
