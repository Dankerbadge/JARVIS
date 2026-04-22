#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <template_path> [--output-path <path>] [--strict] [--allow-invalid-rows] [extra_cli_flags...]"
  exit 2
fi

TEMPLATE_PATH="$1"
shift

OUTPUT_PATH="${JARVIS_IMPROVEMENT_SEED_OUTPUT_PATH:-}"
STRICT_FLAG=""
ALLOW_INVALID_ROWS_FLAG=""
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
    --allow-invalid-rows)
      ALLOW_INVALID_ROWS_FLAG="--allow-invalid-rows"
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
  "$PYTHON_BIN" -m jarvis.cli improvement seed-hypotheses
  --template-path "$TEMPLATE_PATH"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

if [[ -n "$OUTPUT_PATH" ]]; then
  CMD+=(--output-path "$OUTPUT_PATH")
fi

if [[ -n "$STRICT_FLAG" ]]; then
  CMD+=("$STRICT_FLAG")
fi

if [[ -n "$ALLOW_INVALID_ROWS_FLAG" ]]; then
  CMD+=("$ALLOW_INVALID_ROWS_FLAG")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
