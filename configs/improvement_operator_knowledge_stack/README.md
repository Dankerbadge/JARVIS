# Improvement Operator Knowledge Stack

This pack bootstraps a multi-domain operator workflow for:

- quant finance
- Kalshi weather market automation
- fitness-app frustration mining
- market machine learning validation

## 1) Seed hypothesis templates

```bash
python3 -m jarvis.cli improvement seed-hypotheses \
  --template-path ./configs/improvement_hypothesis_templates_knowledge_stack.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/seed_report.json
```

## 1b) Run Kalshi weather leaderboard (standalone)

```bash
python3 -m jarvis.cli improvement fitness-leaderboard \
  --input-path ./configs/improvement_operator_knowledge_stack/analysis/kalshi_feedback.jsonl \
  --domain kalshi_weather \
  --source kalshi_trade_journal \
  --lookback-days 10 \
  --min-cross-app-count 1 \
  --output-path ./configs/improvement_operator_knowledge_stack/output/kalshi_weather_leaderboard.json
```

Wrapper:

```bash
./scripts/run_improvement_kalshi_leaderboard.sh \
  ./configs/improvement_operator_knowledge_stack/analysis/kalshi_feedback.jsonl \
  --output-path ./configs/improvement_operator_knowledge_stack/output/kalshi_weather_leaderboard.json
```

## 1c) Run quant finance leaderboard (standalone)

```bash
python3 -m jarvis.cli improvement fitness-leaderboard \
  --input-path ./configs/improvement_operator_knowledge_stack/analysis/quant_feedback.jsonl \
  --domain quant_finance \
  --source research_notes \
  --lookback-days 10 \
  --min-cross-app-count 1 \
  --output-path ./configs/improvement_operator_knowledge_stack/output/quant_finance_leaderboard.json
```

Wrapper:

```bash
./scripts/run_improvement_quant_leaderboard.sh \
  ./configs/improvement_operator_knowledge_stack/analysis/quant_feedback.jsonl \
  --output-path ./configs/improvement_operator_knowledge_stack/output/quant_finance_leaderboard.json
```

## 1d) Run market-ML leaderboard (standalone)

```bash
python3 -m jarvis.cli improvement fitness-leaderboard \
  --input-path ./configs/improvement_operator_knowledge_stack/analysis/market_ml_feedback.jsonl \
  --domain market_ml \
  --source ml_incident_log \
  --lookback-days 10 \
  --min-cross-app-count 1 \
  --output-path ./configs/improvement_operator_knowledge_stack/output/market_ml_leaderboard.json
```

Wrapper:

```bash
./scripts/run_improvement_market_ml_leaderboard.sh \
  ./configs/improvement_operator_knowledge_stack/analysis/market_ml_feedback.jsonl \
  --output-path ./configs/improvement_operator_knowledge_stack/output/market_ml_leaderboard.json
```

## 1e) Run domain smoke loop (pull -> leaderboard -> seed)

```bash
./scripts/run_improvement_domain_smoke.sh \
  ./configs/improvement_operator_knowledge_stack.json \
  kalshi_weather \
  --output-dir ./configs/improvement_operator_knowledge_stack/output/domain_smoke/kalshi_weather \
  --allow-missing
```

The smoke wrapper resolves domain-specific defaults from config, runs:

1. `run_improvement_pull_feeds.sh` (scoped to the domain feed when available)
2. Domain leaderboard wrapper (`fitness` / `kalshi` / `quant` / `market_ml`)
3. `run_improvement_seed_from_leaderboard.sh`

and writes `<domain>_smoke_summary.json` under the smoke output directory.

## 2) Run full operator cycle

```bash
python3 -m jarvis.cli improvement operator-cycle \
  --config-path ./configs/improvement_operator_knowledge_stack.json \
  --output-dir ./configs/improvement_operator_knowledge_stack/output \
  --strict
```

The operator-cycle wrapper automatically follows this run with
`improvement knowledge-bootstrap-route`, writing
`knowledge_bootstrap_route.json` so automation can branch on:

- `bootstrap`
- `run_cycle`
- `noop`

Wrapper:

```bash
./scripts/run_improvement_operator_cycle.sh \
  ./configs/improvement_operator_knowledge_stack.json \
  --output-dir ./configs/improvement_operator_knowledge_stack/output \
  --strict
```

## 2b) Resolve knowledge bootstrap route (standalone)

```bash
python3 -m jarvis.cli improvement knowledge-bootstrap-route \
  --report-path ./configs/improvement_operator_knowledge_stack/output/operator_cycle_report.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/knowledge_bootstrap_route.json
```

Wrapper:

```bash
./scripts/run_improvement_knowledge_bootstrap_route.sh \
  ./configs/improvement_operator_knowledge_stack/output/operator_cycle_report.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/knowledge_bootstrap_route.json
```

