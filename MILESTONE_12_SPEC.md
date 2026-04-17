# MILESTONE 12: OPERATOR SURFACE + COMPANION LAYER

## Goal

Make M11 cognition operationally usable as a persistent local companion by exposing a local-only operator API, an inspectable dashboard, interruption governance controls, and automatic digest/archive outputs.

## Scope

### 1. Local Operator API + Dashboard

- Add `jarvis/server.py` local HTTP surface (default `127.0.0.1:8765`)
- Provide read endpoints for:
  - operator home view (unified priorities)
  - thoughts
  - synthesis
  - interrupts
  - approvals (with evidence packet)
  - academics overview/risks/schedule/windows
  - preferences
  - digest exports
- Provide write endpoints for:
  - interrupt acknowledge
  - interrupt snooze
  - focus mode updates
  - quiet-hours updates
  - suppress-until updates
  - digest export trigger
- Serve a first local dashboard at `/` using the same API

### 2. Interruption Governance Controls

- Add persistent operator preference state via `jarvis/operator_state.py`
- Support:
  - focus mode (`academics`, `zenith`, `off`)
  - quiet hours
  - manual suppress-until
- Add preference event history for auditability
- Integrate preferences into cognition suppression windows

### 3. Async Digest Archive

- Add `jarvis/archive.py` daily digest export service
- Export Markdown, HTML, and JSON per day under `./.jarvis/archive/`
- Persist export index and list/show surfaces
- Trigger digest export from daemon `run_once()`

### 4. CLI Surface

- Add `serve` command
- Add interrupt governance commands:
  - `interrupts suppress-until`
  - `interrupts focus-mode`
  - `interrupts quiet-hours`
  - `interrupts preferences`
- Add archive commands:
  - `archive export`
  - `archive list`
  - `archive show`

## Safety Invariants

- No planner/executor boundary changes
- No approval-path weakening
- No direct irreversible action path from cognition or UI
- Local-only operator surface by default (`127.0.0.1`)

## Validation

- Existing suite remains green
- New tests added:
  - `tests/test_operator_state.py`
  - `tests/test_archive_digest.py`
  - `tests/test_operator_server.py`
- Full regression suite passes after M12 integration
