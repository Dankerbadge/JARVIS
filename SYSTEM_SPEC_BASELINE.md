# JARVIS Baseline System Spec (v0.1)

This baseline codifies five non-negotiable contracts.

## 1) World-State Model

- SQLite-backed live state graph
- canonical event envelopes
- truth object metadata on all state assertions:
  - `id`
  - `type`
  - `value`
  - `valid_from`
  - `valid_to`
  - `confidence`
  - `source_refs`
  - `last_verified_at`

## 2) Memory Model

Three stores with provenance enforcement:

- episodic memory: timeline of events/outcomes
- semantic memory: retrievable meaning
- procedural memory: reusable safe procedures

All reads expose:

- answer payload
- confidence
- provenance
- freshness
- conflict flags

## 3) Planner/Executor Boundary

- planner emits persisted `PlanArtifact` objects
- executor runs only persisted plan steps
- planner cannot invoke tools directly
- executor cannot invent out-of-scope intent

## 4) Permission Classes

- `P0` observe
- `P1` reversible local
- `P2` external reversible
- `P3` irreversible/high impact
- `P4` prohibited

Guardrails:

- approval workflow
- audit log
- rollback markers
- global kill switch

## 5) First Skill Pack: Zenith

Scope:

- project risk monitoring
- CI/deadline signal handling
- patch proposal previews
- protected UI changes gated behind approval

Zenith is wired to a real repository path and uses repo-aware diffs.

