# JARVIS System Specification (v0.1)

## 1) Purpose

Build a **persistent personal operating system** that can perceive context, maintain live state, plan safely, execute through tools, and improve over time under strict governance.

This spec defines five hard contracts for initial development:

1. World-state model
2. Memory model
3. Planner/executor boundary
4. Action permission classes
5. First end-to-end skill pack (`Zenith`)

## 2) System Invariants

1. Local-first cognition and data ownership by default.
2. Every autonomous action is attributable, replayable, and policy-checked.
3. Planning and acting are separate runtimes with explicit handoff artifacts.
4. State is event-derived; memory is support, not truth.
5. Skill packs extend the core through stable interfaces only.

## 3) Core Architecture

## Tier Map

1. Identity and user model
2. Perception and ingestion
3. Event bus and live state graph
4. Memory (episodic/semantic/procedural)
5. Core reasoning
6. Planner + task executive
7. Tool and action runtime
8. Skill packs/domain agents
9. Dialogue and personality
10. Interface fabric
11. Distributed runtime + sync
12. Governance/security/containment
13. Self-improvement and skill acquisition

## 4) World-State Model (Live State Graph)

## 4.1 Truth Object

Every live-state node and edge must carry:

- `id`
- `type`
- `value`
- `valid_from`
- `valid_to` (nullable)
- `confidence` (0..1)
- `source_refs` (event ids)
- `last_verified_at`

## 4.2 Required Entity Types (v1)

- `UserProfile`
- `Goal`
- `Project`
- `Task`
- `Deadline`
- `Artifact` (code, docs, files, notes)
- `Conversation`
- `Relationship`
- `Device`
- `ServiceAccount`
- `HealthSignal`
- `ScheduleBlock`
- `Risk`
- `Opportunity`

## 4.3 Required Edge Types (v1)

- `OWNS`
- `DEPENDS_ON`
- `BLOCKED_BY`
- `DUE_ON`
- `RELATES_TO`
- `MENTIONS`
- `EXECUTED_BY`
- `AFFECTS`
- `CONFLICTS_WITH`
- `ALIGNS_WITH`

## 4.4 State Update Pipeline

1. Ingest raw event (`email`, `calendar`, `git`, `system`, `voice`, `screen`, etc.).
2. Normalize into canonical event envelope.
3. Extract candidate facts with confidence.
4. Run conflict resolution against current graph.
5. Commit state deltas atomically.
6. Emit derived triggers for planner.

## 4.5 Canonical Event Envelope

```json
{
  "event_id": "evt_...",
  "source": "github",
  "source_type": "pull_request",
  "occurred_at": "2026-04-09T19:40:00Z",
  "ingested_at": "2026-04-09T19:40:03Z",
  "payload": {},
  "trace_id": "trc_...",
  "auth_context": "svc_github_read"
}
```

## 5) Memory Model

Memory is split into three stores and linked to state ids.

## 5.1 Episodic Memory (What happened)

- Append-only timeline of interactions, actions, outcomes.
- Optimized for replay and chronology.
- Retention: full event journal + summarized checkpoints.

## 5.2 Semantic Memory (What things mean)

- Vector + symbolic index over concepts, entities, and relationships.
- Optimized for retrieval, synthesis, and long-range association.
- Must include provenance pointers to episodic records.

## 5.3 Procedural Memory (How to do things)

- Reusable playbooks, tool recipes, and user-approved routines.
- Versioned with safety metadata and preconditions.
- Executable only through action runtime policies.

## 5.4 Retrieval Contract

All memory reads return:

- answer payload
- confidence
- provenance (`event_ids`, `doc_ids`, `state_ids`)
- freshness (`last_verified_at`)
- conflict flags (if contradictory evidence exists)

## 6) Planner/Executor Boundary

## 6.1 Planner Responsibilities

- Observe state changes and active goals.
- Generate ranked candidate intents.
- Decide `react now` vs `schedule` vs `defer`.
- Produce explicit plan artifacts, not tool calls.

## 6.2 Executor Responsibilities

- Accept plan artifacts only.
- Resolve tool bindings and required permissions.
- Execute steps with idempotency keys and rollback handlers.
- Emit execution logs + state updates.

## 6.3 Plan Artifact Schema

