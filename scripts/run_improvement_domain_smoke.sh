#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <config_path> <domain> [--output-dir <path>] [--feed-timeout-seconds <seconds>] [--allow-missing] [--strict] [--as-of <iso8601>] [extra_seed_flags...]"
  exit 2
fi

CONFIG_PATH_RAW="$1"
DOMAIN_RAW="$2"
shift 2

OUTPUT_DIR="${JARVIS_IMPROVEMENT_DOMAIN_SMOKE_OUTPUT_DIR:-}"
FEED_TIMEOUT_SECONDS="${JARVIS_IMPROVEMENT_DOMAIN_SMOKE_FEED_TIMEOUT_SECONDS:-20}"
ALLOW_MISSING_FLAG=""
STRICT_FLAG=""
AS_OF_VALUE="${JARVIS_IMPROVEMENT_DOMAIN_SMOKE_AS_OF:-}"
EXTRA_SEED_ARGS=()

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
    --feed-timeout-seconds)
      if [[ $# -lt 2 ]]; then
        echo "error: --feed-timeout-seconds requires a numeric value"
        exit 2
      fi
      FEED_TIMEOUT_SECONDS="$2"
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
    --as-of)
      if [[ $# -lt 2 ]]; then
        echo "error: --as-of requires an ISO8601 timestamp"
        exit 2
      fi
      AS_OF_VALUE="$2"
      shift 2
      ;;
    *)
      EXTRA_SEED_ARGS+=("$1")
      shift
      ;;
  esac
done

normalize_domain() {
  local raw normalized
  raw="${1:-}"
  normalized="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$normalized" in
    quantitative_finance)
      printf '%s' "quant_finance"
      ;;
    weather_betting|kalshi)
      printf '%s' "kalshi_weather"
      ;;
    fitness)
      printf '%s' "fitness_apps"
      ;;
    market_machine_learning)
      printf '%s' "market_ml"
      ;;
    *)
      printf '%s' "$normalized"
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

DOMAIN="$(normalize_domain "$DOMAIN_RAW")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${JARVIS_IMPROVEMENT_PYTHON_BIN:-python3}"
CONFIG_PATH="$(to_abs_path "$CONFIG_PATH_RAW")"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "error: config path not found: $CONFIG_PATH"
  exit 2
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="$(dirname "$CONFIG_PATH")/output/improvement/domain_smoke/${DOMAIN}"
else
  OUTPUT_DIR="$(to_abs_path "$OUTPUT_DIR")"
fi
mkdir -p "$OUTPUT_DIR"

eval "$(
  "$PYTHON_BIN" - "$CONFIG_PATH" "$DOMAIN" <<'PY'
import json
import shlex
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).expanduser().resolve()
domain = str(sys.argv[2]).strip().lower()
payload = json.loads(config_path.read_text(encoding="utf-8"))
config_dir = config_path.parent
defaults = dict(payload.get("defaults") or {})


def _resolve_domain_default(key: str, fallback):
    by_domain = defaults.get(f"{key}_by_domain")
    if isinstance(by_domain, dict):
        value = by_domain.get(domain)
        if value is not None:
            return value
    value = defaults.get(key)
    if value is not None:
        return value
    return fallback


feedback_jobs = [row for row in list(payload.get("feedback_jobs") or []) if isinstance(row, dict)]
selected_feedback = None
for row in feedback_jobs:
    row_domain = str(row.get("domain") or "").strip().lower()
    if row_domain == domain:
        selected_feedback = row
        break
if selected_feedback is None:
    raise SystemExit(f"missing_feedback_job_for_domain:{domain}")

feedback_input_raw = str(selected_feedback.get("input_path") or "").strip()
if not feedback_input_raw:
    raise SystemExit(f"missing_feedback_input_path_for_domain:{domain}")

feedback_input_path = Path(feedback_input_raw).expanduser()
if not feedback_input_path.is_absolute():
    feedback_input_path = (config_dir / feedback_input_path).resolve()

leaderboard_source = str(selected_feedback.get("source") or "market_reviews").strip() or "market_reviews"