## 3) Controlled matrix expectations

`matrices/controlled_experiment_matrix.json` defines scenario-level expected verdicts and artifact references.
Use it to compare actual pipeline outcomes against expected controlled-test behavior.

```bash
python3 -m jarvis.cli improvement verify-matrix \
  --matrix-path ./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json \
  --report-path ./configs/improvement_operator_knowledge_stack/output/daily_pipeline_report.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/matrix_verification_report.json \
  --strict
```

Coverage drift is included in the same report via
`summary.unmapped_run_count`, `unmapped_runs`, and
`unmapped_run_count_by_domain` whenever experiment runs are not mapped by
matrix scenarios.

Wrapper:

```bash
./scripts/run_improvement_verify_matrix.sh \
  ./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json \
  ./configs/improvement_operator_knowledge_stack/output/daily_pipeline_report.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/matrix_verification_report.json \
  --strict
```

Open a high-priority delivered interrupt automatically when drift is present:

```bash
python3 -m jarvis.cli improvement verify-matrix-alert \
  --matrix-path ./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json \
  --report-path ./configs/improvement_operator_knowledge_stack/output/daily_pipeline_report.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/matrix_drift_alert_report.json \
  --strict
```

This command auto-classifies drift severity (`warn` or `critical`) and scales
alert urgency/confidence accordingly. Use `--alert-urgency` and
`--alert-confidence` only when you want manual overrides.

When a `critical` alert exists and remains unacknowledged, promotion gates
(`plans evaluate-promotion` / `plans promote-ready`) block by default until:

```bash
python3 -m jarvis.cli interrupts acknowledge <interrupt_id> --actor operator
```

`plans evaluate-promotion` includes `policy.critical_drift_gate_status`
with `blocking_interrupt_ids` and ready-to-run `acknowledge_commands` so the
operator can unblock directly from command output.
You can also print a concise unblock view with:

```bash
python3 -m jarvis.cli plans gate-status <plan_id> <step_id> \
  --single-maintainer-override \
  --allow-no-required-checks \
  --output text
```

And a consolidated queue across recent review steps:

```bash
python3 -m jarvis.cli plans gate-status-all \
  --limit 25 \
  --only-blocked \
  --fail-on-blocked \
  --fail-on-errors \
  --fail-on-zero-scanned \
  --fail-on-zero-evaluated \
  --fail-on-zero-unlock-ready \
  --fail-on-empty-ack-commands \
  --blocked-exit-code 7 \
  --error-exit-code 11 \
  --zero-scanned-exit-code 17 \
  --zero-evaluated-exit-code 13 \
  --zero-unlock-ready-exit-code 23 \
  --empty-ack-commands-exit-code 19 \
  --emit-ci-summary-path ./configs/improvement_operator_knowledge_stack/output/gate_status_all_summary.md \
  --emit-ci-json-path ./configs/improvement_operator_knowledge_stack/output/gate_status_all_compact.json \
  --single-maintainer-override \
  --allow-no-required-checks \
  --output text
```

If `--emit-ci-summary-path` is omitted, GitHub Actions runs automatically write the markdown summary
to `GITHUB_STEP_SUMMARY` when that environment variable is present.
Use `--emit-ci-json-path` when automation needs a compact JSON artifact for branching logic.

GitHub Actions template for compact JSON branching:

- `./configs/improvement_operator_knowledge_stack/github-actions-gate-status-compact.yml`
- `./configs/improvement_operator_knowledge_stack/github-actions-knowledge-bootstrap-route.yml`
- `./configs/improvement_operator_knowledge_stack/github-actions-domain-smoke-nightly.yml`
- `./configs/improvement_operator_knowledge_stack/github-actions-controlled-matrix-nightly.yml`

Active workflow in this repo:

- `./.github/workflows/improvement-gate-status-compact.yml`
- `./.github/workflows/improvement-knowledge-bootstrap-route.yml`
- `./.github/workflows/improvement-domain-smoke-nightly.yml`
- `./.github/workflows/improvement-controlled-matrix-nightly.yml`
- `./.github/workflows/reconcile-codeowner-review-gate.yml`

`improvement-knowledge-bootstrap-route.yml` runs on weekdays at `13:25 UTC`
and fails when either:

- `steps.route.outputs.route_blocking == '1'`
- guardrail gate detects `stage_error_count > 0` or `verify_matrix.status != ok`

