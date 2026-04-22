#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <domain> <source> <input_path> [--no-auto-register] [--report-path <path>] [extra_cli_flags...]"
  exit 2
fi

DOMAIN="$1"
SOURCE="$2"
INPUT_PATH="$3"
shift 3

AUTO_REGISTER_FLAG="--auto-register"
REPORT_PATH="${JARVIS_IMPROVEMENT_REPORT_PATH:-}"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-auto-register)
      AUTO_REGISTER_FLAG="--no-auto-register"
      shift
      ;;
    --report-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --report-path requires a path"
        exit 2
      fi
      REPORT_PATH="$2"
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
INPUT_FORMAT="${JARVIS_IMPROVEMENT_INPUT_FORMAT:-jsonl}"
OWNER="${JARVIS_IMPROVEMENT_OWNER:-operator}"
MIN_CLUSTER_COUNT="${JARVIS_IMPROVEMENT_MIN_CLUSTER_COUNT:-2}"
PROPOSAL_LIMIT="${JARVIS_IMPROVEMENT_PROPOSAL_LIMIT:-5}"

CMD=(
  "$PYTHON_BIN" -m jarvis.cli improvement cycle-from-file
  --domain "$DOMAIN"
  --source "$SOURCE"
  --input-path "$INPUT_PATH"
  --input-format "$INPUT_FORMAT"
  --owner "$OWNER"
  --min-cluster-count "$MIN_CLUSTER_COUNT"
  --proposal-limit "$PROPOSAL_LIMIT"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
  "$AUTO_REGISTER_FLAG"
)

if [[ -n "$REPORT_PATH" ]]; then
  CMD+=(--report-path "$REPORT_PATH")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
