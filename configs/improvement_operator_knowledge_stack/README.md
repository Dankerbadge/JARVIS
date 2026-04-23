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
  --fail-on-empty-ack-commands \
  --blocked-exit-code 7 \
  --error-exit-code 11 \
  --zero-scanned-exit-code 17 \
  --zero-evaluated-exit-code 13 \
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

Active workflow in this repo:

- `./.github/workflows/improvement-gate-status-compact.yml`
- `./.github/workflows/improvement-knowledge-bootstrap-route.yml`
- `./.github/workflows/improvement-domain-smoke-nightly.yml`
- `./.github/workflows/reconcile-codeowner-review-gate.yml`

`improvement-knowledge-bootstrap-route.yml` runs on weekdays at `13:25 UTC`
and fails when either:

- `steps.route.outputs.route_blocking == '1'`
- guardrail gate detects `stage_error_count > 0` or `verify_matrix.status != ok`

When initial route is `bootstrap`, it executes one follow-up rerun from
`next_action_command`, regenerates
`output/ci/knowledge_bootstrap_route_post_bootstrap.json`, and then branches on
the effective post-follow-up route payload.
Before artifact upload, the workflow also collects:

- `configs/improvement_operator_knowledge_stack/output/debug_runs`
- `analysis/improvement/knowledge_snapshots`

into `output/ci/` for run-level debug traceability.

`improvement-domain-smoke-nightly.yml` runs on weekdays at `03:40 UTC` with a
four-domain matrix (`quant_finance`, `kalshi_weather`, `fitness_apps`,
`market_ml`), executes `run_improvement_domain_smoke.sh` for each lane, and
uploads per-domain artifacts from `output/ci/domain_smoke/<domain>/`.
When a lane is blocking, it also writes `<domain>_smoke_alert.json`,
auto-creates a delivered interrupt in lane-local `jarvis.db`, and emits both
an `acknowledge_command` and a direct smoke-loop `rerun_command`.
After matrix lanes complete, `domain-smoke-aggregate` downloads
`domain-smoke-*` artifacts and writes cross-domain triage outputs:
`output/ci/domain_smoke/domain_smoke_cross_domain_summary.json` and
`output/ci/domain_smoke/domain_smoke_cross_domain_summary.md`, including ranked
`top_risks` with rerun/acknowledge commands.
The aggregate summary JSON also carries `operator_ack_bundle` with
`acknowledge_bundle_command_sequence` so one copied command string can
acknowledge per-domain interrupts in order.
For compact UIs it also exposes `acknowledge_command_count` and
`first_acknowledge_command`.
When `warning_count > 0`, it also writes
`output/ci/domain_smoke/domain_smoke_cross_domain_alert.json` and opens a
single delivered cross-domain interrupt with aggregate acknowledge/rerun
commands, then updates `operator_ack_bundle` so that same command sequence
includes the cross-domain interrupt acknowledge command at the end.

Copy that file into `.github/workflows/` to run `plans gate-status-all`, read
`output/ci/gate_status_all_compact.json`, and branch on:

- `steps.gate.outputs.exit_reason`
- `steps.gate.outputs.blocked_step_count`

Single-maintainer safety reconciler (auto-toggle code-owner review gate by collaborator count):

```bash
./scripts/reconcile_codeowner_review_gate.sh \
  --repo-slug Dankerbadge/JARVIS \
  --branch main \
  --min-collaborators 2 \
  --apply
```

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
