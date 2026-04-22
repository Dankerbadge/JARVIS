#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <matrix_path> <report_path> [--output-path <path>] [--strict] [--alert-domain <domain>] [--alert-urgency <float>] [--alert-confidence <float>] [--alert-max-items <int>] [extra_cli_flags...]"
  exit 2
fi

MATRIX_PATH="$1"
REPORT_PATH="$2"
shift 2

OUTPUT_PATH="${JARVIS_IMPROVEMENT_VERIFY_MATRIX_ALERT_OUTPUT_PATH:-}"
STRICT_FLAG=""
ALERT_DOMAIN="${JARVIS_IMPROVEMENT_VERIFY_MATRIX_ALERT_DOMAIN:-markets}"
ALERT_URGENCY="${JARVIS_IMPROVEMENT_VERIFY_MATRIX_ALERT_URGENCY:-}"
ALERT_CONFIDENCE="${JARVIS_IMPROVEMENT_VERIFY_MATRIX_ALERT_CONFIDENCE:-}"
ALERT_MAX_ITEMS="${JARVIS_IMPROVEMENT_VERIFY_MATRIX_ALERT_MAX_ITEMS:-3}"
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
    --strict)
      STRICT_FLAG="--strict"
      shift
      ;;
    --alert-domain)
      if [[ $# -lt 2 ]]; then
        echo "error: --alert-domain requires a value"
        exit 2
      fi
      ALERT_DOMAIN="$2"
      shift 2
      ;;
    --alert-urgency)
      if [[ $# -lt 2 ]]; then
        echo "error: --alert-urgency requires a value"
        exit 2
      fi
      ALERT_URGENCY="$2"
      shift 2
      ;;
    --alert-confidence)
      if [[ $# -lt 2 ]]; then
        echo "error: --alert-confidence requires a value"
        exit 2
      fi
      ALERT_CONFIDENCE="$2"
      shift 2
      ;;
    --alert-max-items)
      if [[ $# -lt 2 ]]; then
        echo "error: --alert-max-items requires a value"
        exit 2
      fi
      ALERT_MAX_ITEMS="$2"
      shift 2
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
  "$PYTHON_BIN" -m jarvis.cli improvement verify-matrix-alert
  --matrix-path "$MATRIX_PATH"
  --report-path "$REPORT_PATH"
  --alert-domain "$ALERT_DOMAIN"
  --alert-max-items "$ALERT_MAX_ITEMS"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

if [[ -n "$ALERT_URGENCY" ]]; then
  CMD+=(--alert-urgency "$ALERT_URGENCY")
fi

if [[ -n "$ALERT_CONFIDENCE" ]]; then
  CMD+=(--alert-confidence "$ALERT_CONFIDENCE")
fi

if [[ -n "$OUTPUT_PATH" ]]; then
  CMD+=(--output-path "$OUTPUT_PATH")
fi

if [[ -n "$STRICT_FLAG" ]]; then
  CMD+=("$STRICT_FLAG")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
