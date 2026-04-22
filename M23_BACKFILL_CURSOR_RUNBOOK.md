# M23 Backfill Cursor Runbook

This runbook is the operator path for project-signal backfills using cursor profiles.

It covers:

1. Preview effective cursor inputs
2. Dry-run candidate signals
3. Execute and persist cursor movement
4. Read compact run summary
5. Re-run incrementally
6. Use the CLI wrapper with presets
7. Build a single-command daily ops bundle (summary + bridge)

## Preconditions

1. Use a real repo path and an existing JARVIS DB:
```bash
export JARVIS_REPO_PATH="/absolute/path/to/repo"
export JARVIS_DB_PATH="/absolute/path/to/repo/.jarvis/jarvis.db"
```
2. Run from project root (`/Users/dankerbadge/Documents/J.A.R.V.I.S`) so imports resolve.

## Step 1: Preview Effective Cursor Inputs

Use this when you want to verify which `since_*` values will be used before ingest.

```bash
.venv-voice/bin/python - <<'PY'
from pathlib import Path
from jarvis.runtime import JarvisRuntime

runtime = JarvisRuntime(
    db_path=Path("/absolute/path/to/repo/.jarvis/jarvis.db"),
    repo_path=Path("/absolute/path/to/repo"),
)
try:
    preview = runtime.preview_project_backfill_cursor_inputs(
        project_id="alpha",
        load_since_from_cursor_profile=True,
        cursor_profile_key="nightly",
    )
    print(preview)
finally:
    runtime.close()
PY
```

Check `cursor_profile.resolution_source` to see if each source was resolved from:

- `explicit`
- `profile.source`
- `profile.global`
- `global.explicit`
- `global.profile`
- `none`

## Step 2: Dry-Run Candidate Signals (No Ingest, No Cursor Persist)

Use dry-run to inspect candidate signals and counts safely.

```bash
.venv-voice/bin/python - <<'PY'
from pathlib import Path
from jarvis.runtime import JarvisRuntime

runtime = JarvisRuntime(
    db_path=Path("/absolute/path/to/repo/.jarvis/jarvis.db"),
    repo_path=Path("/absolute/path/to/repo"),
)
try:
    run = runtime.run_project_backfill_with_cursor_profile_summary(
        project_id="alpha",
        profile_key="nightly",
        actor="operator",
        include_outcomes=True,
        include_review_artifacts=True,
        include_merge_outcomes=True,
        dry_run=True,
        top_signal_types=5,
    )
    print(run["summary"])
finally:
    runtime.close()
PY
```

Expected dry-run behavior:

- `dry_run: true`
- `cursor_persisted: false`
- `persisted_marker_count: 0`
- `would_ingest_count` shows how many signals would be ingested on a real run
- `candidate_pool_count` shows total deduped candidates before scan-cap
- `candidate_scan_limit` and `candidate_unscanned_count` show bounded-scan effects

If you need raw sampling block details, inspect `run["backfill"]["sampling"]`.

## Step 3: Execute Real Backfill + Persist Cursors

Run with `dry_run=False` (default) to ingest and persist updated cursors.

```bash
.venv-voice/bin/python - <<'PY'
from pathlib import Path
from jarvis.runtime import JarvisRuntime

runtime = JarvisRuntime(
    db_path=Path("/absolute/path/to/repo/.jarvis/jarvis.db"),
    repo_path=Path("/absolute/path/to/repo"),
)
try:
    run = runtime.run_project_backfill_with_cursor_profile_summary(
        project_id="alpha",
        profile_key="nightly",
        actor="operator",
        include_outcomes=True,
        include_review_artifacts=True,
        include_merge_outcomes=True,
        top_signal_types=5,
    )
    print(run["summary"])
finally:
    runtime.close()
PY
```

Expected real-run behavior:

- `dry_run: false`
- `cursor_persisted: true`
- `persisted_marker_count >= 0`
- cursor profile reflects new `next_since` values after completion

## Step 4: Read Compact Summary Output

`summary` is intentionally compact for fast operator checks:

