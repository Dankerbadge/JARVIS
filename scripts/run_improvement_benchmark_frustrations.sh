#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <report_path> [--top-limit <n>] [--output-path <path>] [--strict] [extra_cli_flags...]"
  exit 2
fi

REPORT_PATH="$1"
shift

TOP_LIMIT="${JARVIS_IMPROVEMENT_BENCHMARK_TOP_LIMIT:-}"
OUTPUT_PATH="${JARVIS_IMPROVEMENT_BENCHMARK_OUTPUT_PATH:-}"
STRICT_FLAG=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --top-limit)
      if [[ $# -lt 2 ]]; then
        echo "error: --top-limit requires a number"
        exit 2
      fi
      TOP_LIMIT="$2"
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
  "$PYTHON_BIN" -m jarvis.cli improvement benchmark-frustrations
  --report-path "$REPORT_PATH"
)

if [[ -n "$TOP_LIMIT" ]]; then
  CMD+=(--top-limit "$TOP_LIMIT")
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
