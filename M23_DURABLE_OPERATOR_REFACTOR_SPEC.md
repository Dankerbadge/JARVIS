# M23: Durable Operator Refactor (Decision Trace + Learning Substrate)

## Goal

Convert JARVIS from a highly capable reactive runtime into a durable learning operator by separating:

- durable orchestration
- decision-trace capture
- memory summarization
- outcome-based learning
- globally ranked proactive suggestions
- long-horizon project development loops

The key objective is not adding more features. It is introducing a first-class judgment substrate that can be replayed, scored, and promoted.

## Source Anchors (Current Code)

Primary files and anchors this plan is mapped to:

- `jarvis/runtime.py`
- `jarvis/state_index.py`
- `jarvis/memory.py`
- `jarvis/adaptive_policy.py`
- `jarvis/cognition.py`
- `jarvis/skills/markets.py`

Runtime anchors:

- `jarvis/runtime.py:671` (`class JarvisRuntime`)
- `jarvis/runtime.py:3718` (`_build_presence_dialogue_context`)
- `jarvis/runtime.py:4333` (`generate_dialogue_turn`)
- `jarvis/runtime.py:5865` (`generate_presence_reply_body`)
- `jarvis/runtime.py:9748` (`run_cognition_cycle`)
- `jarvis/runtime.py:10360` (`record_market_handoff_outcome`)
- `jarvis/runtime.py:1571` (`trigger_self_patch_task`)
- `jarvis/runtime.py:2043` (`run_adaptive_calibration`)

State/memory/policy/cognition anchors:

- `jarvis/memory.py:19` (`class MemoryStore`)
- `jarvis/memory.py:87` (`append_event`)
- `jarvis/adaptive_policy.py:107` (`class AdaptivePolicyStore`)
- `jarvis/adaptive_policy.py:225` (`update_patch`)
- `jarvis/cognition.py:161` (`class CognitionEngine`)
- `jarvis/cognition.py:385` (`run_cycle`)
- `jarvis/state_index.py:6`..`jarvis/state_index.py:102` (`latest_*_key`)
- `jarvis/skills/markets.py:24` (`class MarketsSkill`)

## Architectural End State

```text
jarvis/
  runtime/
    __init__.py
    gateway.py
    orchestrator.py
    services.py
    workflows/
      __init__.py
      models.py
      plan_repository.py
      planner.py
      executor.py
      engine.py
  reasoning/
    __init__.py
    schema.py
    store.py
    tracer.py
    replayer.py
  suggestions/
    __init__.py
    models.py
    detectors.py
    ranker.py
    policy.py
    feedback.py
  learning/
    __init__.py
    features.py
    datasets.py
    ranker.py
    eval.py
    registry.py
  planning/
    __init__.py
    project_graph.py
    milestones.py
    next_action_ranker.py
    devloop.py
  improvement/
    __init__.py
    failure_mining.py
    fix_synthesis.py
    patch_evaluator.py
```

## Migration Strategy

### Phase 0: Safety Rails (No Behavior Change)

- Capture current behavior and metrics baselines.
- Keep `jarvis/runtime.py` as the public entrypoint while migration is in progress.
- Add compatibility adapters before moving execution paths.

Deliverables:

- `analysis/m23_baseline_runtime_contract.json`
- `analysis/m23_baseline_reply_contract.json`
- `analysis/m23_baseline_adaptive_policy.json`

Exit criteria:

- Existing tests pass unchanged.
- Runtime API surface remains stable.

### Phase 1: Split Runtime Composition and Routing

Problem addressed: runtime fusion.

Create:

- `jarvis/runtime/gateway.py` for inbound/outbound runtime IO surfaces.
- `jarvis/runtime/orchestrator.py` for service composition + domain routing.
- `jarvis/runtime/services.py` for typed service container/factory.

Move map:

- `JarvisRuntime.__init__` (`jarvis/runtime.py:672`) -> `runtime/services.py` + `runtime/orchestrator.py`
- `ingest_event`, `ingest_signal`, `ingest_envelope` (`jarvis/runtime.py:842`, `883`, `9714`) -> `runtime/gateway.py`
- `prepare_openclaw_reply`, `prepare_openclaw_voice_reply` (`jarvis/runtime.py:2795`, `6412`) -> `runtime/gateway.py`
- `run_taskflow_presence_cycle` (`jarvis/runtime.py:8356`) -> `runtime/orchestrator.py`

Compatibility:

- `jarvis/runtime.py` becomes a thin facade delegating to the new orchestrator.

### Phase 2: Durable Workflow Substrate

Problem addressed: in-process plan execution without durable step-state semantics.

Create:

- `jarvis/runtime/workflows/models.py` (`WorkflowRun`, `WorkflowStep`, `StepAttempt`, `Compensation`)
- `jarvis/runtime/workflows/engine.py`
- `jarvis/runtime/workflows/plan_repository.py`
- `jarvis/runtime/workflows/planner.py`
- `jarvis/runtime/workflows/executor.py`

Step state contract:

- `queued`
- `running`
- `blocked`
- `approved`
- `succeeded`
- `failed`
- `compensated`

Move map:

- `class PlanRepository` (`jarvis/runtime.py:85`) -> `runtime/workflows/plan_repository.py`
- `class Planner` (`jarvis/runtime.py:357`) -> `runtime/workflows/planner.py`
- `class Executor` (`jarvis/runtime.py:397`) -> `runtime/workflows/executor.py`
- `run`, `preflight_plan`, `execute_approved_step` (`jarvis/runtime.py:10429`, `10432`, `10475`) -> `runtime/workflows/engine.py`

Durability requirements:

- Step attempts are append-only.
- Activity input/output payloads are deterministic and replay-safe.
- Retried activities preserve idempotency keys.

### Phase 3: First-Class Reasoning Ledger

Problem addressed: memory events are not decision traces.

Create:

- `jarvis/reasoning/schema.py`
- `jarvis/reasoning/store.py`
- `jarvis/reasoning/tracer.py`
- `jarvis/reasoning/replayer.py`

Canonical entities:

- `decision_trace`
- `decision_step`
- `candidate_action`
- `selected_action`
- `outcome_label`
- `counterfactual_note`

Move map:

- Decision lifecycle capture currently scattered in `plan`, `run`, `run_cognition_cycle`, `trigger_self_patch_task` (`jarvis/runtime.py:9740`, `10429`, `9748`, `1571`) -> `reasoning/tracer.py` instrumentation hooks
- `ThoughtArtifact` context (`jarvis/cognition.py:63`) is referenced as input evidence, not used as the canonical decision ledger.

Rules:

- Memory keeps summaries of decision traces.
- Reasoning store remains the source of truth for replay and learning.

### Phase 4: Unified Suggestions Engine

Problem addressed: suggestion generation is fragmented by subsystem.

Create:

- `jarvis/suggestions/models.py`
- `jarvis/suggestions/detectors.py`
- `jarvis/suggestions/ranker.py`
- `jarvis/suggestions/policy.py`
- `jarvis/suggestions/feedback.py`

Candidate schema:

- `kind`
- `domain`
- `trigger`
- `why_now`
- `why_not_later`
- `cost`
- `confidence`
- `expected_value`
- `required_context`
- `approval_class`

Move map:

- Cognition interrupt candidate surfacing (`jarvis/cognition.py:385`) -> detector inputs only
- Markets suggestion-first candidate logic (`jarvis/skills/markets.py:24`) -> market detector plugin
- Runtime direct suggestion surfacing in presence methods (`jarvis/runtime.py:3718`, `5865`) -> ranker output consumption

### Phase 5: Outcome-Based Learning Pipeline

Problem addressed: adaptive policy is tuning knobs, not learning reusable decision patterns.

