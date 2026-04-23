#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <report_path> [--output-path <path>] [--rerun-command <cmd>] [--strict] [extra_cli_flags...]"
  exit 2
fi

REPORT_PATH="$1"
shift

OUTPUT_PATH="${JARVIS_IMPROVEMENT_CODEOWNER_RECONCILE_RUNTIME_ALERT_OUTPUT_PATH:-}"
RERUN_COMMAND="${JARVIS_IMPROVEMENT_CODEOWNER_RECONCILE_RUNTIME_ALERT_RERUN_COMMAND:-}"
STRICT_FLAG=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --output-path requires a path"
        exit 2
      fi
      OUTPUT_PATH="$2"
      shift 2
      ;;
    --rerun-command)
      if [[ $# -lt 2 ]]; then
        echo "error: --rerun-command requires a command string"
        exit 2
      fi
      RERUN_COMMAND="$2"
      shift 2
      ;;
    --strict)
      STRICT_FLAG="--strict"
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

REPO_PATH="${JARVIS_REPO_PATH:-$(pwd)}"
DB_PATH="${JARVIS_DB_PATH:-${REPO_PATH}/.jarvis/jarvis.db}"
PYTHON_BIN="${JARVIS_IMPROVEMENT_PYTHON_BIN:-python3}"

CMD=(
  "$PYTHON_BIN" -m jarvis.cli improvement reconcile-codeowner-review-gate-runtime-alert
  --report-path "$REPORT_PATH"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

if [[ -n "$OUTPUT_PATH" ]]; then
  CMD+=(--output-path "$OUTPUT_PATH")
fi

if [[ -n "$RERUN_COMMAND" ]]; then
  CMD+=(--rerun-command "$RERUN_COMMAND")
fi

if [[ -n "$STRICT_FLAG" ]]; then
  CMD+=("$STRICT_FLAG")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
