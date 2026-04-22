#!/usr/bin/env bash

backfill_bridge_append_csv_args() {
  local -n out_ref="$1"
  local flag="$2"
  local raw="${3:-}"
  if [[ -z "${raw}" ]]; then
    return
  fi
  IFS=',' read -r -a entries <<< "${raw}"
  for entry in "${entries[@]}"; do
    local cleaned
    cleaned="$(echo "${entry}" | xargs)"
    if [[ -n "${cleaned}" ]]; then
      out_ref+=("${flag}" "${cleaned}")
    fi
  done
}

build_backfill_bridge_alert_args() {
  local -n out_ref="$1"
  out_ref=()

  if [[ -n "${BACKFILL_BRIDGE_ALERT_POLICY_DRIFT_COUNT_THRESHOLD:-}" ]]; then
    out_ref+=(--bridge-alert-policy-drift-count-threshold "${BACKFILL_BRIDGE_ALERT_POLICY_DRIFT_COUNT_THRESHOLD}")
  fi
  if [[ -n "${BACKFILL_BRIDGE_ALERT_POLICY_DRIFT_RATE_THRESHOLD:-}" ]]; then
    out_ref+=(--bridge-alert-policy-drift-rate-threshold "${BACKFILL_BRIDGE_ALERT_POLICY_DRIFT_RATE_THRESHOLD}")
  fi
  if [[ -n "${BACKFILL_BRIDGE_ALERT_GUARDRAIL_COUNT_THRESHOLD:-}" ]]; then
    out_ref+=(--bridge-alert-guardrail-count-threshold "${BACKFILL_BRIDGE_ALERT_GUARDRAIL_COUNT_THRESHOLD}")
  fi
  if [[ -n "${BACKFILL_BRIDGE_ALERT_GUARDRAIL_RATE_THRESHOLD:-}" ]]; then
    out_ref+=(--bridge-alert-guardrail-rate-threshold "${BACKFILL_BRIDGE_ALERT_GUARDRAIL_RATE_THRESHOLD}")
  fi
  if [[ -n "${BACKFILL_BRIDGE_ALERT_POLICY_DRIFT_SEVERITY:-}" ]]; then
    out_ref+=(--bridge-alert-policy-drift-severity "${BACKFILL_BRIDGE_ALERT_POLICY_DRIFT_SEVERITY}")
  fi
  if [[ -n "${BACKFILL_BRIDGE_ALERT_GUARDRAIL_SEVERITY:-}" ]]; then
    out_ref+=(--bridge-alert-guardrail-severity "${BACKFILL_BRIDGE_ALERT_GUARDRAIL_SEVERITY}")
  fi
  backfill_bridge_append_csv_args out_ref --bridge-alert-project-severity-override "${BACKFILL_BRIDGE_ALERT_PROJECT_SEVERITY_OVERRIDES:-}"
  backfill_bridge_append_csv_args out_ref --bridge-alert-suppress-rule "${BACKFILL_BRIDGE_ALERT_SUPPRESS_RULES:-}"
  backfill_bridge_append_csv_args out_ref --bridge-alert-project-suppress-scope "${BACKFILL_BRIDGE_ALERT_PROJECT_SUPPRESS_SCOPES:-}"
  if [[ -n "${BACKFILL_BRIDGE_ALERT_EXIT_CODE:-}" ]]; then
    out_ref+=(--bridge-alert-exit-code "${BACKFILL_BRIDGE_ALERT_EXIT_CODE}")
  fi
}