When initial route is `bootstrap`, it executes one follow-up rerun from
`next_action_command` via `improvement knowledge-bootstrap-followup-rerun`,
regenerates `output/ci/knowledge_bootstrap_route_post_bootstrap.json`, writes
`output/ci/knowledge_bootstrap_followup_rerun.json`, emits follow-up outputs
(`bootstrap_followup_command`, `bootstrap_followup_status`,
`bootstrap_followup_phase`, `bootstrap_followup_route`) via
`--emit-github-output`, writes the `Bootstrap Follow-Up` step summary via
`--summary-heading`, and then branches on the effective post-follow-up route
payload.
Route output extraction for both `route_initial` and effective `route` is now
fully command-driven via `improvement knowledge-bootstrap-route-outputs`, which
emits step outputs with `--emit-github-output` and writes route step summaries
via `--summary-heading` (effective route also uses
`--summary-include-artifact-source`).
Guardrail gate is also command-driven via
`improvement verify-matrix-guardrail-gate`, which emits guardrail outputs
(`guardrail_gate_report`, `guardrail_gate_operator_status`,
`guardrail_gate_stage_error_count`, `guardrail_gate_verify_matrix_status`) via
`--emit-github-output`, writes the `Operator Guardrail Gate` step summary via
`--summary-heading`, and preserves strict failure semantics via `--strict`
using the command-provided `failure_reason`.
Before guardrail checks, it builds compact verify-matrix coverage artifacts:

- `output/ci/operator_cycle/verify_matrix_compact.json`
- `output/ci/operator_cycle/verify_matrix_compact.md`

The compact coverage gate is command-driven via
`improvement verify-matrix-compact`, emits compact outputs through
`--emit-github-output`, writes the `Verify Matrix Compact Coverage` summary via
`--summary-heading`, and appends compact markdown details via
`--summary-include-markdown`.

The compact payload includes per-domain `domain_statuses` for
`quant_finance`, `kalshi_weather`, `fitness_apps`, and `market_ml`, plus
`required_domain_count`, `covered_domain_count`, `missing_domain_count`,
`missing_domains_csv`, `required_domain_missing_count`, `first_missing_domain`,
`acknowledge_command_count`, `first_acknowledge_command`,
`verify_matrix_recheck_command` / `recheck_command`,
`verify_matrix_first_unlock_ready_command` / `first_unlock_ready_command`, compact
`operator_ack_bundle` (`command_count`, `first_command`, `command_sequence`),
`suggested_actions` (`suggested_action_count`, `first_suggested_action`), and
`first_repair_command`.
When required-domain coverage is missing, it also writes
`output/ci/operator_cycle/verify_matrix_coverage_alert.json`, opens a delivered
operations interrupt, and appends coverage alert fields to the compact payload
(`coverage_alert_path`, `coverage_interrupt_id`,
`coverage_acknowledge_command`, `verify_matrix_coverage_first_repair_command`).
The coverage interrupt step is command-driven via
`improvement verify-matrix-coverage-alert`, emits step outputs with
`--emit-github-output`, and writes the `Verify Matrix Coverage Interrupt Alert`
step summary via `--summary-heading`.
The workflow fails if any required domain is missing from verify-matrix
comparisons, even when overall verify-matrix status is `ok`.
Before artifact upload, the workflow also collects:

- `configs/improvement_operator_knowledge_stack/output/debug_runs`
- `analysis/improvement/knowledge_snapshots`

into `output/ci/` for run-level debug traceability.

`improvement-domain-smoke-nightly.yml` runs on weekdays at `03:40 UTC` with a
four-domain matrix (`quant_finance`, `kalshi_weather`, `fitness_apps`,
`market_ml`), executes `run_improvement_domain_smoke.sh` for each lane, and
uploads per-domain artifacts from `output/ci/domain_smoke/<domain>/`.
The lane output extraction adapter is command-driven via
`python3 -m jarvis.cli improvement domain-smoke-outputs --emit-github-output --summary-heading "Domain Smoke"`.
When a lane is blocking, the runtime interrupt adapter is command-driven via
`python3 -m jarvis.cli improvement domain-smoke-runtime-alert --emit-github-output --summary-heading "Domain Smoke Interrupt Alert"`.
When a lane is blocking, it also writes `<domain>_smoke_alert.json`,
auto-creates a delivered interrupt in lane-local `jarvis.db`, and emits both
an `acknowledge_command` and a direct smoke-loop `rerun_command`.
After matrix lanes complete, `domain-smoke-aggregate` downloads
`domain-smoke-*` artifacts and writes cross-domain triage outputs:
`output/ci/domain_smoke/domain_smoke_cross_domain_summary.json` and
`output/ci/domain_smoke/domain_smoke_cross_domain_summary.md`, including ranked
`top_risks` with rerun/acknowledge commands.
The cross-domain compact adapter is command-driven via
`python3 -m jarvis.cli improvement domain-smoke-cross-domain-compact --emit-github-output --summary-heading "Domain Smoke Cross-Domain Summary"`.
The aggregate summary JSON also carries `operator_ack_bundle` with
`acknowledge_bundle_command_sequence` so one copied command string can
acknowledge per-domain interrupts in order.
For compact UIs it also exposes `acknowledge_command_count` and
`first_acknowledge_command`.
For symmetric compact triage it also exposes `rerun_command_count` and
`first_rerun_command`.
It also includes `suggested_action_count` and `first_suggested_action` for
quick operator action previews.
When `warning_count > 0`, it also writes
`output/ci/domain_smoke/domain_smoke_cross_domain_alert.json` and opens a
single delivered cross-domain interrupt with aggregate acknowledge/rerun
commands, then updates `operator_ack_bundle` so that same command sequence
includes the cross-domain interrupt acknowledge command at the end.
That cross-domain runtime adapter is command-driven via
`python3 -m jarvis.cli improvement domain-smoke-cross-domain-runtime-alert --emit-github-output --summary-heading "Domain Smoke Cross-Domain Interrupt Alert"`.

