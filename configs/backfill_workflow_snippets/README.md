# Backfill Workflow Snippets

This folder provides reusable automation snippets for running:

`python -m jarvis.cli plans backfill-project-signals`

with stable warning-policy defaults.

## Files

- `warning-policy-strict.json`
- `warning-policy-quiet.json`
- `github-actions-backfill-strict.yml`
- `github-actions-backfill-quiet.yml`
- `helpers/bridge_args.sh`
- `../../scripts/run_backfill_project_signals.sh`
- `../../scripts/export_backfill_warning_audit.py`
- `../../scripts/compare_backfill_policy_audits.py`
- `../../scripts/summarize_backfill_warning_audits.py`
- `../../scripts/prune_backfill_warning_audits.py`
- `../../scripts/build_backfill_warning_bridge.py`
- `../../scripts/publish_bridge_markdown.py`

## Recommended Usage

1. Pick a warning-policy JSON (`strict` or `quiet`).
2. Use the wrapper script for local cron, CI, or external schedulers.
3. For GitHub Actions, copy one of the `github-actions-*` templates into
   `.github/workflows/` and adjust `project_id`, `profile_key`, and schedule.
   The templates source `helpers/bridge_args.sh` to keep bridge-alert arg assembly shared.
4. Persist warning telemetry artifacts with:
   - `python ./scripts/export_backfill_warning_audit.py ...`
   - add `--export-profile minimal` to reduce artifact size for long-term retention
5. Compare two audit artifacts and optionally fail on drift:
   - `python ./scripts/compare_backfill_policy_audits.py before.json after.json`
   - add `--summary-only --json-compact` for alert/dashboard-friendly output
   - add `--projection-profile policy_core` for narrow policy-only contracts
6. For single-command daily ops bundles, emit summary + bridge together:
   - `python ./scripts/summarize_backfill_warning_audits.py ... --include-bridge --bridge-include-markdown`

For scheduled guardrails, prefer:

- `--compare-with-latest`
- `--enforce-stable-policy-source`
- `--enforce-stable-policy-checksum`
- `--export-profile minimal` for monitor-heavy lanes

Optional soft-noise policy guardrail mode:

- `--drift-projection-profile policy_core`
- `--enforce-stable-policy-core`
- keeps warning-code/severity/path churn as soft noise while still enforcing policy-core contract

For very high-frequency monitor lanes, also consider:

- `--minimal-warning-code-limit <N>` (or `0` for count-only)
- `--minimal-omit-signal-summary`
- `--minimal-omit-policy-drift-differences`

For pinned-baseline guardrails (strict template), set:

- `BACKFILL_POLICY_BASELINE_AUDIT=/absolute/path/to/gold-baseline.json`
- `BACKFILL_REQUIRE_BASELINE=true` (optional hard-fail if baseline is missing)

When set, strict jobs compare against that fixed baseline via `--baseline-audit`.

Template env toggles for policy-core guardrail mode:

- `BACKFILL_POLICY_DRIFT_PROJECTION_PROFILE=policy_core`
- `BACKFILL_ENFORCE_STABLE_POLICY_CORE=true`

Both strict/quiet templates now also emit bridge artifacts under:

- `output/backfill_warning_bridge/*.json`

## Policy Modes

- `strict`: warning/error hints can fail jobs (non-zero exit policy).
- `quiet`: suppresses capping-noise hints and keeps zero-exit monitoring.

## Audit Export

The export helper writes timestamped warning payloads to:

- `output/backfill_warning_audit/*.json`

Each export includes:

- warning status/count/codes
- warning policy provenance (`warning_policy_resolution`)
- command + exit-code audit metadata

To enforce stable policy resolution between runs, compare:

```bash
python ./scripts/compare_backfill_policy_audits.py \
  output/backfill_warning_audit/alpha_prev.json \
  output/backfill_warning_audit/alpha_now.json
```

Or enforce in one step during export:

```bash
python ./scripts/export_backfill_warning_audit.py \
  alpha \
  --policy-config ./configs/backfill_workflow_snippets/warning-policy-strict.json \
  --compare-with-latest \
  --enforce-stable-policy-source \
  --enforce-stable-policy-checksum
```

