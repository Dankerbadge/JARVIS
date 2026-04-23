#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 0 && "$1" == "--help" ]]; then
  echo "usage: $0 [--workspace-dir <path>] [--report-source <label>] [--strict|--no-strict] [extra_batch_flags...]"
  exit 0
fi

WORKSPACE_DIR="${JARVIS_IMPROVEMENT_EVIDENCE_LANE_SMOKE_WORKSPACE_DIR:-$(pwd)/output/ci/evidence_lane_smoke}"
REPORT_SOURCE="${JARVIS_IMPROVEMENT_EVIDENCE_LANE_SMOKE_REPORT_SOURCE:-evidence_lane_smoke}"
STRICT_MODE="${JARVIS_IMPROVEMENT_EVIDENCE_LANE_SMOKE_STRICT:-1}"
EXTRA_BATCH_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      if [[ $# -lt 2 ]]; then
        echo "error: --workspace-dir requires a path"
        exit 2
      fi
      WORKSPACE_DIR="$2"
      shift 2
      ;;
    --report-source)
      if [[ $# -lt 2 ]]; then
        echo "error: --report-source requires a value"
        exit 2
      fi
      REPORT_SOURCE="$2"
      shift 2
      ;;
    --strict)
      STRICT_MODE="1"
      shift
      ;;
    --no-strict)
      STRICT_MODE="0"
      shift
      ;;
    *)
      EXTRA_BATCH_ARGS+=("$1")
      shift
      ;;
  esac
done

REPO_PATH="${JARVIS_REPO_PATH:-$(pwd)}"
DB_PATH="${JARVIS_DB_PATH:-${WORKSPACE_DIR}/jarvis_smoke.db}"
PYTHON_BIN="${JARVIS_IMPROVEMENT_PYTHON_BIN:-python3}"

