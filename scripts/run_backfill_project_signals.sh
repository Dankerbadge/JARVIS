#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <project_id> [profile_key] [preset] [--execute] [--policy-config <path>] [extra_cli_flags...]"
  exit 2
fi

PROJECT_ID="${1}"
PROFILE_KEY="${2:-nightly}"
PRESET="${3:-balanced}"
shift $(( $# >= 3 ? 3 : $# ))

EXECUTE_FLAG=""
POLICY_CONFIG="${JARVIS_BACKFILL_WARNING_POLICY_CONFIG:-}"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute)
      EXECUTE_FLAG="--execute"
      shift
      ;;
    --policy-config)
      if [[ $# -lt 2 ]]; then
        echo "error: --policy-config requires a path"
        exit 2
      fi
      POLICY_CONFIG="$2"
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
PYTHON_BIN="${JARVIS_BACKFILL_PYTHON_BIN:-python3}"

CMD=(
  "$PYTHON_BIN" -m jarvis.cli plans backfill-project-signals "$PROJECT_ID"
  --profile-key "$PROFILE_KEY"
  --preset "$PRESET"
  --summary-only
  --output warnings
  --json-compact
  --repo-path "$REPO_PATH"
  --db-path "$DB_PATH"
)

if [[ -n "$EXECUTE_FLAG" ]]; then
  CMD+=("$EXECUTE_FLAG")
fi

if [[ -n "${POLICY_CONFIG}" ]]; then
  CMD+=(--warning-policy-config "$POLICY_CONFIG")
fi

if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