`improvement-controlled-matrix-nightly.yml` runs on weekdays at `05:20 UTC`,
executes `run_improvement_daily_pipeline.sh` in strict mode, and then runs
`run_improvement_verify_matrix_alert.sh` against
`matrices/controlled_experiment_matrix.json` using the freshly generated
`daily_pipeline_report.json`.
The compact summary adapter is command-driven via
`python3 -m jarvis.cli improvement controlled-matrix-compact --emit-github-output --summary-heading "Controlled Matrix Drift Summary"`,
and the runtime interrupt adapter is command-driven via
`python3 -m jarvis.cli improvement controlled-matrix-runtime-alert --emit-github-output --summary-heading "Controlled Matrix Runtime Interrupt Alert"`.
It writes compact controlled-validation artifacts to
`output/ci/controlled_matrix/`:

- `daily_pipeline_report.json`
- `verify_matrix_alert_report.json`
- `controlled_matrix_summary.json`
- `controlled_matrix_summary.md`

For compact triage, the summary includes `acknowledge_command_count` and
`first_acknowledge_command`, compact `operator_ack_bundle`
(`command_count`, `first_command`, `command_sequence`), and
`repair_command_count` / `first_repair_command`, plus
`suggested_action_count` / `first_suggested_action`,
`mitigation_action_count` / `first_mitigation_action`, and
`top_scenario_count` / `first_top_scenario`.
When drift gating fails without a pre-existing matrix interrupt, it also writes
`output/ci/controlled_matrix/controlled_matrix_runtime_alert.json`, opens a
delivered operations interrupt, and appends runtime fields to summary payload
(`runtime_alert_path`, `runtime_interrupt_id`, `runtime_acknowledge_command`,
`runtime_first_repair_command`).
The workflow uploads those artifacts under `controlled-matrix-validation` and
fails when either the daily pipeline step fails or controlled matrix status is
not `ok`, printing matrix/runtime interrupt ids and repair command hints.

Copy that file into `.github/workflows/` to run `plans gate-status-all` with
`--emit-github-output` and `--summary-heading "Gate Status Compact"`, read
`output/ci/gate_status_all_compact.json`, and branch on:

- `steps.gate.outputs.exit_reason`
- `steps.gate.outputs.blocked_step_count`
- `steps.gate.outputs.unlock_ready_step_count`
- `steps.gate.outputs.first_unlock_ready_command`
- `steps.gate.outputs.acknowledge_command_count`
- `steps.gate.outputs.first_acknowledge_command`

The compact gate workflow keeps `--fail-on-zero-unlock-ready` with
`--zero-unlock-ready-exit-code 23` so `exit_reason` captures that condition,
but CI treats `zero_unlock_ready_steps` as a warning branch and only fails on
other non-`none` exit reasons.

Single-maintainer safety reconciler (auto-toggle code-owner review gate by collaborator count):

```bash
./scripts/reconcile_codeowner_review_gate.sh \
  --repo-slug Dankerbadge/JARVIS \
  --branch main \
  --min-collaborators 2 \
  --apply
```

For collaborator counts below threshold, it also reconciles
`required_approving_review_count` down to `0` so single-maintainer branches
can merge and disables `require_last_push_approval`; when above threshold it
keeps at least one required approval and preserves last-push approval behavior.

The scheduled reconciler workflow expects `JARVIS_ADMIN_GH_TOKEN` with repo-admin capability so it
can patch branch-protection review settings automatically.

Wrapper:

```bash
./scripts/run_improvement_verify_matrix_alert.sh \
  ./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json \
  ./configs/improvement_operator_knowledge_stack/output/daily_pipeline_report.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/matrix_drift_alert_report.json \
  --strict
```
