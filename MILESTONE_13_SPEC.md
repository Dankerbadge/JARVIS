# MILESTONE 13: PROVIDER-NATIVE ACADEMICS INTAKE

## Goal

Move Academics intake from file-path-fed inputs to ambient provider-native signals while keeping
the same governed runtime path (event -> state -> memory -> cognition -> plan/approval).

## Scope

### 1. Google Calendar connector (read-only)

- Add `jarvis/connectors/academics_google_calendar.py`
- Poll Google Calendar Events API with bearer auth
- Cursor contract:
  - `updated_cursor`
  - `seen_at_cursor` (tie-break IDs at same update timestamp)
- Emit normalized events in shared vocabulary:
  - `academic.exam_scheduled`
  - `academic.assignment_due`
  - `academic.study_window`
  - `academic.class_scheduled`

### 2. Gmail connector (read-only)

- Add `jarvis/connectors/academics_gmail.py`
- Poll Gmail Messages API + message details with bearer auth
- Cursor contract:
  - `history_id_cursor`
  - `seen_at_history` (tie-break IDs at same history id)
- Emit normalized events in shared vocabulary:
  - `academic.assignment_due`
  - `academic.exam_scheduled`
  - `academic.reading_assigned`
  - `academic.syllabus_item`
  - `academic.professor_message`
  - `academic.announcement`
  - `academic.grade_update`

### 3. Runtime source provenance

- Preserve source metadata in Academics artifacts:
  - `signal_source_kind`
  - `signal_provider`
- Add operator home summary:
  - `academics.signal_sources`

### 4. CLI wiring

Extend `run-once` and `watch` with:

- `--google-calendar-id`
- `--google-api-token`
- `--google-api-token-env`
- `--gmail-query`
- `--gmail-max-results`

Token resolution prefers explicit value then env var name.

## Safety Invariants

- Read-only provider intake; no provider write actions.
- No planner/executor or approval boundary changes.
- No direct irreversible actions from cognition or connector code.

## Validation

- New connector tests:
  - `tests/test_academics_google_calendar_connector.py`
  - `tests/test_academics_gmail_connector.py`
- New provenance/runtime test:
  - `tests/test_academics_provenance.py`
- Full regression suite remains green.

