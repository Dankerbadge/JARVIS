#!/usr/bin/env bash
set -euo pipefail

SEED_REPORT_PATH="${JARVIS_IMPROVEMENT_DRAFT_SEED_REPORT_PATH:-}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  SEED_REPORT_PATH="$1"
  shift
fi

BENCHMARK_REPORT_PATH="${JARVIS_IMPROVEMENT_DRAFT_BENCHMARK_REPORT_PATH:-}"
BENCHMARK_MIN_OPPORTUNITY="${JARVIS_IMPROVEMENT_DRAFT_BENCHMARK_MIN_OPPORTUNITY:-}"
PIPELINE_CONFIG_PATH="${JARVIS_IMPROVEMENT_DRAFT_PIPELINE_CONFIG_PATH:-}"
ARTIFACTS_DIR="${JARVIS_IMPROVEMENT_DRAFT_ARTIFACTS_DIR:-analysis/improvement/experiment_artifacts}"
WRITE_CONFIG_PATH="${JARVIS_IMPROVEMENT_DRAFT_WRITE_CONFIG_PATH:-}"
LIMIT_VALUE="${JARVIS_IMPROVEMENT_DRAFT_LIMIT:-8}"
STRICT_FLAG=""
OUTPUT_PATH="${JARVIS_IMPROVEMENT_DRAFT_OUTPUT_PATH:-}"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --benchmark-report-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --benchmark-report-path requires a path"
        exit 2
      fi
      BENCHMARK_REPORT_PATH="$2"
      shift 2
      ;;
    --benchmark-min-opportunity)
      if [[ $# -lt 2 ]]; then
        echo "error: --benchmark-min-opportunity requires a numeric value"
        exit 2
      fi
      BENCHMARK_MIN_OPPORTUNITY="$2"
      shift 2
      ;;
    --pipeline-config-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --pipeline-config-path requires a path"
        exit 2
      fi
      PIPELINE_CONFIG_PATH="$2"
      shift 2
      ;;
    --artifacts-dir)
      if [[ $# -lt 2 ]]; then
        echo "error: --artifacts-dir requires a path"
        exit 2
      fi
      ARTIFACTS_DIR="$2"
      shift 2
      ;;
    --write-config-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --write-config-path requires a path"
        exit 2
      fi
      WRITE_CONFIG_PATH="$2"
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
  "$PYTHON_BIN" -m jarvis.cli improvement draft-experiment-jobs
  --limit "$LIMIT_VALUE"
  --artifacts-dir "$ARTIFACTS_DIR"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

if [[ -z "$SEED_REPORT_PATH" && -z "$BENCHMARK_REPORT_PATH" ]]; then
  echo "usage: $0 [<seed_report_path>] [--benchmark-report-path <path>] [--benchmark-min-opportunity <value>] [--pipeline-config-path <path>] [--artifacts-dir <path>] [--write-config-path <path>] [--limit <n>] [--strict] [--output-path <path>] [extra_cli_flags...]"
  echo "error: provide a seed report path or --benchmark-report-path"
  exit 2
fi

if [[ -n "$SEED_REPORT_PATH" ]]; then
  CMD+=(--seed-report-path "$SEED_REPORT_PATH")
fi

if [[ -n "$BENCHMARK_REPORT_PATH" ]]; then
  CMD+=(--benchmark-report-path "$BENCHMARK_REPORT_PATH")
fi

if [[ -n "$BENCHMARK_MIN_OPPORTUNITY" ]]; then
  CMD+=(--benchmark-min-opportunity "$BENCHMARK_MIN_OPPORTUNITY")
fi

if [[ -n "$PIPELINE_CONFIG_PATH" ]]; then
  CMD+=(--pipeline-config-path "$PIPELINE_CONFIG_PATH")
fi

if [[ -n "$WRITE_CONFIG_PATH" ]]; then
  CMD+=(--write-config-path "$WRITE_CONFIG_PATH")
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