feed_name = ""
feed_jobs = [row for row in list(payload.get("feed_jobs") or []) if isinstance(row, dict)]
for row in feed_jobs:
    output_raw = str(row.get("output_path") or "").strip()
    if not output_raw:
        continue
    output_path = Path(output_raw).expanduser()
    if not output_path.is_absolute():
        output_path = (config_dir / output_path).resolve()
    if output_path == feedback_input_path:
        candidate_feed_name = str(row.get("name") or "").strip()
        if candidate_feed_name:
            feed_name = candidate_feed_name
            break

entry_source = str(_resolve_domain_default("seed_entry_source", "leaderboard") or "leaderboard").strip() or "leaderboard"
if entry_source not in {"leaderboard", "shared_market_displeasures", "white_space_candidates"}:
    entry_source = "leaderboard"

fallback_entry_source = str(defaults.get("seed_fallback_entry_source") or "leaderboard").strip() or "leaderboard"
if fallback_entry_source not in {"leaderboard", "shared_market_displeasures", "white_space_candidates", "none"}:
    fallback_entry_source = "leaderboard"

result = {
    "FEED_NAME": feed_name,
    "INPUT_PATH": str(feedback_input_path),
    "LEADERBOARD_SOURCE": leaderboard_source,
    "LOOKBACK_DAYS": str(_resolve_domain_default("seed_lookback_days", 7)),
    "MIN_CROSS_APP_COUNT": str(_resolve_domain_default("seed_min_cross_app_count", 1)),
    "SEED_TRENDS": str(defaults.get("seed_trends") or "new,rising"),
    "SEED_ENTRY_SOURCE": entry_source,
    "SEED_FALLBACK_ENTRY_SOURCE": fallback_entry_source,
    "SEED_LIMIT": str(_resolve_domain_default("seed_limit", 8)),
    "SEED_MIN_SIGNAL_COUNT_CURRENT": str(_resolve_domain_default("seed_min_signal_count_current", 0)),
    "SEED_MIN_IMPACT_SCORE": str(_resolve_domain_default("seed_min_impact_score", 0.0)),
    "SEED_MIN_IMPACT_DELTA": str(_resolve_domain_default("seed_min_impact_delta", 0.0)),
    "SEED_SOURCE": f"domain_smoke_{domain}",
}

for key, value in result.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"

LEADERBOARD_SCRIPT=""
case "$DOMAIN" in
  fitness_apps)
    LEADERBOARD_SCRIPT="${SCRIPT_DIR}/run_improvement_fitness_leaderboard.sh"
    ;;
  kalshi_weather)
    LEADERBOARD_SCRIPT="${SCRIPT_DIR}/run_improvement_kalshi_leaderboard.sh"
    ;;
  quant_finance)
    LEADERBOARD_SCRIPT="${SCRIPT_DIR}/run_improvement_quant_leaderboard.sh"
    ;;
  market_ml)
    LEADERBOARD_SCRIPT="${SCRIPT_DIR}/run_improvement_market_ml_leaderboard.sh"
    ;;
  *)
    echo "error: unsupported domain for smoke wrapper: ${DOMAIN}"
    exit 2
    ;;
esac

PULL_REPORT_PATH="${OUTPUT_DIR}/${DOMAIN}_pull_report.json"
LEADERBOARD_REPORT_PATH="${OUTPUT_DIR}/${DOMAIN}_leaderboard_report.json"
SEED_REPORT_PATH="${OUTPUT_DIR}/${DOMAIN}_seed_report.json"
SMOKE_SUMMARY_PATH="${OUTPUT_DIR}/${DOMAIN}_smoke_summary.json"

PULL_CMD=(
  "${SCRIPT_DIR}/run_improvement_pull_feeds.sh"
  "$CONFIG_PATH"
  --output-path "$PULL_REPORT_PATH"
)
if [[ -n "${FEED_NAME:-}" ]]; then
  PULL_CMD+=(--feed-names "$FEED_NAME")
fi
if [[ -n "$ALLOW_MISSING_FLAG" ]]; then
  PULL_CMD+=("$ALLOW_MISSING_FLAG")
