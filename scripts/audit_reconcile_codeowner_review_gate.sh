#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
usage: audit_reconcile_codeowner_review_gate.sh [--repo-slug <owner/repo>] [--branch <name>] [--min-collaborators <int>] [--required-status-check <context>]... [--required-status-check-strict <true|false|preserve>] [--reconcile-workflow-file <path-or-id>] [--recent-runs-limit <int>] [--expected-trigger-event <event>] [--event-audit-since <ISO8601>] [--source-workflow-run-id <id>] [--source-workflow-run-conclusion <value>] [--source-workflow-name <name>] [--source-workflow-event <event>] [--source-workflow-run-url <url>]

Runs the standard reconcile dry-run and augments it with a recent-run audit for
reconcile workflow trigger events.

Defaults:
- repo slug: inferred by reconcile script from origin remote
- branch: main
- min collaborators: 2
- required status checks: unchanged unless --required-status-check is provided
- required status-check strict mode: preserve
- reconcile workflow file: reconcile-codeowner-review-gate.yml
- recent runs limit: 20
- expected trigger event: workflow_run
- event-audit-since: unset (all recent runs are audited)
USAGE
}

REPO_SLUG=""
BRANCH="main"
MIN_COLLABORATORS=2
REQUIRED_STATUS_CHECKS=()
REQUIRED_STATUS_CHECK_STRICT_MODE="preserve"
RECONCILE_WORKFLOW_FILE="reconcile-codeowner-review-gate.yml"
RECENT_RUNS_LIMIT=20
EXPECTED_TRIGGER_EVENT="workflow_run"
EVENT_AUDIT_SINCE=""
SOURCE_WORKFLOW_RUN_ID=""
SOURCE_WORKFLOW_RUN_CONCLUSION=""
SOURCE_WORKFLOW_NAME=""
SOURCE_WORKFLOW_EVENT=""
SOURCE_WORKFLOW_RUN_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-slug)
      if [[ $# -lt 2 ]]; then
        echo "error: --repo-slug requires a value"
        exit 2
      fi
      REPO_SLUG="$2"
      shift 2
      ;;
    --branch)
      if [[ $# -lt 2 ]]; then
        echo "error: --branch requires a value"
        exit 2
      fi
      BRANCH="$2"
      shift 2
      ;;
    --min-collaborators)
      if [[ $# -lt 2 ]]; then
        echo "error: --min-collaborators requires a value"
        exit 2
      fi
      MIN_COLLABORATORS="$2"
      shift 2
      ;;
    --required-status-check)
      if [[ $# -lt 2 ]]; then
        echo "error: --required-status-check requires a value"
        exit 2
      fi
      REQUIRED_STATUS_CHECKS+=("$2")
      shift 2
      ;;
    --required-status-check-strict)
      if [[ $# -lt 2 ]]; then
        echo "error: --required-status-check-strict requires a value"
        exit 2
      fi
      REQUIRED_STATUS_CHECK_STRICT_MODE="$2"
      shift 2
      ;;
    --reconcile-workflow-file)
      if [[ $# -lt 2 ]]; then
        echo "error: --reconcile-workflow-file requires a value"
        exit 2
      fi
      RECONCILE_WORKFLOW_FILE="$2"
      shift 2
      ;;
    --recent-runs-limit)
      if [[ $# -lt 2 ]]; then
        echo "error: --recent-runs-limit requires a value"
        exit 2
      fi
      RECENT_RUNS_LIMIT="$2"
      shift 2
      ;;
    --expected-trigger-event)
      if [[ $# -lt 2 ]]; then
        echo "error: --expected-trigger-event requires a value"
        exit 2
      fi
      EXPECTED_TRIGGER_EVENT="$2"
      shift 2
      ;;
    --event-audit-since)
      if [[ $# -lt 2 ]]; then
        echo "error: --event-audit-since requires a value"
        exit 2
      fi
      EVENT_AUDIT_SINCE="$2"
      shift 2
      ;;
    --source-workflow-run-id)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-run-id requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_RUN_ID="$2"
      shift 2
      ;;
    --source-workflow-run-conclusion)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-run-conclusion requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_RUN_CONCLUSION="$2"
      shift 2
      ;;
    --source-workflow-name)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-name requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_NAME="$2"
      shift 2
      ;;
    --source-workflow-event)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-event requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_EVENT="$2"
      shift 2
      ;;
    --source-workflow-run-url)
      if [[ $# -lt 2 ]]; then
        echo "error: --source-workflow-run-url requires a value"
        exit 2
      fi
      SOURCE_WORKFLOW_RUN_URL="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1"
      usage
      exit 2
      ;;
  esac
done

if [[ "$RECENT_RUNS_LIMIT" =~ ^[0-9]+$ ]]; then
  if (( RECENT_RUNS_LIMIT < 1 )); then
    echo "error: --recent-runs-limit must be >= 1"
    exit 2
  fi
else
  echo "error: --recent-runs-limit must be an integer"
  exit 2
fi

RECONCILE_CMD=(
  bash ./scripts/reconcile_codeowner_review_gate.sh
  --branch "$BRANCH"
  --min-collaborators "$MIN_COLLABORATORS"
  --required-status-check-strict "$REQUIRED_STATUS_CHECK_STRICT_MODE"
  --source-workflow-run-id "$SOURCE_WORKFLOW_RUN_ID"
  --source-workflow-run-conclusion "$SOURCE_WORKFLOW_RUN_CONCLUSION"
  --source-workflow-name "$SOURCE_WORKFLOW_NAME"
  --source-workflow-event "$SOURCE_WORKFLOW_EVENT"
  --source-workflow-run-url "$SOURCE_WORKFLOW_RUN_URL"
)

if [[ -n "$REPO_SLUG" ]]; then
  RECONCILE_CMD+=(--repo-slug "$REPO_SLUG")
fi

for context in "${REQUIRED_STATUS_CHECKS[@]}"; do
  RECONCILE_CMD+=(--required-status-check "$context")
done

RECONCILE_REPORT_JSON="$("${RECONCILE_CMD[@]}")"

if [[ -z "$REPO_SLUG" ]]; then
  REPO_SLUG="$(
    python3 - <<'PY' "$RECONCILE_REPORT_JSON"
import json
import sys
payload = json.loads(sys.argv[1] or "{}")
print(str(payload.get("repo_slug") or "").strip())
PY
  )"
fi

if [[ -z "$REPO_SLUG" ]]; then
  echo "error: unable to resolve repo slug from reconcile report"
  exit 2
fi

WORKFLOW_RUNS_JSON="$(
  gh api "repos/${REPO_SLUG}/actions/workflows/${RECONCILE_WORKFLOW_FILE}/runs?per_page=${RECENT_RUNS_LIMIT}&branch=${BRANCH}"
)"

python3 - <<'PY' "$RECONCILE_REPORT_JSON" "$WORKFLOW_RUNS_JSON" "$EXPECTED_TRIGGER_EVENT" "$RECENT_RUNS_LIMIT" "$RECONCILE_WORKFLOW_FILE" "$EVENT_AUDIT_SINCE"
import json
import sys
from typing import Any
from datetime import datetime, timezone

report = json.loads(sys.argv[1] or "{}")
runs_payload = json.loads(sys.argv[2] or "{}")
expected_event = str(sys.argv[3] or "workflow_run").strip() or "workflow_run"
recent_runs_limit = max(1, int(str(sys.argv[4] or "20").strip() or "20"))
workflow_file = str(sys.argv[5] or "reconcile-codeowner-review-gate.yml").strip() or "reconcile-codeowner-review-gate.yml"
event_audit_since_raw = str(sys.argv[6] or "").strip()


def _parse_iso8601(value: str) -> datetime | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


event_audit_since: datetime | None = None
if event_audit_since_raw:
    try:
        event_audit_since = _parse_iso8601(event_audit_since_raw)
    except Exception:
        raise SystemExit(f"invalid_event_audit_since:{event_audit_since_raw}")

if not isinstance(report, dict):
    report = {}
if not isinstance(runs_payload, dict):
    runs_payload = {}

raw_runs = runs_payload.get("workflow_runs") or []
if not isinstance(raw_runs, list):
    raw_runs = []

recent_runs: list[dict[str, Any]] = []
recent_runs_with_scope: list[dict[str, Any]] = []
for item in raw_runs[:recent_runs_limit]:
    if not isinstance(item, dict):
        continue
    run_id_raw = item.get("id")
    run_number_raw = item.get("run_number")
    try:
        run_id = int(run_id_raw)
    except Exception:
        run_id = 0
    try:
        run_number = int(run_number_raw)
    except Exception:
        run_number = 0
    event = str(item.get("event") or "").strip() or "unknown"
    normalized = {
        "run_id": run_id,
        "run_number": run_number,
        "event": event,
        "status": str(item.get("status") or "").strip() or "unknown",
        "conclusion": str(item.get("conclusion") or "").strip() or "none",
        "html_url": str(item.get("html_url") or "").strip() or None,
        "created_at": str(item.get("created_at") or "").strip() or None,
        "updated_at": str(item.get("updated_at") or "").strip() or None,
        "head_branch": str(item.get("head_branch") or "").strip() or None,
        "name": str(item.get("name") or "").strip() or None,
    }
    recent_runs.append(normalized)
    run_created_at_raw = str(normalized.get("created_at") or "").strip()
    run_created_at_dt = None
    if run_created_at_raw:
        try:
            run_created_at_dt = _parse_iso8601(run_created_at_raw)
        except Exception:
            run_created_at_dt = None
    in_event_audit_scope = True
    if event_audit_since is not None and run_created_at_dt is not None:
        in_event_audit_scope = run_created_at_dt >= event_audit_since
    elif event_audit_since is not None and run_created_at_dt is None:
        in_event_audit_scope = True
    row_with_scope = dict(normalized)
    row_with_scope["event_audit_in_scope"] = bool(in_event_audit_scope)
    recent_runs_with_scope.append(row_with_scope)

non_expected_runs = [
    item
    for item in recent_runs_with_scope
    if str(item.get("event") or "") != expected_event and bool(item.get("event_audit_in_scope"))
]
legacy_non_expected_runs = [
    item
    for item in recent_runs_with_scope
    if str(item.get("event") or "") != expected_event and not bool(item.get("event_audit_in_scope"))
]
non_expected_events = sorted({str(item.get("event") or "").strip() for item in non_expected_runs if str(item.get("event") or "").strip()})
non_expected_run_ids = [
    str(int(item.get("run_id") or 0))
    for item in non_expected_runs
    if int(item.get("run_id") or 0) > 0
]
non_expected_events_csv = ",".join(non_expected_events) if non_expected_events else "none"
non_expected_run_ids_csv = ",".join(non_expected_run_ids) if non_expected_run_ids else "none"

event_change_needed = bool(non_expected_runs)
base_change_needed = bool(report.get("change_needed"))
combined_change_needed = bool(base_change_needed or event_change_needed)

report["reconcile_trigger_expected_event"] = expected_event
report["reconcile_trigger_recent_runs_limit"] = int(recent_runs_limit)
report["reconcile_trigger_event_audit_since"] = event_audit_since_raw or None
report["reconcile_trigger_recent_runs"] = recent_runs
report["reconcile_trigger_recent_runs_with_scope"] = recent_runs_with_scope
report["reconcile_trigger_recent_run_count"] = int(len(recent_runs))
report["reconcile_trigger_non_workflow_run_count"] = int(len(non_expected_runs))
report["reconcile_trigger_non_workflow_events"] = non_expected_events
report["reconcile_trigger_non_workflow_events_csv"] = non_expected_events_csv
report["reconcile_trigger_non_workflow_run_ids"] = non_expected_run_ids
report["reconcile_trigger_non_workflow_run_ids_csv"] = non_expected_run_ids_csv
report["reconcile_trigger_non_workflow_runs"] = non_expected_runs
report["reconcile_trigger_legacy_non_workflow_run_count"] = int(len(legacy_non_expected_runs))
report["reconcile_trigger_legacy_non_workflow_runs"] = legacy_non_expected_runs
report["reconcile_trigger_event_change_needed"] = bool(event_change_needed)
report["reconcile_trigger_event_change_reason"] = (
    "non_workflow_run_event_detected" if event_change_needed else "none"
)
report["reconcile_trigger_audit_workflow_file"] = workflow_file
report["change_needed"] = bool(combined_change_needed)

print(json.dumps(report, indent=2))
PY
