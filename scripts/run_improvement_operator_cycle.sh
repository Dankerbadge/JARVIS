#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <config_path> [--output-dir <path>] [--operator-report-path <path>] [--strict] [--no-allow-missing-feeds] [--no-allow-missing-inputs] [--no-allow-missing-retests] [--seed-enable] [--seed-domains <csv>] [--seed-leaderboard-input-path <path>] [--seed-min-signal-count-current <n>] [--draft-enable] [--draft-seed-report-path <path>] [--knowledge-route-enable|--no-knowledge-route] [--knowledge-route-output-path <path>] [--knowledge-route-strict] [extra_cli_flags...]"
  exit 2
fi

CONFIG_PATH="$1"
shift

OUTPUT_DIR="${JARVIS_IMPROVEMENT_OPERATOR_OUTPUT_DIR:-}"
OPERATOR_REPORT_PATH="${JARVIS_IMPROVEMENT_OPERATOR_REPORT_PATH:-}"
STRICT_FLAG=""
ALLOW_MISSING_FEEDS_FLAG=""
ALLOW_MISSING_INPUTS_FLAG=""
ALLOW_MISSING_RETESTS_FLAG=""
SEED_ENABLE_FLAG=""
SEED_DOMAINS="${JARVIS_IMPROVEMENT_OPERATOR_SEED_DOMAINS:-}"
SEED_LEADERBOARD_INPUT_PATH="${JARVIS_IMPROVEMENT_OPERATOR_SEED_LEADERBOARD_INPUT_PATH:-}"
SEED_MIN_SIGNAL_COUNT_CURRENT="${JARVIS_IMPROVEMENT_OPERATOR_SEED_MIN_SIGNAL_COUNT_CURRENT:-}"
DRAFT_ENABLE_FLAG=""
DRAFT_SEED_REPORT_PATH="${JARVIS_IMPROVEMENT_OPERATOR_DRAFT_SEED_REPORT_PATH:-}"
KNOWLEDGE_ROUTE_ENABLE="${JARVIS_IMPROVEMENT_OPERATOR_KNOWLEDGE_ROUTE_ENABLE:-1}"
KNOWLEDGE_ROUTE_OUTPUT_PATH="${JARVIS_IMPROVEMENT_OPERATOR_KNOWLEDGE_ROUTE_OUTPUT_PATH:-}"
KNOWLEDGE_ROUTE_STRICT="${JARVIS_IMPROVEMENT_OPERATOR_KNOWLEDGE_ROUTE_STRICT:-0}"
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
    --operator-report-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --operator-report-path requires a path"
        exit 2
      fi
      OPERATOR_REPORT_PATH="$2"
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
    --seed-min-signal-count-current)
      if [[ $# -lt 2 ]]; then
        echo "error: --seed-min-signal-count-current requires an integer value"
        exit 2
      fi
      SEED_MIN_SIGNAL_COUNT_CURRENT="$2"
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
    --knowledge-route-enable)
      KNOWLEDGE_ROUTE_ENABLE="1"
      shift
      ;;
    --no-knowledge-route)
      KNOWLEDGE_ROUTE_ENABLE="0"
      shift
      ;;
    --knowledge-route-output-path)
      if [[ $# -lt 2 ]]; then
        echo "error: --knowledge-route-output-path requires a path"
        exit 2
      fi
      KNOWLEDGE_ROUTE_OUTPUT_PATH="$2"
      shift 2
      ;;
    --knowledge-route-strict)
      KNOWLEDGE_ROUTE_STRICT="1"
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

truthy() {
  local raw
  raw="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    1|true|yes|on|enabled)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

to_abs_path() {
  local raw="$1"
  if [[ -z "$raw" ]]; then
    printf '%s' ""
    return 0
  fi
  if [[ "$raw" = /* ]]; then
    printf '%s' "$raw"
    return 0
  fi
  printf '%s' "$(pwd)/$raw"
}

REPO_PATH="${JARVIS_REPO_PATH:-$(pwd)}"
DB_PATH="${JARVIS_DB_PATH:-${REPO_PATH}/.jarvis/jarvis.db}"
PYTHON_BIN="${JARVIS_IMPROVEMENT_PYTHON_BIN:-python3}"
CONFIG_PATH_ABS="$(to_abs_path "$CONFIG_PATH")"
DEFAULT_OUTPUT_DIR="$(dirname "$CONFIG_PATH_ABS")/output/improvement/operator_cycle"
if [[ -z "$OUTPUT_DIR" ]]; then
  EFFECTIVE_OUTPUT_DIR="$DEFAULT_OUTPUT_DIR"
else
  EFFECTIVE_OUTPUT_DIR="$(to_abs_path "$OUTPUT_DIR")"
fi
if [[ -z "$OPERATOR_REPORT_PATH" ]]; then
  OPERATOR_REPORT_PATH="${EFFECTIVE_OUTPUT_DIR}/operator_cycle_report.json"
else
  OPERATOR_REPORT_PATH="$(to_abs_path "$OPERATOR_REPORT_PATH")"
fi
if [[ -z "$KNOWLEDGE_ROUTE_OUTPUT_PATH" ]]; then
  KNOWLEDGE_ROUTE_OUTPUT_PATH="${EFFECTIVE_OUTPUT_DIR}/knowledge_bootstrap_route.json"
else
  KNOWLEDGE_ROUTE_OUTPUT_PATH="$(to_abs_path "$KNOWLEDGE_ROUTE_OUTPUT_PATH")"
fi

CMD=(
  "$PYTHON_BIN" -m jarvis.cli improvement operator-cycle
  --config-path "$CONFIG_PATH"
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
  --operator-report-path "$OPERATOR_REPORT_PATH"
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

if [[ -n "$SEED_MIN_SIGNAL_COUNT_CURRENT" ]]; then
  CMD+=(--seed-min-signal-count-current "$SEED_MIN_SIGNAL_COUNT_CURRENT")
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

if truthy "$KNOWLEDGE_ROUTE_ENABLE"; then
  if [[ ! -f "$OPERATOR_REPORT_PATH" ]]; then
    echo "warning: operator report path not found after operator-cycle run: $OPERATOR_REPORT_PATH" >&2
    if truthy "$KNOWLEDGE_ROUTE_STRICT"; then
      exit 2
    fi
    exit 0
  fi

  ROUTE_CMD=(
    "$PYTHON_BIN" -m jarvis.cli improvement knowledge-bootstrap-route
    --report-path "$OPERATOR_REPORT_PATH"
    --output-path "$KNOWLEDGE_ROUTE_OUTPUT_PATH"
    --json-compact
  )
  if truthy "$KNOWLEDGE_ROUTE_STRICT"; then
    ROUTE_CMD+=(--strict)
  fi

  ROUTE_PAYLOAD="$("${ROUTE_CMD[@]}")"
  ROUTE_SUMMARY="$(
    printf '%s' "$ROUTE_PAYLOAD" \
      | "$PYTHON_BIN" -c 'import json,sys
payload=json.loads(sys.stdin.read() or "{}")
status=str(payload.get("status") or "").strip() or "unknown"
phase=str(payload.get("phase") or "").strip() or "unknown"
route=str(payload.get("route") or "").strip() or "unknown"
print(f"status={status} phase={phase} route={route}")
'
  )"
  echo "[knowledge-bootstrap-route] $ROUTE_SUMMARY artifact=$KNOWLEDGE_ROUTE_OUTPUT_PATH" >&2
fi
