# MILESTONE 14: IDENTITY + PERSONAL CONTEXT MODEL

## Goal

Add a persistent user model and personal-context signals so cross-domain cognition is weighted by
explicit priorities and current human constraints rather than generic rules.

## Scope

### 1. Identity model persistence

- Add `jarvis/identity_state.py` with:
  - goal hierarchy storage
  - domain-weight storage
  - routines/constraints baseline fields
  - identity event audit log

### 2. Personal context persistence

- Store stress/energy/sleep/focus/mode/note snapshots
- Add update path with audit events
- Add connector:
  - `jarvis/connectors/personal_context.py`
  - file-change cursor polling for local context snapshots

### 3. Shared runtime integration

- Add `jarvis/skills/identity.py`
- Emit state artifacts:
  - `latest_user_model:default`
  - `latest_personal_context:default`
- Extend runtime with identity/context APIs and operator-home exposure

### 4. Cognition + interrupt policy weighting

- Thought artifacts include:
  - `user_model_snapshot`
  - `personal_context_snapshot`
- Hypothesis expected-value/confidence weighting now accounts for:
  - domain goal hierarchy weights
  - stress/energy/sleep/focus context
  - focus-mode penalties outside active domain
- Interrupt policy supports:
  - goal-domain threshold shift
  - stress-aware suppression for non-critical zenith interruptions

### 5. CLI + operator surface

- New CLI command group:
  - `identity show`
  - `identity set-domain-weight`
  - `identity set-goal`
  - `identity update-context`
- Server API additions:
  - `GET /api/identity`
  - `POST /api/identity/domain-weight`
  - `POST /api/identity/context`

## Safety Invariants

- No approval-path weakening
- No direct irreversible actions from cognition changes
- Identity/context updates are `P0` metadata updates with audit trail

## Validation

- New tests:
  - `tests/test_identity_state.py`
  - `tests/test_personal_context_connector.py`
  - `tests/test_cognition_identity_weighting.py`
- Full regression suite must remain green.