fi
if [[ -n "$STRICT_FLAG" ]]; then
  PULL_CMD+=("$STRICT_FLAG")
fi

LEADERBOARD_CMD=(
  "$LEADERBOARD_SCRIPT"
  "$INPUT_PATH"
  --output-path "$LEADERBOARD_REPORT_PATH"
  --lookback-days "$LOOKBACK_DAYS"
  --domain "$DOMAIN"
  --source "$LEADERBOARD_SOURCE"
  --min-cross-app-count "$MIN_CROSS_APP_COUNT"
)
if [[ -n "$AS_OF_VALUE" ]]; then
  LEADERBOARD_CMD+=(--as-of "$AS_OF_VALUE")
fi
if [[ -n "$STRICT_FLAG" ]]; then
  LEADERBOARD_CMD+=("$STRICT_FLAG")
fi

SEED_CMD=(
  "${SCRIPT_DIR}/run_improvement_seed_from_leaderboard.sh"
  "$LEADERBOARD_REPORT_PATH"
  --trends "$SEED_TRENDS"
  --entry-source "$SEED_ENTRY_SOURCE"
  --fallback-entry-source "$SEED_FALLBACK_ENTRY_SOURCE"
  --min-cross-app-count "$MIN_CROSS_APP_COUNT"
  --min-signal-count-current "$SEED_MIN_SIGNAL_COUNT_CURRENT"
  --min-impact-score "$SEED_MIN_IMPACT_SCORE"
  --min-impact-delta "$SEED_MIN_IMPACT_DELTA"
  --limit "$SEED_LIMIT"
  --output-path "$SEED_REPORT_PATH"
  --domain "$DOMAIN"
  --source "$SEED_SOURCE"
)
if [[ -n "$STRICT_FLAG" ]]; then
  SEED_CMD+=("$STRICT_FLAG")
fi
if [[ ${#EXTRA_SEED_ARGS[@]} -gt 0 ]]; then
  SEED_CMD+=("${EXTRA_SEED_ARGS[@]}")
fi

JARVIS_IMPROVEMENT_FEED_TIMEOUT_SECONDS="$FEED_TIMEOUT_SECONDS" "${PULL_CMD[@]}"
"${LEADERBOARD_CMD[@]}"
"${SEED_CMD[@]}"

"$PYTHON_BIN" \
  - \
  "$SMOKE_SUMMARY_PATH" \
  "$DOMAIN" \
  "$CONFIG_PATH" \
  "$OUTPUT_DIR" \
  "$FEED_NAME" \
  "$INPUT_PATH" \
  "$LEADERBOARD_SOURCE" \
  "$LOOKBACK_DAYS" \
  "$MIN_CROSS_APP_COUNT" \
  "$SEED_ENTRY_SOURCE" \
  "$SEED_FALLBACK_ENTRY_SOURCE" \
  "$SEED_TRENDS" \
  "$SEED_LIMIT" \
  "$PULL_REPORT_PATH" \
  "$LEADERBOARD_REPORT_PATH" \
  "$SEED_REPORT_PATH" \
  <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

summary_path = Path(sys.argv[1]).expanduser().resolve()
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "status": "ok",
    "domain": str(sys.argv[2]),
    "config_path": str(sys.argv[3]),
    "output_dir": str(sys.argv[4]),
    "feed_name": str(sys.argv[5]),
    "input_path": str(sys.argv[6]),
    "leaderboard_source": str(sys.argv[7]),
    "lookback_days": int(float(sys.argv[8] or 0)),
    "min_cross_app_count": int(float(sys.argv[9] or 0)),
    "seed_entry_source": str(sys.argv[10]),
    "seed_fallback_entry_source": str(sys.argv[11]),
    "seed_trends": str(sys.argv[12]),
    "seed_limit": int(float(sys.argv[13] or 0)),
    "pull_report_path": str(sys.argv[14]),
    "leaderboard_report_path": str(sys.argv[15]),
    "seed_report_path": str(sys.argv[16]),
}
summary_path.parent.mkdir(parents=True, exist_ok=True)
summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(json.dumps(payload, indent=2))
PY