to_abs_path() {
  local raw="$1"
  if [[ "$raw" = /* ]]; then
    printf '%s' "$raw"
  else
    printf '%s' "$(pwd)/$raw"
  fi
}

WORKSPACE_DIR="$(to_abs_path "$WORKSPACE_DIR")"
mkdir -p "$WORKSPACE_DIR/fixtures"

FIXTURE_INPUT_PATH="${WORKSPACE_DIR}/fixtures/feedback.jsonl"
FIXTURE_CONFIG_PATH="${WORKSPACE_DIR}/fixtures/improvement_operator_smoke_config.json"
FIXTURE_REPORT_PATH="${WORKSPACE_DIR}/fixtures/operator_cycle_report.json"
BATCH_OUTPUT_PATH="${WORKSPACE_DIR}/evidence_lookup_batch_outputs.json"
LOOKUP_REPORT_PATH="${WORKSPACE_DIR}/evidence_lookup_report.json"
RUNTIME_ALERT_PATH="${WORKSPACE_DIR}/evidence_lookup_runtime_alert.json"
RUNTIME_HISTORY_PATH="${WORKSPACE_DIR}/evidence_lookup_runtime_history.jsonl"
LOOKUP_COMMAND_OUTPUT_PATH="${WORKSPACE_DIR}/evidence_lookup_command_output.json"
SMOKE_SUMMARY_PATH="${WORKSPACE_DIR}/evidence_lane_smoke_summary.json"

cat > "$FIXTURE_INPUT_PATH" <<'JSONL'
{"id":"rec-existing-1","title":"Known fixture record","text":"Synthetic fixture record used by evidence lane smoke tests."}
JSONL

cat > "$FIXTURE_CONFIG_PATH" <<JSON
{
  "defaults": {
    "domain": "fitness_apps",
    "source": "fixture_smoke"
  },
  "feedback_jobs": [
    {
      "name": "fixture_smoke_feedback",
      "domain": "fitness_apps",
      "source": "fixture_smoke",
      "input_path": "${FIXTURE_INPUT_PATH}",
      "input_format": "jsonl"
    }
  ]
}
JSON

cat > "$FIXTURE_REPORT_PATH" <<JSON
{
  "generated_at": "2026-04-23T00:00:00Z",
  "status": "warning",
  "config_path": "${FIXTURE_CONFIG_PATH}",
  "evidence_lookup_report_path": "${LOOKUP_REPORT_PATH}",
  "blockers": [
    {
      "hypothesis_id": "hyp-smoke-1",
      "seed_evidence_record_ids": ["rec-missing-1"]
    }
  ]
}
JSON

BATCH_CMD=(
  "${REPO_PATH}/scripts/run_improvement_evidence_lookup_batch_outputs.sh"
  "$FIXTURE_REPORT_PATH"
  --report-source "$REPORT_SOURCE"
  --output-path "$BATCH_OUTPUT_PATH"
  --json-compact
)
if [[ ${#EXTRA_BATCH_ARGS[@]} -gt 0 ]]; then
  BATCH_CMD+=("${EXTRA_BATCH_ARGS[@]}")
fi

BATCH_PAYLOAD="$("${BATCH_CMD[@]}")"
printf '%s\n' "$BATCH_PAYLOAD" > "${WORKSPACE_DIR}/batch_payload.json"

BATCH_READY="$(
  printf '%s' "$BATCH_PAYLOAD" | "$PYTHON_BIN" -c 'import json,sys; p=json.loads(sys.stdin.read() or "{}"); print(int(p.get("ready") or 0))'
)"
BATCH_RECORD_COUNT="$(
  printf '%s' "$BATCH_PAYLOAD" | "$PYTHON_BIN" -c 'import json,sys; p=json.loads(sys.stdin.read() or "{}"); print(int(p.get("record_count") or 0))'
)"
BATCH_COMMAND="$(
  printf '%s' "$BATCH_PAYLOAD" | "$PYTHON_BIN" -c 'import json,sys; p=json.loads(sys.stdin.read() or "{}"); print(str(p.get("command") or "none").strip() or "none")'
)"
BATCH_STATUS="$(
  printf '%s' "$BATCH_PAYLOAD" | "$PYTHON_BIN" -c 'import json,sys; p=json.loads(sys.stdin.read() or "{}"); print(str(p.get("status") or "unknown").strip() or "unknown")'
)"

LOOKUP_EXECUTED="0"
if [[ "$BATCH_READY" == "1" && "$BATCH_COMMAND" != "none" ]]; then
  bash -lc "$BATCH_COMMAND" > "$LOOKUP_COMMAND_OUTPUT_PATH"
  LOOKUP_EXECUTED="1"
fi

RUNTIME_PAYLOAD="$(
  JARVIS_REPO_PATH="$REPO_PATH" JARVIS_DB_PATH="$DB_PATH" \
    "${REPO_PATH}/scripts/run_improvement_evidence_lookup_runtime_alert.sh" \
      "$LOOKUP_REPORT_PATH" \
      --output-path "$RUNTIME_ALERT_PATH" \
      --rerun-command "$BATCH_COMMAND" \
      --history-path "$RUNTIME_HISTORY_PATH" \
      --history-window 3 \
      --json-compact
)"
printf '%s\n' "$RUNTIME_PAYLOAD" > "${WORKSPACE_DIR}/runtime_alert_payload.json"

MISSING_COUNT="$(
  printf '%s' "$RUNTIME_PAYLOAD" | "$PYTHON_BIN" -c 'import json,sys; p=json.loads(sys.stdin.read() or "{}"); print(int(p.get("evidence_lookup_missing_count") or p.get("missing_count") or 0))'
)"
ALERT_CREATED="$(
  printf '%s' "$RUNTIME_PAYLOAD" | "$PYTHON_BIN" -c 'import json,sys; p=json.loads(sys.stdin.read() or "{}"); print(int(p.get("evidence_lookup_runtime_alert_created") or p.get("alert_created") or 0))'
)"
INTERRUPT_ID="$(
  printf '%s' "$RUNTIME_PAYLOAD" | "$PYTHON_BIN" -c 'import json,sys; p=json.loads(sys.stdin.read() or "{}"); print(str(p.get("evidence_lookup_runtime_interrupt_id") or p.get("interrupt_id") or "none"))'
)"
FIRST_REPAIR_COMMAND="$(
  printf '%s' "$RUNTIME_PAYLOAD" | "$PYTHON_BIN" -c 'import json,sys; p=json.loads(sys.stdin.read() or "{}"); print(str(p.get("evidence_lookup_runtime_first_repair_command") or p.get("first_repair_command") or "none"))'
)"

SMOKE_PASS="0"
if [[ "$BATCH_STATUS" == "ok" && "$BATCH_READY" == "1" && "$BATCH_RECORD_COUNT" -ge 1 && "$LOOKUP_EXECUTED" == "1" && "$MISSING_COUNT" -ge 1 && "$ALERT_CREATED" == "1" ]]; then
  SMOKE_PASS="1"
fi

cat > "$SMOKE_SUMMARY_PATH" <<JSON
{
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "$([[ "$SMOKE_PASS" == "1" ]] && echo ok || echo warning)",
  "workspace_dir": "${WORKSPACE_DIR}",
  "strict_mode": $([[ "$STRICT_MODE" == "1" ]] && echo true || echo false),
  "batch_status": "${BATCH_STATUS}",
  "batch_ready": ${BATCH_READY},
  "batch_record_count": ${BATCH_RECORD_COUNT},
  "batch_command": "${BATCH_COMMAND}",
  "lookup_executed": $([[ "$LOOKUP_EXECUTED" == "1" ]] && echo true || echo false),
  "missing_count": ${MISSING_COUNT},
  "alert_created": ${ALERT_CREATED},
  "interrupt_id": "${INTERRUPT_ID}",
  "first_repair_command": "${FIRST_REPAIR_COMMAND}",
  "smoke_pass": $([[ "$SMOKE_PASS" == "1" ]] && echo true || echo false),
  "batch_output_path": "${BATCH_OUTPUT_PATH}",
  "lookup_report_path": "${LOOKUP_REPORT_PATH}",
  "runtime_alert_path": "${RUNTIME_ALERT_PATH}",
  "runtime_history_path": "${RUNTIME_HISTORY_PATH}",
  "lookup_command_output_path": "${LOOKUP_COMMAND_OUTPUT_PATH}"
}
JSON

cat "$SMOKE_SUMMARY_PATH"

if [[ "$STRICT_MODE" == "1" && "$SMOKE_PASS" != "1" ]]; then
  exit 2
fi
