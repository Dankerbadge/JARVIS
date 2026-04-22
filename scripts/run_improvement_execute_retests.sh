#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <pipeline_report_path> [--max-runs <n>] [--artifact-dir <path>] [--strict] [--output-path <path>] [extra_cli_flags...]"
  exit 2
fi

PIPELINE_REPORT_PATH="$1"
shift

MAX_RUNS="${JARVIS_IMPROVEMENT_RETEST_MAX_RUNS:-}"
ARTIFACT_DIR="${JARVIS_IMPROVEMENT_RETEST_ARTIFACT_DIR:-}"
OUTPUT_PATH="${JARVIS_IMPROVEMENT_RETEST_OUTPUT_PATH:-}"
STRICT_FLAG=""
ALLOW_MISSING_JOBS_FLAG=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-runs)
      if [[ $# -lt 2 ]]; then
        echo "error: --max-runs requires a value"
        exit 2
      fi
      MAX_RUNS="$2"
      shift 2
      ;;
    --artifact-dir)
      if [[ $# -lt 2 ]]; then
        echo "error: --artifact-dir requires a path"
        exit 2
      fi
      ARTIFACT_DIR="$2"
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
    --allow-missing-jobs)
      ALLOW_MISSING_JOBS_FLAG="--allow-missing-jobs"
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
NOTES_PREFIX="${JARVIS_IMPROVEMENT_RETEST_NOTES_PREFIX:-auto_retest}"

CMD=(
  "$PYTHON_BIN" -m jarvis.cli improvement execute-retests
  --pipeline-report-path "$PIPELINE_REPORT_PATH"
  --notes-prefix "$NOTES_PREFIX"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

if [[ -n "$MAX_RUNS" ]]; then
  CMD+=(--max-runs "$MAX_RUNS")
fi

if [[ -n "$ARTIFACT_DIR" ]]; then
  CMD+=(--artifact-dir "$ARTIFACT_DIR")
fi

if [[ -n "$OUTPUT_PATH" ]]; then
  CMD+=(--output-path "$OUTPUT_PATH")
fi

if [[ -n "$STRICT_FLAG" ]]; then
  CMD+=("$STRICT_FLAG")
fi

if [[ -n "$ALLOW_MISSING_JOBS_FLAG" ]]; then
  CMD+=("$ALLOW_MISSING_JOBS_FLAG")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