Create:

- `jarvis/learning/features.py`
- `jarvis/learning/datasets.py`
- `jarvis/learning/ranker.py`
- `jarvis/learning/eval.py`
- `jarvis/learning/registry.py`

Training row contract:

- task type
- domain
- risk state
- recent failures
- recent approvals
- momentum signal
- candidate action type
- user acceptance
- utility score
- regret/revert signal

Move map:

- Calibration metrics generation (`jarvis/runtime.py:2043`) -> feature extraction input
- Adaptive policy updates (`jarvis/adaptive_policy.py:225`) -> policy override sink, not primary learner

### Phase 6: Project Development Planner

Problem addressed: interrupt intelligence exists, long-horizon project development does not.

Create:

- `jarvis/planning/project_graph.py`
- `jarvis/planning/milestones.py`
- `jarvis/planning/next_action_ranker.py`
- `jarvis/planning/devloop.py`

Planner inputs:

- repo state
- PR state
- CI failures
- recurring bug signatures
- unresolved review themes
- missing tests
- architecture drift signals

Planner outputs:

- proposed task
- expected value
- proof required
- rollback path
- approval requirement

### Phase 7: Improvement Engine Above Self-Patch

Problem addressed: self-patch is a trigger-driven repair action, not a closed-loop architecture improvement system.

Create:

- `jarvis/improvement/failure_mining.py`
- `jarvis/improvement/fix_synthesis.py`
- `jarvis/improvement/patch_evaluator.py`

Move map:

- `_maybe_trigger_self_patch_from_calibration` and `trigger_self_patch_task` (`jarvis/runtime.py:1860`, `1571`) become execution plumbing invoked by improvement decisions.
- Improvement engine performs pattern mining + candidate synthesis before patch execution.

## State Layer Separation Rules

Keep existing state graph and state index for current truth:

- `state_graph` -> latest known status snapshots.
- `state_index.py` -> stable artifact key helpers.

Add new stores for lineage and learning:

- Reasoning store -> decision lineage (`how we got here`).
- Learning store -> reusable performance patterns (`what tends to work`).

Do not overload `memory.py` with decision-trace payload types.

## Backward Compatibility Plan

- Keep runtime API signatures stable during migration.
- Keep `MemoryStore` schema unchanged in early phases.
- Add mirrored writes during transition:
  - existing memory event append
  - new reasoning trace append
- Only switch read paths after parity checks pass on replay/sample sets.

## PR Stack (Recommended)

1. PR-1 Runtime split scaffolding (facade only, no logic move).
2. PR-2 Workflow package extraction (PlanRepository/Planner/Executor + engine).
3. PR-3 Reasoning ledger introduction + mirrored trace writes.
4. PR-4 Suggestion engine + detector migration for cognition/markets.
5. PR-5 Learning dataset + evaluator + adaptive-policy sink integration.
6. PR-6 Project planner + devloop proposals.
7. PR-7 Improvement engine feeding self-patch executor.

## Acceptance Gates

- Workflow durability:
  - mid-plan process restart resumes from last committed step attempt.
- Reasoning replay:
  - sampled traces can be replayed to reconstructed decision summaries.
- Learning lift:
  - offline eval shows improvement against baseline utility/regret metrics.
- Suggestion coherence:
  - one ranked list with suppression reasons across domains.
- Project development behavior:
  - planner emits actionable next steps with proof and rollback fields.

## First Implementation Slice (Recommended Next Action)

Start with a no-risk structural extraction:

- Introduce `jarvis/runtime/` package and keep `jarvis/runtime.py` facade.
- Move `PlanRepository`, `Planner`, and `Executor` into `jarvis/runtime/workflows/`.
- Add unit tests ensuring identical behavior for:
  - `plan()`
  - `run()`
  - `preflight_plan()`
  - `execute_approved_step()`

This yields immediate modularity gains while preserving behavior.