- Counts:
`signals_count`, `skipped_existing_count`, `would_ingest_count`, `persisted_marker_count`
- Aggregates:
`source_counts`, `signal_type_counts`, `top_signal_types`
- Aggregate metadata:
`source_counts_metadata`, `signal_type_counts_metadata`
- Cursor movement:
`cursor_movement.global|plan_outcomes|review_artifacts|merge_outcomes`
- Candidate-pool diagnostics:
`candidate_pool_count`, `candidate_scan_limit`, `candidate_scanned_count`, `candidate_unscanned_count`
- Per-source candidate diagnostics:
`candidate_pool_by_source`, `candidate_scanned_by_source`, `candidate_unscanned_by_source`

By default, `run_project_backfill_with_cursor_profile_summary(...)` returns a compact backfill payload
with raw `backfill.signals` and `backfill.ingestions` omitted:

- `backfill.signals` is `[]`
- `backfill.signals_omitted: true`
- `backfill.signals_omitted_count` reports omitted row count
- `backfill.ingestions` is `[]`
- `backfill.ingestions_omitted: true`
- `backfill.ingestions_omitted_count` reports omitted ingestion count

If you need raw rows in the response:

- set `include_raw_signals=True` for full signal payloads
- set `include_raw_ingestions=True` for full ingestion payloads

Use per-source diagnostics to tune source-level inclusion and limits:

- if one source dominates `candidate_pool_by_source`, reduce source scope in that run
- compare `candidate_pool_by_source` vs `candidate_scanned_by_source` to detect scan-cap clipping

Optional summary caps for high-cardinality runs:

- `max_source_counts` caps `summary.source_counts` keys
- `max_signal_type_counts` caps `summary.signal_type_counts` keys
- use `*_metadata` fields to track `total_keys`, `returned_keys`, and `omitted_keys`

Each cursor movement record includes:

- `from`
- `to`
- `after`
- `changed`
- `persisted`

In dry-run, `to` can differ from `after` because nothing is persisted.

## Step 5: Repeat Incremental Runs

After the first real run, call the same summary helper again with the same
`project_id + profile_key`. The stored cursor profile keeps incremental windows bounded.

## Step 6: CLI Wrapper (Preset + Safe Defaults)

Use the CLI wrapper when you want a single operator command path:

```bash
python3 -m jarvis.cli plans backfill-project-signals alpha \
  --profile-key nightly \
  --preset balanced \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path "$JARVIS_DB_PATH"
```

Behavior:

- defaults to dry-run (`--execute` is required for persistence)
- prints resolved preset/options and full run result
- returns compact payloads by default (raw signals/ingestions omitted)

Preset options:

- `quick`: outcomes-only fast pass (`limit=50`)
- `balanced`: outcomes + review + merge (`limit=100`) default
- `deep`: broader window (`limit=300`, `top_signal_types=10`)

Execute mode (persist markers/cursors):

```bash
python3 -m jarvis.cli plans backfill-project-signals alpha \
  --profile-key nightly \
  --preset balanced \
  --execute \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path "$JARVIS_DB_PATH"
```

Raw payload opt-ins:

- `--include-raw-signals`
- `--include-raw-ingestions`

Summary aggregate caps:

- `--max-source-counts <N>`
- `--max-signal-type-counts <N>`

CLI output modes:

- `--summary-only` to emit a summary-focused result payload
- `--json-compact` to emit minified single-line JSON

## Step 7: Single-Command Daily Ops Bundle (Summary + Bridge)

Use this when you want one payload for dashboards/chat pipelines that includes:

- dashboard rollup summary
- bridge delta payload
- optional markdown bridge briefing
- optional `bridge_markdown_telemetry` (when markdown bundle output is enabled)

```bash
python3 ./scripts/summarize_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --include-bridge \
  --bridge-projection-profile policy_core \
  --bridge-include-markdown \
  --bridge-markdown-max-projects 20 \
  --json-compact
```

Optional bridge severity tiers in bundle mode:

```bash
python3 ./scripts/summarize_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --include-bridge \
  --bridge-alert-policy-drift-count-threshold 2 \
  --bridge-alert-policy-drift-severity warn \
  --bridge-alert-guardrail-count-threshold 1 \
  --bridge-alert-guardrail-severity error \
  --json-compact
```

Severity behavior:

- `warn` rules are surfaced in `bridge.alerts` but do not cause bridge exit
- `error` rules are surfaced and mark `bridge.alerts.exit_triggered=true`

Per-project severity override pattern (noisy project suppression):

