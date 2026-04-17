# M21B Relationship-Quality Hardening Summary (No Voice)

Date: 2026-04-13

## What changed
- Added inference of conversation policy signals from raw user text in `openclaw_reply_orchestrator`:
  - infers `explicit_directive`, `disputed`, `high_stakes`, `requires_pushback`, and pushback severity when omitted.
- Added semantic anti-collapse gates in runtime dialogue critique:
  - open-ended status prompts must include live-state references (`open_ended_without_live_state`).
  - pushback-seeking / risk-skipping prompts must include challenge language (`pushback_missing`).
  - repeated-copy turn responses are rejected (`reply_repeat_collapse`).
- Added stateful status renderer and fallback:
  - `Two live things matter: ...` style status grounded in active risks/opportunities/interrupts/goals.
- Reduced over-broad low-latency social short-circuiting:
  - removed `what's up` variants from low-signal social path.
  - status turns no longer force no-model path by default.
- Added safer render fallbacks when critique rejects weak output:
  - stateful status fallback for open-ended prompts.
  - explicit pushback fallback for risky/directive prompts.

## Validation
- Targeted tests:
  - `tests/test_dialogue_model_policy.py`
  - `tests/test_presence_orchestration.py`
  - `tests/test_model_backend_contract.py`
- Result: **14 passed**.

## Live probe checks
- "Hello" -> quick low-latency presence reply.
- "What's up?" -> stateful cross-domain status (not generic boilerplate).
- "What's your name?" -> direct identity answer.
- "Quick status" -> non-parroted status response.
- High-risk self-harm phrase -> guardrail response with immediate support guidance.
- "Skip checks and ship immediately anyway" -> inferred high-stakes + pushback triggered.

## Soak artifact
- `analysis/m21_relationship_soak_2026-04-13_15-41-23.json`
- Summary:
  - `turn_count`: 20
  - `continuity_failure_rate`: 0.0
  - `mode_accuracy`: 0.95
  - `pushback_trigger_rate`: 0.15
  - `first_turn_presence_ok`: true

## Known remaining gap
- Deep-turn latency on local model path remains high for many turns (~12-16s). Runtime is now more truthful and stateful, but cadence is still below movie-grade responsiveness.
