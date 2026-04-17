# MILESTONE 16: MARKET HANDOFF + CLOSED-LOOP EVALUATION

## Goal

Close the Markets feedback loop from suggestion -> external handoff -> outcome -> learning,
while preserving suggestion-first safety boundaries and no direct autonomous trade execution.

## Scope

### 1. Handoff outcome ingestion

- Add `jarvis/connectors/markets_outcomes.py`.
- Emit `market.handoff_outcome` events from read-only outcome receipts.
- Cursor contract: incremental `seen_ids` persistence.

### 2. Markets skill/state updates

- Extend `jarvis/skills/markets.py` to ingest:
  - `market.handoff_outcome`
- Persist artifacts:
  - `latest_market_handoff:<handoff_id>`
  - `latest_market_outcome:<handoff_id>`
- Add risk artifacts for adverse outcomes (`stopped`, `rejected`, `expired`).

### 3. Runtime learning loop

- Add outcome mapping in runtime/daemon:
  - `filled -> success`
  - `accepted -> partial`
  - `rejected -> failure`
  - `expired -> failure`
  - `stopped -> regression`
  - `skipped -> partial`
- Persist mapped outcomes through existing `plan_outcomes` store so cognition/correlation can
  consume market feedback alongside Zenith/Academics history.

### 4. Operator surfaces

- Extend Markets home/overview with:
  - handoffs
  - outcomes
  - aggregate evaluation summary by status
- CLI additions:
  - `markets handoffs`
  - `markets outcomes`
- API additions:
  - `GET /api/markets/handoffs`
  - `GET /api/markets/outcomes`

## Safety Invariants

- Direct market execution stays disabled (no `P3/P4` market actions).
- Handoff and outcome ingestion remains read-only and auditable.
- Planner/executor split and approval boundaries are unchanged.

## Validation

- New tests:
  - `tests/test_markets_outcomes_connector.py`
  - `tests/test_markets_closed_loop.py`
- Expanded tests:
  - `tests/test_operator_server.py`
- Full regression suite remains green.