```bash
python3 ./scripts/summarize_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --include-bridge \
  --bridge-alert-policy-drift-count-threshold 2 \
  --bridge-alert-policy-drift-severity error \
  --bridge-alert-project-severity-override alpha=warn@policy_only \
  --bridge-alert-project-severity-override beta=warn@guardrail_only \
  --json-compact
```

Overrides apply to bridge alert families and can downgrade specific projects to
warn while keeping global default severity at error. Scope suffixes:

- `@policy_only`
- `@guardrail_only`
- `@both` (default)

Rule suppression pattern (monitor lane noise control):

```bash
python3 ./scripts/summarize_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --include-bridge \
  --bridge-alert-policy-drift-count-threshold 2 \
  --bridge-alert-suppress-rule policy_drift_count_threshold \
  --json-compact
```

Suppressed rules do not trigger bridge exits, but are still visible in:

- `bridge.alerts.triggered_rules_raw`
- `bridge.alerts.suppressed_triggered_rules`

Scoped suppression pattern (suppress only when project/family matches):

```bash
python3 ./scripts/summarize_backfill_warning_audits.py \
  --input-dir output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --include-bridge \
  --bridge-alert-policy-drift-count-threshold 2 \
  --bridge-alert-suppress-rule policy_drift_count_threshold \
  --bridge-alert-project-suppress-scope alpha@policy_only \
  --json-compact
```

Project suppression scopes:

- `@policy_only`
- `@guardrail_only`
- `@both`

Markdown bridge briefing now surfaces scoped suppression triage fields:

- `Suppressed Triggered Rules`
- `Project Suppression Scopes` (+ applied/unused)
- `Triggered Rule Detail` rows with `suppressed` and `scope_matched` markers
- `Triggered Rules By Family` (`policy_only`/`guardrail_only`/`both`)
- `Suppressed Triggered Rules By Family`
- `Suppression Digest Counts`
- optional `Triggered Family Projects` listing via `--markdown-include-family-projects`
- optional family-project count summary via `--markdown-family-projects-include-counts`
- optional hide-empty family rows via `--markdown-family-projects-hide-empty-families`
- optional family-project rendering mode via `--markdown-family-projects-mode full|counts_only`
- optional family-project source selector via `--markdown-family-projects-source triggered|all_current|triggered_or_current`
- optional family-project severity selector via `--markdown-family-projects-severity all|warn_only|error_only`
- optional family-project list cap via `--markdown-family-projects-max-items <N>`
- optional family-project ordering via `--markdown-family-projects-order alphabetical|severity_then_project`
- optional family-project count ordering via `--markdown-family-projects-count-order by_family|by_total_desc`
- optional family-project count rendering mode via `--markdown-family-projects-count-render-mode full_fields|nonzero_buckets`
- optional family-project count visibility mode via `--markdown-family-projects-count-visibility-mode all_rows|nonzero_all`
- optional family-project count export mode via `--markdown-family-projects-count-export-mode inline|table`
- optional family-project count table style via `--markdown-family-projects-count-table-style full|minimal`
- optional family-project count table empty-row behavior via `--markdown-family-projects-count-table-empty-mode inline_none|table_empty`
- optional family-project count table family-label mode via `--markdown-family-projects-count-table-family-label-mode raw|title`
- optional family-project count table header-label mode via `--markdown-family-projects-count-table-header-label-mode raw|title`
- optional family-project count table family-label overrides via `--markdown-family-projects-count-table-family-label-override policy_only=...,guardrail_only=...,both=...`
- optional family-project count table metric-label mode via `--markdown-family-projects-count-table-metric-label-mode raw|title`
- optional family-project count table metric-label overrides via `--markdown-family-projects-count-table-metric-label-override warn=...,error=...,all=...`
- optional family-project count table row-order mode via `--markdown-family-projects-count-table-row-order-mode count_order|canonical|sorted`
- optional family-project count table schema signature trace via `--markdown-family-projects-count-table-include-schema-signature`
- optional inline family count label mode via `--markdown-family-projects-count-inline-family-label-mode raw|title`
- optional inline bucket count label mode via `--markdown-family-projects-count-inline-bucket-label-mode raw|title`
- optional malformed table-override diagnostics via `--markdown-family-projects-count-label-override-diagnostics`
- optional malformed table-override diagnostics severity via `--markdown-family-projects-count-label-override-diagnostics-severity off|note|warn`
- optional machine-readable label-override diagnostics via `--markdown-family-projects-count-label-override-diagnostics-json`
- optional label-override diagnostics JSON mode via `--markdown-family-projects-count-label-override-diagnostics-json-mode full|compact`
- optional compact diagnostics JSON key-prefix mode via `--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode bridge_|count_override_`
- optional compact diagnostics JSON profile preset via `--markdown-family-projects-count-label-override-diagnostics-json-compact-profile compact_min|compact_full`
- optional compact diagnostics malformed-token include mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode none|counts_only|counts_plus_tokens|counts_plus_tokens_if_truncated`
- optional compact diagnostics malformed-token sorting mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sort-mode input_order|lexicographic`
- optional compact diagnostics malformed-token max per scope via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-max-per-scope <N>`
- optional compact diagnostics malformed-token overflow suffix via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix "+{omitted} more"`
- optional compact diagnostics malformed-token overflow-suffix mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix-mode include|suppress`
- optional compact diagnostics omitted-count visibility mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode always|if_truncated_only|off`
- optional compact diagnostics malformed-token list guard mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode off|require_nonempty_tokens`
- optional compact diagnostics malformed-token list key mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode always|if_nonempty|if_truncated`
- optional compact diagnostics malformed-token scope fallback mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode selected_only|auto_expand_when_empty`
- optional compact diagnostics malformed-token truncation-indicator mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode off|summary_only|per_scope`
- optional compact diagnostics malformed-token scope-priority mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode family_first|metric_first`
- optional compact diagnostics malformed-token fallback-emission mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-emission-mode first_success_only|all_eligible`
- optional compact diagnostics malformed-token fallback-source-marker mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode off|summary|per_scope`
- optional compact diagnostics fallback-source marker activation mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode always|fallback_only`
- optional compact diagnostics selected-scope marker mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode off|summary|per_scope`
- optional compact diagnostics marker-key naming mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode default|short`
- optional compact diagnostics marker suppression mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode off|omit_when_no_token_payload`
- optional compact diagnostics boolean-marker visibility mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-visibility-mode always|if_true_only`
- optional compact diagnostics per-scope marker key-order mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-scope-order-mode canonical|priority`
- optional compact diagnostics summary-list marker visibility mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-list-visibility-mode always|if_nonempty`
- optional compact diagnostics marker key-prefix mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode inherit|markers`
- optional compact diagnostics boolean marker-family visibility mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode all|fallback_only|selected_only|truncation_only|fallback_selected|fallback_truncation|selected_truncation|none`
- optional compact diagnostics summary-list marker order mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode insertion|lexicographic`
- optional compact diagnostics summary-list marker family visibility mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-family-visibility-mode all|fallback_only|selected_only|none`
- optional compact diagnostics per-scope marker family visibility mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-per-scope-family-visibility-mode all|fallback_only|selected_only|truncation_only|none`
- optional compact diagnostics summary-boolean marker family visibility mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-boolean-family-visibility-mode all|fallback_only|selected_only|truncation_only|none`
- optional compact diagnostics marker profile shortcut mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode off|strict_minimal|strict_verbose|strict_debug`
- optional compact diagnostics marker-profile signature expected value via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-expected <64hex>`
- optional compact diagnostics marker-profile signature match mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-match-mode off|warn|strict`
- optional strict signature-drift non-zero exit via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-drift-exit-code <N>` (`0` disables)
- optional compact diagnostics marker precedence export mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-precedence-export-mode full|summary_only`
- optional compact diagnostics malformed-token scope mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode family_only|metric_only|both`
- optional compact diagnostics malformed-token dedup mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-dedup-mode off|on`
- optional compact diagnostics malformed-token normalization mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-normalization-mode preserve|lower`
- optional compact diagnostics malformed-token sanitization mode via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-mode off|ascii_safe`
- optional compact diagnostics sanitization replacement chars via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-replacement-char "_"` (effective in `ascii_safe` mode)
- optional compact diagnostics malformed-token min-length filter via `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-min-length <N>`
- optional strict CI recommendation signaling via `--markdown-family-projects-count-label-override-ci-policy-mode off|strict`
- family-project count markdown now includes compact diagnostics counters (`resolved_family_count`, `resolved_metric_count`, `family_malformed_count`, `metric_malformed_count`)
- optional family-project count visibility threshold via `--markdown-family-projects-count-min-all <N>`
- optional family-project count threshold mode via `--markdown-family-projects-count-threshold-mode off|all_min`
- optional family-project count cap via `--markdown-family-projects-count-top-n <N>`
- when count cap is active, markdown includes `Family Projects Count Rows` (`shown`, `total`, `omitted`)

