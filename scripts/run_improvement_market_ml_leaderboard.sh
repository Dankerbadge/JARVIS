#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <input_path> [--output-path <path>] [--as-of <iso8601>] [--lookback-days <days>] [--domain <domain>] [--source <source>] [--min-cross-app-count <n>] [--strict] [extra_cli_flags...]"
  exit 2
fi

INPUT_PATH="$1"
shift

OUTPUT_PATH="${JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_OUTPUT_PATH:-}"
AS_OF_VALUE="${JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_AS_OF:-}"
LOOKBACK_DAYS="${JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_LOOKBACK_DAYS:-10}"
DOMAIN_VALUE="${JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_DOMAIN:-market_ml}"
SOURCE_VALUE="${JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_SOURCE:-ml_incident_log}"
MIN_CROSS_APP_COUNT="${JARVIS_IMPROVEMENT_MARKET_ML_LEADERBOARD_MIN_CROSS_APP_COUNT:-1}"
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
    --as-of)
      if [[ $# -lt 2 ]]; then
        echo "error: --as-of requires an ISO8601 timestamp"
        exit 2
      fi
      AS_OF_VALUE="$2"
      shift 2
      ;;
    --lookback-days)
      if [[ $# -lt 2 ]]; then
        echo "error: --lookback-days requires an integer value"
        exit 2
      fi
      LOOKBACK_DAYS="$2"
      shift 2
      ;;
    --domain)
      if [[ $# -lt 2 ]]; then
        echo "error: --domain requires a value"
        exit 2
      fi
      DOMAIN_VALUE="$2"
      shift 2
      ;;
    --source)
      if [[ $# -lt 2 ]]; then
        echo "error: --source requires a value"
        exit 2
      fi
      SOURCE_VALUE="$2"
      shift 2
      ;;
    --min-cross-app-count)
      if [[ $# -lt 2 ]]; then
        echo "error: --min-cross-app-count requires an integer value"
        exit 2
      fi
      MIN_CROSS_APP_COUNT="$2"
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
  "$PYTHON_BIN" -m jarvis.cli improvement fitness-leaderboard
  --input-path "$INPUT_PATH"
  --domain "$DOMAIN_VALUE"
  --source "$SOURCE_VALUE"
  --lookback-days "$LOOKBACK_DAYS"
  --min-cross-app-count "$MIN_CROSS_APP_COUNT"
)

if [[ -n "$AS_OF_VALUE" ]]; then
  CMD+=(--as-of "$AS_OF_VALUE")
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
