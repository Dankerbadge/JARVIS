#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <config_path> [--output-dir <path>] [--strict] [--no-allow-missing-feeds] [--no-allow-missing-inputs] [--no-allow-missing-retests] [extra_cli_flags...]"
  exit 2
fi

CONFIG_PATH="$1"
shift

OUTPUT_DIR="${JARVIS_IMPROVEMENT_OPERATOR_OUTPUT_DIR:-}"
STRICT_FLAG=""
ALLOW_MISSING_FEEDS_FLAG=""
ALLOW_MISSING_INPUTS_FLAG=""
ALLOW_MISSING_RETESTS_FLAG=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      if [[ $# -lt 2 ]]; then
        echo "error: --output-dir requires a path"
        exit 2
      fi
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --strict)
      STRICT_FLAG="--strict"
      shift
      ;;
    --no-allow-missing-feeds)
      ALLOW_MISSING_FEEDS_FLAG="--no-allow-missing-feeds"
      shift
      ;;
    --no-allow-missing-inputs)
      ALLOW_MISSING_INPUTS_FLAG="--no-allow-missing-inputs"
      shift
      ;;
    --no-allow-missing-retests)
      ALLOW_MISSING_RETESTS_FLAG="--no-allow-missing-retests"
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
  "$PYTHON_BIN" -m jarvis.cli improvement operator-cycle
  --config-path "$CONFIG_PATH"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

if [[ -n "$OUTPUT_DIR" ]]; then
  CMD+=(--output-dir "$OUTPUT_DIR")
fi

if [[ -n "$STRICT_FLAG" ]]; then
  CMD+=("$STRICT_FLAG")
fi

if [[ -n "$ALLOW_MISSING_FEEDS_FLAG" ]]; then
  CMD+=("$ALLOW_MISSING_FEEDS_FLAG")
fi

if [[ -n "$ALLOW_MISSING_INPUTS_FLAG" ]]; then
  CMD+=("$ALLOW_MISSING_INPUTS_FLAG")
fi

if [[ -n "$ALLOW_MISSING_RETESTS_FLAG" ]]; then
  CMD+=("$ALLOW_MISSING_RETESTS_FLAG")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
