#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <report_path> [--report-source <label>] [--output-path <path>] [--strict] [extra_cli_flags...]"
  exit 2
fi

REPORT_PATH="$1"
shift

REPORT_SOURCE="${JARVIS_IMPROVEMENT_EVIDENCE_BATCH_OUTPUTS_REPORT_SOURCE:-}"
OUTPUT_PATH="${JARVIS_IMPROVEMENT_EVIDENCE_BATCH_OUTPUTS_OUTPUT_PATH:-}"
STRICT_FLAG=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --report-source)
      if [[ $# -lt 2 ]]; then
        echo "error: --report-source requires a value"
        exit 2
      fi
      REPORT_SOURCE="$2"
      shift 2
      ;;
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
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

PYTHON_BIN="${JARVIS_IMPROVEMENT_PYTHON_BIN:-python3}"

CMD=(
  "$PYTHON_BIN" -m jarvis.cli improvement evidence-lookup-batch-outputs
  --report-path "$REPORT_PATH"
)

if [[ -n "$REPORT_SOURCE" ]]; then
  CMD+=(--report-source "$REPORT_SOURCE")
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
