#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <config_path> [--output-dir <path>] [--strict] [--no-allow-missing-feeds] [--no-allow-missing-inputs] [--no-allow-missing-retests] [--seed-enable] [--seed-domains <csv>] [--seed-leaderboard-input-path <path>] [--draft-enable] [--draft-seed-report-path <path>] [extra_cli_flags...]"
  exit 2
fi

CONFIG_PATH="$1"
shift

OUTPUT_DIR="${JARVIS_IMPROVEMENT_OPERATOR_OUTPUT_DIR:-}"
STRICT_FLAG=""
ALLOW_MISSING_FEEDS_FLAG=""
ALLOW_MISSING_INPUTS_FLAG=""
ALLOW_MISSING_RETESTS_FLAG=""
SEED_ENABLE_FLAG=""
SEED_DOMAINS="${JARVIS_IMPROVEMENT_OPERATOR_SEED_DOMAINS:-}"
SEED_LEADERBOARD_INPUT_PATH="${JARVIS_IMPROVEMENT_OPERATOR_SEED_LEADERBOARD_INPUT_PATH:-}"
DRAFT_ENABLE_FLAG=""
DRAFT_SEED_REPORT_PATH="${JARVIS_IMPROVEMENT_OPERATOR_DRAFT_SEED_REPORT_PATH:-}"
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
    --seed-enable)
      SEED_ENABLE_FLAG="--seed-enable"
      shift
      ;;
    --seed-domains)
      if [[ $# -lt 2 ]]; then
        echo "error: --seed-domains requires a csv list"
        exit 2
      fi
      SEED_DOMAINS="$2"
      shift 2
      ;;
    --seed-leaderboard-input-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --seed-leaderboard-input-path requires a path"
        exit 2
      fi
      SEED_LEADERBOARD_INPUT_PATH="$2"
      shift 2
      ;;
    --draft-enable)
      DRAFT_ENABLE_FLAG="--draft-enable"
      shift
      ;;
    --draft-seed-report-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --draft-seed-report-path requires a path"
        exit 2
      fi
      DRAFT_SEED_REPORT_PATH="$2"
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

if [[ -n "$SEED_ENABLE_FLAG" ]]; then
  CMD+=("$SEED_ENABLE_FLAG")
fi

if [[ -n "$SEED_DOMAINS" ]]; then
  CMD+=(--seed-domains "$SEED_DOMAINS")
fi

if [[ -n "$SEED_LEADERBOARD_INPUT_PATH" ]]; then
  CMD+=(--seed-leaderboard-input-path "$SEED_LEADERBOARD_INPUT_PATH")
fi

if [[ -n "$DRAFT_ENABLE_FLAG" ]]; then
  CMD+=("$DRAFT_ENABLE_FLAG")
fi

if [[ -n "$DRAFT_SEED_REPORT_PATH" ]]; then
  CMD+=(--draft-seed-report-path "$DRAFT_SEED_REPORT_PATH")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