Markdown publish webhook dry-run validation:

```bash
python3 ./scripts/publish_bridge_markdown.py \
  --markdown-path output/backfill_warning_bridge/bridge_quiet.md \
  --webhook-url "https://example.invalid/webhook" \
  --dry-run \
  --dry-run-output-mode preview_only \
  --json-compact
```

Expected dry-run behavior:

- `status: "ok"`
- `dry_run: true`
- `dry_run_output_mode: "preview_only"` (when selected)
- `would_post: true` when webhook URL is present
- `posted: false` (no network request performed)

Markdown publish retry/backoff validation (non-dry-run):

```bash
python3 ./scripts/publish_bridge_markdown.py \
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
  --retry-diagnostics-mode full \
  --json-compact
```

Expected retry metadata fields:

- `retry_policy`
- `attempt_count`
- `retries_attempted`
- `retry_scheduled_count`
- `first_attempt_started_at`
- `last_attempt_finished_at`
- `last_error_body_preview` (when enabled and failure response body is available)
- `attempts[]` with per-attempt `started_at`, `finished_at`, `elapsed_ms`, `next_attempt_at`, `will_retry`, `base_backoff_seconds`, `jitter_seconds`, `backoff_seconds`, and optional `error_body_preview`
- `retry_diagnostics_mode` (`full` or `minimal`; `minimal` trims per-attempt payload fields)
- `--output pretty` for human-readable non-JSON terminal output
- `--output warnings` for automation-friendly warning payloads only
- `--output policy` for compact warning-policy provenance + status JSON
- `--color auto|always|never` for pretty-mode ANSI color behavior

Operator warning hints:

- CLI output now includes:
  - `operator_hints_count`
  - `operator_hints[]`
- emitted warning codes:
  - `candidate_scan_clipped`
  - `source_counts_capped`
  - `signal_type_counts_capped`
- when scan clipping is detected, recommendations are preset-aware (for example `quick` -> suggest `--preset balanced`).
- suppress specific warning codes with repeatable:
  - `--suppress-warning-code <code>`
- set warning-policy defaults with:
  - `--warning-policy-config /abs/path/warning-policy.json`
  - `--warning-policy-profile default|strict|quiet`
  - `default`: include `info+`, no warning-based exit
  - `strict`: include `warning+`, exit non-zero on warning/error
  - `quiet`: include `warning+`, no warning-based exit, suppresses capping noise
- explicit warning flags override profile defaults:
  - `--min-warning-severity ...`
  - `--exit-code-policy ...`
  - `--warning-exit-code ...`
  - `--error-exit-code ...`
- unattended env defaults:
  - `JARVIS_BACKFILL_WARNING_POLICY_PROFILE=default|strict|quiet`
  - `JARVIS_BACKFILL_SUPPRESS_WARNING_CODES=code_a,code_b,code_c`
  - `JARVIS_BACKFILL_MIN_WARNING_SEVERITY=info|warning|error`
  - `JARVIS_BACKFILL_EXIT_CODE_POLICY=off|warning|error`
  - `JARVIS_BACKFILL_WARNING_EXIT_CODE=<N>`
  - `JARVIS_BACKFILL_ERROR_EXIT_CODE=<N>`

warning-policy config file schema (JSON object):

```json
{
  "warning_policy_profile": "strict",
  "suppress_warning_codes": ["source_counts_capped", "signal_type_counts_capped"],
  "min_warning_severity": "warning",
  "exit_code_policy": "warning",
  "warning_exit_code": 2,
  "error_exit_code": 3
}
```

repo-level default config discovery:

- if `--warning-policy-config` is omitted, CLI auto-loads first existing file:
  - `<repo>/.jarvis/backfill.warning_policy.json`
  - `<repo>/.jarvis/backfill_warning_policy.json`

resolution precedence (highest to lowest):

1. explicit CLI flags (`--min-warning-severity`, `--exit-code-policy`, exit codes, suppression codes)
2. explicit `--warning-policy-config` file
3. repo default config file (`.jarvis/backfill.warning_policy.json`)
4. env defaults (`JARVIS_BACKFILL_*`)
5. selected warning-policy profile defaults

