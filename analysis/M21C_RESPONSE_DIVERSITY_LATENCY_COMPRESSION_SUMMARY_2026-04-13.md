# M21C Response Diversity + Latency Compression Summary (No Voice)

Date: 2026-04-13

## Scope implemented
- Added live brief cache in runtime (`~45s` TTL) with production-oriented briefs:
  - `now_brief`, `top_two_priorities`, `academics_brief`, `markets_brief`, `zenith_brief`, `risk_brief`, `identity_brief`.
- Added richer status-contract generation paths:
  - stateful status replies now include **live state + explicit tradeoff + concrete next move**.
  - priority turns now render an explicit priority pair response contract.
- Strengthened dialogue quality gates:
  - `status_contract_missing` for open-ended status turns without enough live-state grounding.
  - strategist contract checks for `tradeoff + why_now + bounded_move`.
  - pushback specificity checks for `specific risk + safer alternative`.
  - semantic anti-repeat gate over recent turns (`semantic_repeat_collapse`).
- Expanded inferred policy signals in orchestrator:
  - automatic inference for `explicit_directive`, `disputed`, `high_stakes`, `requires_pushback`, severity, and reasons.

## Soak instrumentation upgrades
Updated soak script now reports quality metrics in addition to continuity/latency:
- `reply_uniqueness_rate`
- `live_state_reference_rate`
- `tradeoff_presence_rate`
- `why_now_presence_rate`
- `pushback_specificity_score`

## Validation
- Regression suite:
  - `tests/test_dialogue_model_policy.py`
  - `tests/test_presence_orchestration.py`
  - `tests/test_model_backend_contract.py`
- Result: **14 passed**.

## Current soak artifact
- `analysis/m21_relationship_soak_2026-04-13_16-29-14.json`
- Summary:
  - `turn_count`: 20
  - `continuity_failure_rate`: 0.0
  - `mode_accuracy`: 0.95
  - `first_turn_presence_ok`: true
  - `reply_uniqueness_rate`: 0.60
  - `live_state_reference_rate`: 0.85
  - `tradeoff_presence_rate`: 0.70
  - `why_now_presence_rate`: 0.40
  - `pushback_specificity_score`: 0.60

## Remaining gap
- Cadence remains mixed: some fast turns are now sub-second, but deeper turns still frequently land in ~12-16s range on local model path.
- Diversity is improved but still below target for a consistently movie-grade partner feel.
