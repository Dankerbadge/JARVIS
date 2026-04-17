# Milestone 9: Local Cognition Loop + Academics Domain

## Goal
Add the first bounded cognition layer and a second operational domain (Academics) so JARVIS
can reason across domains instead of operating as a Zenith-only engineering agent.

## Scope
- Persisted `ThoughtArtifact` generation on a bounded schedule.
- Interrupt decision policy with suppression-aware delivery/suppression records.
- Morning/evening synthesis artifact generation.
- Academics connectors + skill pack in the same shared runtime path.
- CLI/operator surfaces for cognition, synthesis, interrupts, and academics state.

## Runtime Additions
### Cognition
- `jarvis/cognition.py`
  - `Hypothesis`
  - `ThoughtArtifact`
  - `ThoughtStore`
  - `CognitionEngine.run_cycle(runtime)`
- Daemon invokes cognition each loop and stores output in summary under `cognition`.

### Interrupt Policy
- `jarvis/interrupts.py`
  - `InterruptCandidate`
  - `InterruptDecision`
  - `InterruptStore`
  - `InterruptPolicy`
- Interrupt outcomes are auditable (`delivered` vs `suppressed`) with reasons.

### Synthesis
- `jarvis/synthesis.py`
  - `MorningSynthesis`
  - `EveningSynthesis`
  - `SynthesisStore`
  - `SynthesisEngine`
- Daily synthesis can be generated automatically in cognition loop and manually via CLI.

### Academics domain
- `jarvis/skills/academics.py`
  - extraction from academic events into state/risk artifacts
  - bounded planning for study recommendations and review-gated draft actions
- `jarvis/connectors/academics.py`
  - JSON-feed connector emitting:
    - `academic.assignment_due`
    - `academic.exam_scheduled`
    - `academic.reading_assigned`
    - `academic.grade_update`
    - `academic.risk_signal`
    - `academic.study_window`

### Shared planner/extractor path
- Runtime now uses a combined extractor (`Zenith + Academics`).
- Planner chooses domain plans from shared risk state using trigger domains.

## New State Keys
- `latest_academic_overview:<term_id>`
- `latest_course_risk:<course_id>`
- `latest_deadline_cluster:<term_id>`
- `latest_study_recommendation:<course_id>`

## CLI Additions
- `thoughts recent`
- `thoughts show <thought_id>`
- `synthesis morning [--generate]`
- `synthesis evening [--generate]`
- `interrupts list`
- `interrupts acknowledge <interrupt_id>`
- `interrupts snooze <interrupt_id>`
- `academics overview`
- `academics risks`
- `run-once/watch` support `--academics-feed-path`

## Acceptance Signals
1. Daemon persists thought artifacts on bounded schedule.
2. Academics events flow through event ingestion → state graph → planning → outcomes.
3. Morning/evening synthesis artifacts are queryable.
4. Interrupt decisions are auditable and suppression-aware.
5. Combined suite passes with added M9 tests.