Reusable scheduled templates:

- `configs/backfill_workflow_snippets/README.md`
- `configs/backfill_workflow_snippets/github-actions-backfill-strict.yml`
- `configs/backfill_workflow_snippets/github-actions-backfill-quiet.yml`
- `configs/backfill_workflow_snippets/helpers/bridge_args.sh`
- `scripts/run_backfill_project_signals.sh`
- `scripts/export_backfill_warning_audit.py`
- `scripts/compare_backfill_policy_audits.py`
- `scripts/summarize_backfill_warning_audits.py`
- `scripts/prune_backfill_warning_audits.py`
- `scripts/build_backfill_warning_bridge.py`

wrapper script examples:

```bash
# strict execute run
./scripts/run_backfill_project_signals.sh \
  alpha nightly balanced \
  --execute \
  --policy-config ./configs/backfill_workflow_snippets/warning-policy-strict.json

# quiet monitor run
./scripts/run_backfill_project_signals.sh \
  alpha nightly quick \
  --policy-config ./configs/backfill_workflow_snippets/warning-policy-quiet.json

# export timestamped warning audit artifact
python ./scripts/export_backfill_warning_audit.py \
  alpha \
  --profile-key nightly \
  --preset quick \
  --policy-config ./configs/backfill_workflow_snippets/warning-policy-quiet.json \
  --export-profile minimal

# high-frequency minimal export (count-first, low storage footprint)
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

# enforce strict drift guardrails against latest prior audit
python ./scripts/export_backfill_warning_audit.py \
  alpha \
  --profile-key nightly \
  --preset balanced \
  --policy-config ./configs/backfill_workflow_snippets/warning-policy-strict.json \
  --compare-with-latest \
  --enforce-stable-policy-source \
  --enforce-stable-policy-checksum

# enforce policy-core contract while treating warning-code/severity churn as soft noise
python ./scripts/export_backfill_warning_audit.py \
  alpha \
  --profile-key nightly \
  --preset balanced \
  --policy-config ./configs/backfill_workflow_snippets/warning-policy-strict.json \
  --compare-with-latest \
  --drift-projection-profile policy_core \
  --enforce-stable-policy-core

# enforce strict drift guardrails against a pinned baseline artifact
python ./scripts/export_backfill_warning_audit.py \
  alpha \
  --profile-key nightly \
  --preset balanced \
  --policy-config ./configs/backfill_workflow_snippets/warning-policy-strict.json \
  --baseline-audit ./output/backfill_warning_audit/alpha_gold_baseline.json \
  --require-baseline \
  --enforce-stable-policy-source \
  --enforce-stable-policy-checksum

# compare policy drift between two audit artifacts
python ./scripts/compare_backfill_policy_audits.py \
  ./output/backfill_warning_audit/alpha_prev.json \
  ./output/backfill_warning_audit/alpha_now.json

# compact drift triage output
python ./scripts/compare_backfill_policy_audits.py \
  ./output/backfill_warning_audit/alpha_prev.json \
  ./output/backfill_warning_audit/alpha_now.json \
  --summary-only \
  --json-compact

# narrow policy-core contract compare (ignores warning-code/severity noise)
python ./scripts/compare_backfill_policy_audits.py \
  ./output/backfill_warning_audit/alpha_prev.json \
  ./output/backfill_warning_audit/alpha_now.json \
  --projection-profile policy_core

# summarize recent audit artifacts (last 24h)
python ./scripts/summarize_backfill_warning_audits.py \
  --input-dir ./output/backfill_warning_audit \
  --since-hours 24

# compact multi-project dashboard rollup
python ./scripts/summarize_backfill_warning_audits.py \
  --input-dir ./output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --json-compact

# dashboard rollup with threshold alerts
python ./scripts/summarize_backfill_warning_audits.py \
  --input-dir ./output/backfill_warning_audit \
  --since-hours 24 \
  --rollup-mode dashboard \
  --dashboard-alert-guardrail-triggered-count-threshold 3 \
  --dashboard-alert-policy-drift-changed-rate-threshold 0.20 \
  --dashboard-alert-project-guardrail-triggered-count-threshold 2 \
  --json-compact

# chat/inbox-ready bridge payload (latest + delta per project)
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir ./output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --json-compact

# operator markdown briefing from bridge payload
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir ./output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --format markdown \
  --markdown-max-projects 20

# compact alert markdown (digest-focused, lower noise)
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir ./output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --format markdown \
  --markdown-alert-compact \
  --markdown-hide-suppression-section \
  --markdown-include-family-projects \
  --markdown-family-projects-include-counts \
  --markdown-family-projects-hide-empty-families \
  --markdown-family-projects-mode full \
  --markdown-family-projects-source all_current \
  --markdown-family-projects-severity all \
  --markdown-family-projects-max-items 25 \
  --markdown-family-projects-order severity_then_project \
  --markdown-family-projects-count-order by_family \
  --markdown-family-projects-count-min-all 0 \
  --markdown-family-projects-count-threshold-mode off \
  --markdown-family-projects-count-top-n 0 \
  --markdown-triggered-rule-detail-max 0

# bridge gating thresholds (returns non-zero on drift bursts)
python ./scripts/build_backfill_warning_bridge.py \
  --input-dir ./output/backfill_warning_audit \
  --since-hours 24 \
  --projection-profile policy_core \
  --bridge-alert-policy-drift-count-threshold 3 \
  --bridge-alert-guardrail-rate-threshold 0.40 \
  --bridge-alert-exit-code 12 \
  --json-compact

# prune old artifacts (dry-run first)
python ./scripts/prune_backfill_warning_audits.py \
  --input-dir ./output/backfill_warning_audit \
  --keep-per-project 200 \
  --max-age-hours 720
```