Lean minimal export example:

```bash
python ./scripts/export_backfill_warning_audit.py \
  alpha \
  --profile-key nightly \
  --preset quick \
  --policy-config ./configs/backfill_workflow_snippets/warning-policy-quiet.json \
  --compare-with-latest \
  --export-profile minimal \
  --minimal-warning-code-limit 0 \
  --minimal-omit-signal-summary \
  --minimal-omit-policy-drift-differences
```

Soft-noise policy-core guardrail example:

```bash
python ./scripts/export_backfill_warning_audit.py \
  alpha \
  --profile-key nightly \
  --preset balanced \
  --policy-config ./configs/backfill_workflow_snippets/warning-policy-strict.json \
  --compare-with-latest \
  --drift-projection-profile policy_core \
  --enforce-stable-policy-core
```

Daily rollup summary:

```bash
python ./scripts/summarize_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24
```

Compact multi-project dashboard rollup:

```bash
python ./scripts/summarize_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --json-compact
```

Dashboard rollup with threshold alerts:

```bash
python ./scripts/summarize_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --dashboard-alert-guardrail-triggered-count-threshold 3 \
  --dashboard-alert-policy-drift-changed-rate-threshold 0.20 \
  --dashboard-alert-project-guardrail-triggered-count-threshold 2 \
  --json-compact
```

Chat/inbox-ready daily bridge payload:

```bash
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --json-compact
```

Operator-facing markdown daily bridge briefing:

```bash
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --format markdown \
  --markdown-max-projects 20
```

Bridge gating thresholds (non-zero exit on bursts):

```bash
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --bridge-alert-policy-drift-count-threshold 3 \
  --bridge-alert-guardrail-rate-threshold 0.40 \
  --bridge-alert-exit-code 12 \
  --json-compact
```

When thresholds are set, output includes `alerts` and returns the configured
`--bridge-alert-exit-code` if any rule is triggered.

Bridge severity-tier mode (warn vs error by rule family):

```bash
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --bridge-alert-policy-drift-count-threshold 2 \
  --bridge-alert-policy-drift-severity warn \
  --bridge-alert-guardrail-count-threshold 1 \
  --bridge-alert-guardrail-severity error \
  --bridge-alert-exit-code 12 \
  --json-compact
```

In severity-tier mode, only triggered `error` rules cause non-zero exit; `warn`
rules are reported in payload/markdown but remain zero-exit.

Per-project severity override example (suppress noisy project lanes):

```bash
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --bridge-alert-policy-drift-count-threshold 2 \
  --bridge-alert-policy-drift-severity error \
  --bridge-alert-project-severity-override alpha=warn@policy_only \
  --bridge-alert-project-severity-override beta=warn@guardrail_only \
  --json-compact
```

Override format supports `project_id=warn|error` (or `project_id:warn|error`) and
can be repeated. Optional scope suffix is supported:

- `@policy_only`
- `@guardrail_only`
- `@both` (default)

Rule suppression example (ignore selected bridge rules in monitor lanes):

```bash
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --bridge-alert-policy-drift-count-threshold 2 \
  --bridge-alert-suppress-rule policy_drift_count_threshold \
  --bridge-alert-suppress-rule guardrail_rate_threshold \
  --json-compact
```

Suppressed triggered rules are retained in telemetry fields:

- `alerts.triggered_rules_raw`
- `alerts.suppressed_triggered_rules`
- `alerts.suppressed_rules_requested|applied|unused`

Scoped suppression example (match by project + family):

```bash
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --bridge-alert-policy-drift-count-threshold 2 \
  --bridge-alert-suppress-rule policy_drift_count_threshold \
  --bridge-alert-project-suppress-scope alpha@policy_only \
  --json-compact
```

Project suppression scopes:

- `project_id@policy_only`
- `project_id@guardrail_only`
- `project_id@both`

Single-command summary + bridge ops bundle:

```bash
python ./scripts/summarize_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --include-bridge \
  --bridge-projection-profile policy_core \
  --bridge-include-markdown \
  --bridge-markdown-max-projects 20 \
  --json-compact
```

