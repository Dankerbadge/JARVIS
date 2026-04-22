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

## 2) Run full operator cycle

```bash
python3 -m jarvis.cli improvement operator-cycle \
  --config-path ./configs/improvement_operator_knowledge_stack.json \
  --output-dir ./configs/improvement_operator_knowledge_stack/output \
  --strict
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

Active workflow in this repo:

- `./.github/workflows/improvement-gate-status-compact.yml`
- `./.github/workflows/reconcile-codeowner-review-gate.yml`

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