precedence debugging quick-check:

1. run with `--output warnings --summary-only --json-compact`.
2. inspect:
   - `warning_policy_profile`
   - `warning_policy_checksum`
   - `warning_policy_config_path`
   - `warning_policy_config_source`
   - `warning_policy_resolution.has_fallbacks`
   - `warning_policy_resolution.fallbacks`
   - `warning_suppression`
   - `warning_severity_filter`
3. force deterministic resolution if needed:
   - `--warning-policy-config /absolute/path/to/policy.json`
   - explicit warning flags (`--min-warning-severity`, `--exit-code-policy`, exit codes)
- enforce minimum hint severity:
  - `--min-warning-severity info|warning|error`
- optional automation exit policy:
  - `--exit-code-policy off|warning|error`
  - `--warning-exit-code <N>`
  - `--error-exit-code <N>`

CI profile examples:

- strict gate (fail CI on warning/error):
```bash
export JARVIS_BACKFILL_WARNING_POLICY_PROFILE=strict
export JARVIS_BACKFILL_EXIT_CODE_POLICY=warning
export JARVIS_BACKFILL_WARNING_EXIT_CODE=2
export JARVIS_BACKFILL_ERROR_EXIT_CODE=3

python3 -m jarvis.cli plans backfill-project-signals alpha \
  --profile-key nightly \
  --preset balanced \
  --summary-only \
  --output warnings \
  --json-compact \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path "$JARVIS_DB_PATH"
```

- quiet drift monitor (emit warnings payload but avoid capping noise + non-zero exits):
```bash
export JARVIS_BACKFILL_WARNING_POLICY_PROFILE=quiet
export JARVIS_BACKFILL_EXIT_CODE_POLICY=off
export JARVIS_BACKFILL_SUPPRESS_WARNING_CODES=source_counts_capped,signal_type_counts_capped

python3 -m jarvis.cli plans backfill-project-signals alpha \
  --profile-key nightly \
  --preset quick \
  --summary-only \
  --output warnings \
  --json-compact \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path "$JARVIS_DB_PATH"
```

## Manual Override Pattern

If you need to override one source window while keeping the rest profile-driven:

```bash
.venv-voice/bin/python - <<'PY'
from pathlib import Path
from jarvis.runtime import JarvisRuntime

runtime = JarvisRuntime(
    db_path=Path("/absolute/path/to/repo/.jarvis/jarvis.db"),
    repo_path=Path("/absolute/path/to/repo"),
)
try:
    run = runtime.run_project_backfill_with_cursor_profile_summary(
        project_id="alpha",
        profile_key="nightly",
        actor="operator",
        load_since_from_cursor_profile=True,
        since_review_artifacts_at="2026-04-18T00:00:00+00:00",
    )
    print(run["summary"])
finally:
    runtime.close()
PY
```

`resolution_source.review_artifacts` will show `explicit`, while other sources can still come from profile/global defaults.