When `--bridge-include-markdown` is enabled in bundle mode, output also includes
`bridge_markdown_telemetry` for structured drift/exit gating metadata.

Workflow template markdown publish toggles (optional downstream brief step):

- `BACKFILL_BRIDGE_MARKDOWN_PUBLISH=true|false` (default `false`)
- `BACKFILL_BRIDGE_MARKDOWN_DRY_RUN=true|false` (validates publish payload without posting)
- `BACKFILL_BRIDGE_MARKDOWN_DRY_RUN_OUTPUT_MODE=full|preview_only` (optional dry-run payload verbosity)
- `BACKFILL_BRIDGE_MARKDOWN_RETRY_ATTEMPTS=<N>`
- `BACKFILL_BRIDGE_MARKDOWN_RETRY_BACKOFF_SECONDS=<seconds>`
- `BACKFILL_BRIDGE_MARKDOWN_RETRY_BACKOFF_MULTIPLIER=<float>`
- `BACKFILL_BRIDGE_MARKDOWN_RETRY_MAX_BACKOFF_SECONDS=<seconds>`
- `BACKFILL_BRIDGE_MARKDOWN_RETRY_JITTER_SECONDS=<seconds>` (optional additive random jitter per retry)
- `BACKFILL_BRIDGE_MARKDOWN_RETRY_JITTER_SEED=<int>` (optional deterministic jitter seed)
- `BACKFILL_BRIDGE_MARKDOWN_RETRY_HTTP_STATUSES=429,500,502,503,504`
- `BACKFILL_BRIDGE_MARKDOWN_ERROR_BODY_PREVIEW_CHARS=<N>` (optional failed HTTP response body preview cap; `0` disables)
- `BACKFILL_BRIDGE_MARKDOWN_RETRY_DIAGNOSTICS_MODE=full|minimal` (optional retry attempt metadata verbosity)
- `BACKFILL_BRIDGE_MARKDOWN_ALERT_COMPACT=true|false` (optional compact alert digest mode)
- `BACKFILL_BRIDGE_MARKDOWN_HIDE_SUPPRESSION_SECTION=true|false` (optional hide suppression-focused markdown lines)
- `BACKFILL_BRIDGE_MARKDOWN_INCLUDE_FAMILY_PROJECTS=true|false` (optional include triggered family project listings)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_INCLUDE_COUNTS=true|false` (optional include per-family warn/error/all counts)
- `BACKFILL_BRIDGE_MARKDOWN_HIDE_EMPTY_FAMILY_PROJECTS=true|false` (optional hide zero-count family rows)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_MODE=full|counts_only` (optional family-project markdown rendering mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_SOURCE=triggered|all_current|triggered_or_current` (optional family project listing source)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_SEVERITY=all|warn_only|error_only` (optional family project severity filter)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_MAX_ITEMS=<N>` (optional per-family/per-severity project list cap; emits `(+N more)` markers)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_ORDER=alphabetical|severity_then_project` (optional family project ordering mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_ORDER=by_family|by_total_desc` (optional family project count ordering mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_RENDER_MODE=full_fields|nonzero_buckets` (optional family project count row rendering mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_VISIBILITY_MODE=all_rows|nonzero_all` (optional family project count row visibility mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_EXPORT_MODE=inline|table` (optional family project count export mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_STYLE=full|minimal` (optional family project count table style when export mode is `table`)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_EMPTY_MODE=inline_none|table_empty` (optional empty-row behavior when count export mode is `table`)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_FAMILY_LABEL_MODE=raw|title` (optional family-label formatting for count table rows)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_HEADER_LABEL_MODE=raw|title` (optional header-label formatting for count table output)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_FAMILY_LABEL_OVERRIDES=policy_only=...,guardrail_only=...,both=...` (optional table family-label overrides)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_METRIC_LABEL_MODE=raw|title` (optional warn/error/all header-label formatting for count table output)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_METRIC_LABEL_OVERRIDES=warn=...,error=...,all=...` (optional count-table metric header-label overrides)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_ROW_ORDER_MODE=count_order|canonical|sorted` (optional table row-order mode independent of count summary ordering)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_INCLUDE_SCHEMA_SIGNATURE=true|false` (optional include rendered table schema signature trace line)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_INLINE_FAMILY_LABEL_MODE=raw|title` (optional inline count-summary family label style)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_INLINE_BUCKET_LABEL_MODE=raw|title` (optional inline count-summary bucket label style)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS=true|false` (optional malformed override diagnostics line)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_SEVERITY=off|note|warn` (optional malformed override diagnostics severity signaling mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON=true|false` (optional machine-readable diagnostics JSON trace line)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_MODE=full|compact` (optional diagnostics JSON detail mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_KEY_PREFIX_MODE=bridge_|count_override_` (optional compact diagnostics JSON key-prefix mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_PROFILE=compact_min|compact_full` (optional compact diagnostics JSON profile preset)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_INCLUDE_MODE=none|counts_only|counts_plus_tokens|counts_plus_tokens_if_truncated` (optional compact diagnostics JSON malformed-token include mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SORT_MODE=input_order|lexicographic` (optional compact malformed-token sorting mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MAX_PER_SCOPE=<N>` (optional compact malformed-token max per scope; `0` keeps all)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_OVERFLOW_SUFFIX=+{omitted} more` (optional compact token-overflow suffix template)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_OVERFLOW_SUFFIX_MODE=include|suppress` (optional compact token-overflow suffix emission mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_OMITTED_COUNT_VISIBILITY_MODE=always|if_truncated_only|off` (optional compact omitted-count key visibility mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_LIST_GUARD_MODE=off|require_nonempty_tokens` (optional compact malformed-token list guard mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_LIST_KEY_MODE=always|if_nonempty|if_truncated` (optional compact malformed-token list key emission mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SCOPE_FALLBACK_MODE=selected_only|auto_expand_when_empty` (optional compact malformed-token scope fallback mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_TRUNCATION_INDICATOR_MODE=off|summary_only|per_scope` (optional compact truncation-indicator emission mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SCOPE_PRIORITY_MODE=family_first|metric_first` (optional compact scope-priority mode for auto-expand fallback ordering)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_FALLBACK_EMISSION_MODE=first_success_only|all_eligible` (optional compact fallback emission mode for auto-expand scope fallback)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_FALLBACK_SOURCE_MARKER_MODE=off|summary|per_scope` (optional compact fallback-source marker mode for strict sinks)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_FALLBACK_SOURCE_MARKER_ACTIVATION_MODE=always|fallback_only` (optional compact fallback-source marker activation mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SELECTED_SCOPE_MARKER_MODE=off|summary|per_scope` (optional compact selected-scope marker mode when fallback is bypassed)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_KEY_NAMING_MODE=default|short` (optional compact marker-key naming mode for strict sinks)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUPPRESSION_MODE=off|omit_when_no_token_payload` (optional compact marker suppression when no malformed-token list keys are emitted)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUMMARY_VISIBILITY_MODE=always|if_true_only` (optional compact boolean-marker visibility mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SCOPE_ORDER_MODE=canonical|priority` (optional compact per-scope marker key order mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_LIST_VISIBILITY_MODE=always|if_nonempty` (optional compact summary-list marker visibility mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_KEY_PREFIX_MODE=inherit|markers` (optional compact marker key-prefix mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_BOOLEAN_TYPE_VISIBILITY_MODE=all|fallback_only|selected_only|truncation_only|fallback_selected|fallback_truncation|selected_truncation|none` (optional compact boolean marker family visibility mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUMMARY_LIST_ORDER_MODE=insertion|lexicographic` (optional compact summary-list marker ordering mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUMMARY_FAMILY_VISIBILITY_MODE=all|fallback_only|selected_only|none` (optional compact summary-list marker family visibility mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PER_SCOPE_FAMILY_VISIBILITY_MODE=all|fallback_only|selected_only|truncation_only|none` (optional compact per-scope marker family visibility mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUMMARY_BOOLEAN_FAMILY_VISIBILITY_MODE=all|fallback_only|selected_only|truncation_only|none` (optional compact summary-boolean marker family visibility mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PROFILE_MODE=off|strict_minimal|strict_verbose|strict_debug` (optional compact marker profile shortcut mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PROFILE_SIGNATURE_EXPECTED=<64hex>` (optional expected marker-profile signature for drift checks)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PROFILE_SIGNATURE_MATCH_MODE=off|warn|strict` (optional marker-profile signature drift handling mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PROFILE_SIGNATURE_DRIFT_EXIT_CODE=<N>` (optional non-zero exit when strict marker-profile signature drift is detected; `0` disables)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PRECEDENCE_EXPORT_MODE=full|summary_only` (optional compact marker precedence export mode: per-control source keys vs condensed source-count summary)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SCOPE_MODE=family_only|metric_only|both` (optional compact malformed-token scope mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_DEDUP_MODE=off|on` (optional compact malformed-token dedup mode before sort/truncation)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_NORMALIZATION_MODE=preserve|lower` (optional compact malformed-token normalization mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SANITIZATION_MODE=off|ascii_safe` (optional compact malformed-token sanitization mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SANITIZATION_REPLACEMENT_CHAR=_` (optional compact sanitization replacement chars for non-ASCII/non-printable chars in `ascii_safe` mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MIN_LENGTH=<N>` (optional compact malformed-token min-length filter after normalization/sanitization)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_CI_POLICY_MODE=off|strict` (optional strict-pipeline recommendation mode for malformed override tokens)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_MIN_ALL=<N>` (optional minimum all-count for showing family count rows)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_THRESHOLD_MODE=off|all_min` (optional explicit count-threshold mode)
- `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TOP_N=<N>` (optional cap for family count rows in counts-only mode)
- `BACKFILL_BRIDGE_MARKDOWN_TRIGGERED_RULE_DETAIL_MAX=<N>` (optional cap on triggered-rule detail rows)
- `BACKFILL_BRIDGE_MARKDOWN_MAX_PROJECTS=<N>`
- `BACKFILL_BRIDGE_MARKDOWN_WEBHOOK_URL=https://...` (optional POST `{"text": "...markdown..."}`)
- `BACKFILL_BRIDGE_ALERT_PROJECT_SEVERITY_OVERRIDES=alpha=warn,beta=warn` (optional)
- `BACKFILL_BRIDGE_ALERT_PROJECT_SEVERITY_OVERRIDES=alpha=warn@policy_only,beta=warn@guardrail_only` (optional)
- `BACKFILL_BRIDGE_ALERT_SUPPRESS_RULES=policy_drift_count_threshold,guardrail_rate_threshold` (optional)
- `BACKFILL_BRIDGE_ALERT_PROJECT_SUPPRESS_SCOPES=alpha@policy_only,beta@guardrail_only` (optional)
- bridge markdown alert section now includes scoped suppression diagnostics (`suppressed/applied/unused` + per-rule scope matches)
- bridge markdown alert section also includes family grouping + digest counters:
  - `Triggered Rules By Family`
  - `Suppressed Triggered Rules By Family`
  - `Suppression Digest Counts`
- compact markdown alert mode suppresses verbose per-rule/per-scope lines while preserving digest counters (`--markdown-alert-compact`)
- suppression section can be fully hidden for ultra-compact digests (`--markdown-hide-suppression-section`)
- triggered family project listings can be enabled with `--markdown-include-family-projects`
- family project counts can be included with `--markdown-family-projects-include-counts`
- zero-count family rows can be hidden with `--markdown-family-projects-hide-empty-families`
- family project rendering mode can be set with `--markdown-family-projects-mode full|counts_only`
- family project listing source can be selected with `--markdown-family-projects-source triggered|all_current|triggered_or_current`
- family project listing severity can be filtered with `--markdown-family-projects-severity all|warn_only|error_only`
- family project list size can be capped with `--markdown-family-projects-max-items <N>`
- family project ordering can be set with `--markdown-family-projects-order alphabetical|severity_then_project`
- family project count ordering can be set with `--markdown-family-projects-count-order by_family|by_total_desc`
- family project count row rendering can be set with `--markdown-family-projects-count-render-mode full_fields|nonzero_buckets`
- family project count row visibility can be set with `--markdown-family-projects-count-visibility-mode all_rows|nonzero_all`
- family project count export mode can be set with `--markdown-family-projects-count-export-mode inline|table`
- family project count table style can be set with `--markdown-family-projects-count-table-style full|minimal`
- family project count table empty-row behavior can be set with `--markdown-family-projects-count-table-empty-mode inline_none|table_empty`
- family project count table family-label mode can be set with `--markdown-family-projects-count-table-family-label-mode raw|title`
- family project count table header-label mode can be set with `--markdown-family-projects-count-table-header-label-mode raw|title`
- family project count table family-label overrides can be set with `--markdown-family-projects-count-table-family-label-override policy_only=...,guardrail_only=...,both=...`
- family project count table metric-label mode can be set with `--markdown-family-projects-count-table-metric-label-mode raw|title`
- family project count table metric-label overrides can be set with `--markdown-family-projects-count-table-metric-label-override warn=...,error=...,all=...`
- family project count table row-order mode can be set with `--markdown-family-projects-count-table-row-order-mode count_order|canonical|sorted`
- family project count table schema signature trace can be enabled with `--markdown-family-projects-count-table-include-schema-signature`
- inline family count labels can be set with `--markdown-family-projects-count-inline-family-label-mode raw|title`
- inline bucket count labels can be set with `--markdown-family-projects-count-inline-bucket-label-mode raw|title`
- malformed table-override diagnostics can be enabled with `--markdown-family-projects-count-label-override-diagnostics`
- malformed table-override diagnostics severity can be set with `--markdown-family-projects-count-label-override-diagnostics-severity off|note|warn`
- machine-readable label-override diagnostics can be enabled with `--markdown-family-projects-count-label-override-diagnostics-json`
- family count rows can be filtered with `--markdown-family-projects-count-min-all <N>`
- count-threshold behavior can be controlled with `--markdown-family-projects-count-threshold-mode off|all_min`
- family count rows can be capped with `--markdown-family-projects-count-top-n <N>`
- when count capping is active in `counts_only` mode, markdown surfaces `Family Projects Count Rows` (`shown`, `total`, `omitted`)
- triggered rule detail rows can be capped with `--markdown-triggered-rule-detail-max <N>`

Dry-run publish helper example:

```bash
python ./scripts/publish_bridge_markdown.py \
  --markdown-path output/backfill_warning_bridge/bridge_quiet.md \
  --webhook-url "https://example.invalid/webhook" \
  --dry-run \
  --dry-run-output-mode preview_only \
  --json-compact
```

Retry/backoff publish helper example (non-dry-run):

```bash
python ./scripts/publish_bridge_markdown.py \
  --markdown-path output/backfill_warning_bridge/bridge_strict.md \
  --webhook-url "https://example.invalid/webhook" \
  --retry-attempts 3 \
  --retry-backoff-seconds 1.0 \
  --retry-backoff-multiplier 2.0 \
  --retry-max-backoff-seconds 10.0 \
  --retry-jitter-seconds 0.25 \
  --retry-jitter-seed 7 \
  --retry-on-http-status 429,500,502,503,504 \
  --error-body-preview-chars 120 \
  --json-compact
```

Publish helper retry output includes:

- top-level timeline fields (`first_attempt_started_at`, `last_attempt_finished_at`)
- per-attempt timing (`started_at`, `finished_at`, `elapsed_ms`, `next_attempt_at`)
- optional failed-response previews (`last_error_body_preview`, per-attempt `error_body_preview`)
- `--retry-diagnostics-mode minimal` to emit compact per-attempt records for smaller artifacts
- `--dry-run-output-mode preview_only` to emit compact preview-only dry-run payloads

Retention prune (dry-run by default):

```bash
python ./scripts/prune_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --keep-per-project 200 \
  --max-age-hours 720
```

Apply deletions:

```bash
python ./scripts/prune_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --keep-per-project 200 \
  --max-age-hours 720 \
  --execute
```

## Notes

- These are templates and do not auto-run from this folder.
- CLI flag precedence still applies:
  - explicit CLI flags
  - explicit `--warning-policy-config`
  - repo default policy file (`.jarvis/backfill.warning_policy.json`)
  - env defaults
  - profile defaults