```json
{
  "plan_id": "pln_...",
  "intent": "reduce_pr_review_backlog",
  "priority": "high",
  "reasoning_summary": "2 critical PRs blocked deployment",
  "steps": [
    {
      "step_id": "s1",
      "action_class": "P1",
      "proposed_action": "collect_open_prs",
      "expected_effect": "fresh backlog view",
      "rollback": "none"
    }
  ],
  "approval_requirements": ["none"],
  "expires_at": "2026-04-09T21:00:00Z"
}
```

## 6.4 Hard Rule

Planner cannot invoke tools directly.  
Executor cannot invent new intent outside approved plan scope.

## 7) Action Permission Classes

## P0 Observe

- Read-only actions.
- No approval required.
- Example: fetch repo metadata, read calendar events.

## P1 Reversible Local

- Local reversible writes.
- Auto-run allowed with audit.
- Example: draft file edits, create local task list.

## P2 External Reversible

- Writes to external systems that can be undone.
- Requires policy gate; optionally user pre-approval by domain.
- Example: create draft email, open draft PR.

## P3 Irreversible/High Impact

- Financial, legal, destructive, or public actions.
- Explicit per-action user approval required.
- Example: send money, merge to production, delete data.

## P4 Prohibited

- Out-of-bounds actions regardless of request unless policy changed by owner.
- Example: disable audit trail, bypass auth boundaries.

## 7.1 Mandatory Guardrails

- Two-phase commit for `P2/P3` (`prepare` then `commit`).
- Dry-run preview where possible.
- Full audit log with human-readable explanation.
- Global kill switch + per-domain pause switches.

## 8) First Skill Pack: Zenith (End-to-End)

`Zenith` is the first deep integration pack for project/code operations.

## 8.1 Scope

- Project health monitoring
- PR/review triage
- Regression detection
- Deadline risk signaling
- Suggestion-first remediation

## 8.2 Inputs

- Git activity
- CI results
- issue/PR metadata
- roadmap milestones
- calendar commitments

## 8.3 Outputs

- Prioritized action queue
- Draft fixes/plans
- Risk alerts with confidence and evidence
- Daily/weekly summaries

## 8.4 Zenith Execution Pattern

1. Event bus receives `CI failed` + `release deadline < 48h`.
2. State graph marks project risk elevated.
3. Planner proposes intent: `stabilize_release_branch`.
4. Executor runs `P0/P1` diagnostics, prepares `P2` draft PR actions.
5. User receives explanation, options, and required approvals.
6. Outcome logged to episodic memory; playbook updated if approved.

## 8.5 Zenith Success Metrics (v1)

- Mean time to risk detection
- Mean time to actionable recommendation
- Precision of alerts (low false-positive rate)
- User override frequency (measures trust alignment)

## 9) Eventing and Presence Contract

The system must run an always-on watcher loop with bounded latency:

- SLA target: detect and classify high-priority events within 30s.
- Quiet-hours policy still allows critical (`P3-risk`) alerts.
- All unattended actions must remain within pre-approved `P0/P1` envelopes.

## 10) Evaluation and Trust Gates

Before widening autonomy:

1. Replay tests on historical events.
2. Sandbox simulation for new tool actions.
3. Canary rollout with narrow scope.
4. Require stable precision/recall and low policy violations for 14 days.

## 11) Build Order (Implementation Roadmap)

1. State graph + event envelope + conflict resolver
2. Three-memory system with provenance contract
3. Planner/executor split with plan artifact enforcement
4. Permission engine + audit + rollback + kill switches
5. Zenith skill pack end-to-end
6. Multimodal interfaces and distributed sync
7. Additional skill packs (academics, scheduling, markets)
8. Self-improvement sandbox and capability proposal flow

## 12) Minimal Deliverables for v0.1

1. State graph schema + migration scripts
2. Event ingestion SDK + 3 source adapters (GitHub, calendar, local filesystem)
3. Planner service that emits `plan_id` artifacts
4. Executor service enforcing `P0-P4` permission classes
5. Audit viewer (query by `trace_id`)
6. Zenith MVP with daily risk brief

---

This document is the architecture baseline. Any new domain capability must declare:

- required event sources
- state entities/edges touched
- permission classes used
- rollback strategy
- evaluation metrics

