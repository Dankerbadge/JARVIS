# MILESTONE 15: MARKETS DOMAIN #3 (SUGGESTION-FIRST)

## Goal

Add Markets as a first-class domain in the shared JARVIS cognition runtime while preserving
strict suggestion-first boundaries (no direct autonomous trade execution).

## Scope

### 1. Read-only market ingestion

- Add connectors:
  - `jarvis/connectors/markets_signals.py`
  - `jarvis/connectors/markets_positions.py`
  - `jarvis/connectors/markets_calendar.py`
- Emit market events:
  - `market.signal_detected`
  - `market.position_snapshot`
  - `market.risk_regime_changed`
  - `market.event_upcoming`
  - `market.opportunity_expired`
- Persist connector cursors for incremental polling.

### 2. Markets skill pack (recommendation-only)

- Add `jarvis/skills/markets.py`.
- Ingested market events create:
  - `MarketOpportunityArtifact`
  - `MarketAbstentionArtifact`
  - `MarketRiskPostureArtifact`
  - market-domain risks for high-confidence/low-downside opportunities and exposure/regime shifts.
- Plan generation remains bounded to:
  - `P0` context collection
  - `P1` suggestion brief
  - optional `P2` handoff packet (approval gated)
- No `P3`/`P4` market execution actions.

### 3. Shared runtime + operator integration

- Wire Markets into:
  - planner domain selection
  - tool runtime registration
  - operator home payload
- Add market state-index helpers:
  - `latest_market_risk_posture:<account_id>`
  - `latest_market_opportunity:<signal_id>`
  - `latest_market_abstention:<signal_id>`
  - `latest_market_event:<event_id>`
- Expose CLI surfaces:
  - `markets overview`
  - `markets opportunities`
  - `markets abstentions`
  - `markets posture`
- Add API surfaces:
  - `GET /api/markets/overview`
  - `GET /api/markets/opportunities`
  - `GET /api/markets/abstentions`
  - `GET /api/markets/posture`

### 4. Tri-domain interruption behavior

- Extend interruption policy with market-specific suppression:
  - low-confidence market signals do not interrupt under academic pressure/focus lock
  - market threshold increases under stress and during non-market focus windows
  - high-confidence, high-urgency market opportunities may still deliver when no higher-priority lock is active

## Safety Invariants

- Direct market execution remains disabled.
- Planner/executor approval boundaries are unchanged.
- Markets proposals are auditable and provenance-tagged.

## Validation

- New tests:
  - `tests/test_markets_connectors.py`
  - `tests/test_markets_runtime.py`
  - expanded `tests/test_interrupt_policy_cross_domain.py`
  - expanded `tests/test_operator_server.py`
- Full regression suite must remain green.
