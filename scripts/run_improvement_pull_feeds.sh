#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <config_path> [--feed-names <csv>] [--allow-missing] [--strict] [--output-path <path>] [extra_cli_flags...]"
  exit 2
fi

CONFIG_PATH="$1"
shift

FEED_NAMES="${JARVIS_IMPROVEMENT_FEED_NAMES:-}"
ALLOW_MISSING_FLAG=""
STRICT_FLAG=""
OUTPUT_PATH="${JARVIS_IMPROVEMENT_FEED_PULL_OUTPUT_PATH:-}"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --feed-names)
      if [[ $# -lt 2 ]]; then
        echo "error: --feed-names requires a CSV value"
        exit 2
      fi
      FEED_NAMES="$2"
      shift 2
      ;;
    --allow-missing)
      ALLOW_MISSING_FLAG="--allow-missing"
      shift
      ;;
    --strict)
      STRICT_FLAG="--strict"
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

PYTHON_BIN="${JARVIS_IMPROVEMENT_PYTHON_BIN:-python3}"
TIMEOUT_SECONDS="${JARVIS_IMPROVEMENT_FEED_TIMEOUT_SECONDS:-20}"

CMD=(
  "$PYTHON_BIN" -m jarvis.cli improvement pull-feeds
  --config-path "$CONFIG_PATH"
  --timeout-seconds "$TIMEOUT_SECONDS"
)

if [[ -n "$FEED_NAMES" ]]; then
  CMD+=(--feed-names "$FEED_NAMES")
fi

if [[ -n "$ALLOW_MISSING_FLAG" ]]; then
  CMD+=("$ALLOW_MISSING_FLAG")
fi

if [[ -n "$STRICT_FLAG" ]]; then
  CMD+=("$STRICT_FLAG")
fi

if [[ -n "$OUTPUT_PATH" ]]; then
  CMD+=(--output-path "$OUTPUT_PATH")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
