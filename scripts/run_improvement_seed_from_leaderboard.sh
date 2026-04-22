#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <leaderboard_path> [--trends <csv>] [--entry-source <leaderboard|shared_market_displeasures|white_space_candidates>] [--min-cross-app-count <n>] [--limit <n>] [--strict] [--output-path <path>] [extra_cli_flags...]"
  exit 2
fi

LEADERBOARD_PATH="$1"
shift

TRENDS="${JARVIS_IMPROVEMENT_LEADERBOARD_TRENDS:-new,rising}"
ENTRY_SOURCE="${JARVIS_IMPROVEMENT_LEADERBOARD_ENTRY_SOURCE:-leaderboard}"
MIN_CROSS_APP_COUNT="${JARVIS_IMPROVEMENT_LEADERBOARD_MIN_CROSS_APP_COUNT:-0}"
LIMIT_VALUE="${JARVIS_IMPROVEMENT_LEADERBOARD_SEED_LIMIT:-8}"
STRICT_FLAG=""
OUTPUT_PATH="${JARVIS_IMPROVEMENT_LEADERBOARD_SEED_OUTPUT_PATH:-}"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --trends)
      if [[ $# -lt 2 ]]; then
        echo "error: --trends requires a CSV value"
        exit 2
      fi
      TRENDS="$2"
      shift 2
      ;;
    --limit)
      if [[ $# -lt 2 ]]; then
        echo "error: --limit requires an integer value"
        exit 2
      fi
      LIMIT_VALUE="$2"
      shift 2
      ;;
    --entry-source)
      if [[ $# -lt 2 ]]; then
        echo "error: --entry-source requires a value"
        exit 2
      fi
      ENTRY_SOURCE="$2"
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
  "$PYTHON_BIN" -m jarvis.cli improvement seed-from-leaderboard
  --leaderboard-path "$LEADERBOARD_PATH"
  --trends "$TRENDS"
  --entry-source "$ENTRY_SOURCE"
  --min-cross-app-count "$MIN_CROSS_APP_COUNT"
  --limit "$LIMIT_VALUE"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

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
