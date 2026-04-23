#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <route_output_path> [--output-path <path>] [--rerun-command <cmd>] [--history-path <path>] [--history-window <n>] [--repeat-threshold <n>] [--rate-ceiling <float>] [--consecutive-runs <n>] [--strict] [extra_cli_flags...]"
  exit 2
fi

ROUTE_OUTPUT_PATH="$1"
shift

OUTPUT_PATH="${JARVIS_IMPROVEMENT_BENCHMARK_STALE_RUNTIME_ALERT_OUTPUT_PATH:-}"
RERUN_COMMAND="${JARVIS_IMPROVEMENT_BENCHMARK_STALE_RUNTIME_ALERT_RERUN_COMMAND:-}"
HISTORY_PATH="${JARVIS_IMPROVEMENT_BENCHMARK_STALE_RUNTIME_ALERT_HISTORY_PATH:-}"
HISTORY_WINDOW="${JARVIS_IMPROVEMENT_BENCHMARK_STALE_RUNTIME_ALERT_HISTORY_WINDOW:-}"
REPEAT_THRESHOLD="${JARVIS_IMPROVEMENT_BENCHMARK_STALE_RUNTIME_ALERT_REPEAT_THRESHOLD:-}"
RATE_CEILING="${JARVIS_IMPROVEMENT_BENCHMARK_STALE_RUNTIME_ALERT_RATE_CEILING:-}"
CONSECUTIVE_RUNS="${JARVIS_IMPROVEMENT_BENCHMARK_STALE_RUNTIME_ALERT_CONSECUTIVE_RUNS:-}"
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
    --rerun-command)
      if [[ $# -lt 2 ]]; then
        echo "error: --rerun-command requires a command string"
        exit 2
      fi
      RERUN_COMMAND="$2"
      shift 2
      ;;
    --history-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --history-path requires a path"
        exit 2
      fi
      HISTORY_PATH="$2"
      shift 2
      ;;
    --history-window)
      if [[ $# -lt 2 ]]; then
        echo "error: --history-window requires an integer value"
        exit 2
      fi
      HISTORY_WINDOW="$2"
      shift 2
      ;;
    --repeat-threshold)
      if [[ $# -lt 2 ]]; then
        echo "error: --repeat-threshold requires an integer value"
        exit 2
      fi
      REPEAT_THRESHOLD="$2"
      shift 2
      ;;
    --rate-ceiling)
      if [[ $# -lt 2 ]]; then
        echo "error: --rate-ceiling requires a floating-point value"
        exit 2
      fi
      RATE_CEILING="$2"
      shift 2
      ;;
    --consecutive-runs)
      if [[ $# -lt 2 ]]; then
        echo "error: --consecutive-runs requires an integer value"
        exit 2
      fi
      CONSECUTIVE_RUNS="$2"
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

REPO_PATH="${JARVIS_REPO_PATH:-$(pwd)}"
DB_PATH="${JARVIS_DB_PATH:-${REPO_PATH}/.jarvis/jarvis.db}"
PYTHON_BIN="${JARVIS_IMPROVEMENT_PYTHON_BIN:-python3}"

CMD=(
  "$PYTHON_BIN" -m jarvis.cli improvement benchmark-stale-fallback-runtime-alert
  --route-output-path "$ROUTE_OUTPUT_PATH"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

if [[ -n "$OUTPUT_PATH" ]]; then
  CMD+=(--output-path "$OUTPUT_PATH")
fi

if [[ -n "$RERUN_COMMAND" ]]; then
  CMD+=(--rerun-command "$RERUN_COMMAND")
fi

if [[ -n "$HISTORY_PATH" ]]; then
  CMD+=(--history-path "$HISTORY_PATH")
fi

if [[ -n "$HISTORY_WINDOW" ]]; then
  CMD+=(--history-window "$HISTORY_WINDOW")
fi

if [[ -n "$REPEAT_THRESHOLD" ]]; then
  CMD+=(--repeat-threshold "$REPEAT_THRESHOLD")
fi

if [[ -n "$RATE_CEILING" ]]; then
  CMD+=(--rate-ceiling "$RATE_CEILING")
fi

if [[ -n "$CONSECUTIVE_RUNS" ]]; then
  CMD+=(--consecutive-runs "$CONSECUTIVE_RUNS")
fi

if [[ -n "$STRICT_FLAG" ]]; then
  CMD+=("$STRICT_FLAG")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
