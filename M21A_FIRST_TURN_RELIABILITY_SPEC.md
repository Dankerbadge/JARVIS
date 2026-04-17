# M21A: First-Turn Reliability + Continuity Hardening

## Goal

Make the frozen daily stack feel alive on the first real turn without identity drift.

## Implemented

1. Fast social-turn path in `generate_presence_reply_body(...)`
- Social turns (`hello`, `hey`, `what's up`, etc.) bypass heavy dialogue-context assembly.
- Returns a guarded low-latency response immediately.

2. Status/priority low-latency path
- Added status-priority detection.
- For those turns, model path is skipped by default and dialogue retrieval is skipped (`skip_dialogue_retrieval=true`) to reduce cold-path latency.
- Heuristic output now returns a concrete status snapshot + next move framing.

3. Presence-model timeout hardening (`ollama_backend`)
- Presence-reply model timeout budgets are shortened for responsive turns.
- Removed the second JSON retry path that previously doubled wall time on model misses.
- On model-budget miss, fallback now happens immediately.

4. Orchestrator presence-ack contract
- Added `presence_ack` to prepared reply payload:
  - `text`
  - `target_ms`
  - `deferred`
  - `defer_reason`
- This supports explicit Phase-A rendering independent of full answer text.

5. M21 soak timeout semantics
- `run_m21_relationship_soak.py` now treats generation timeout as deferred response while preserving continuity.
- Added first-turn metrics to soak summary:
  - `first_turn_presence_ok`
  - `first_turn_elapsed_ms`
  - `first_turn_error`
  - `first_turn_response_deferred`

6. Launcher warmup
- `start_jarvis_daily_production.sh` now performs a post-healthy warmup request and retrieval probe to reduce cold-start misses.

## Validation Snapshot

- Probe (`hello`): ~37 ms, continuity preserved.
- Probe (priority status): ~8 ms, continuity preserved.
- Full M21 loop (16 turns):
  - artifact: `analysis/m21_relationship_soak_2026-04-13_11-52-06.json`
  - `continuity_failure_rate`: `0.0`
  - `first_turn_presence_ok`: `true`
  - `first_turn_elapsed_ms`: `16.984`
  - `mode_accuracy`: `0.9375`

## Next

- M21B multi-day real-use soak with relationship-quality scoring:
  - stateful relevance
  - recognizability
  - pushback quality
  - continuity recovery
