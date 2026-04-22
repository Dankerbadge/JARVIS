#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <config_path> [--strict] [--allow-missing-inputs] [--output-path <path>] [extra_cli_flags...]"
  exit 2
fi

CONFIG_PATH="$1"
shift

STRICT_FLAG=""
ALLOW_MISSING_FLAG=""
OUTPUT_PATH="${JARVIS_IMPROVEMENT_DAILY_OUTPUT_PATH:-}"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict)
      STRICT_FLAG="--strict"
      shift
      ;;
    --allow-missing-inputs)
      ALLOW_MISSING_FLAG="--allow-missing-inputs"
      shift
      ;;
    --output-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --output-path requires a path"
        exit 2
      fi
      OUTPUT_PATH="$2"
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
  "$PYTHON_BIN" -m jarvis.cli improvement daily-pipeline
  --config-path "$CONFIG_PATH"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

if [[ -n "$STRICT_FLAG" ]]; then
  CMD+=("$STRICT_FLAG")
fi

if [[ -n "$ALLOW_MISSING_FLAG" ]]; then
  CMD+=("$ALLOW_MISSING_FLAG")
fi

if [[ -n "$OUTPUT_PATH" ]]; then
  CMD+=(--output-path "$OUTPUT_PATH")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
