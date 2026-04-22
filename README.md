# JARVIS Bootstrap (Clean)

Runnable bootstrap of a local-first JARVIS core aligned to the baseline contract:

- live world-state graph
- episodic/semantic/procedural memory
- planner/executor split with persisted plan artifacts
- permission classes `P0`-`P4` with approvals, audit, rollback markers, kill switch
- first end-to-end `Zenith` skill scaffold

## Quickstart

```bash
python3 -m unittest discover -s tests -v
python3 -m jarvis.cli demo
```

## M23 Operator Runbook (Backfill Cursor Profiles)

For project-signal incremental backfills with cursor profiles (preview, dry-run,
summary mode, and cursor persistence), use:

- `M23_BACKFILL_CURSOR_RUNBOOK.md`
- `configs/backfill_workflow_snippets/README.md` (scheduled automation templates + warning-policy presets)

## M24 Friction -> Hypothesis Cycle

Run friction mining + auto-hypothesis cycle from local feedback feeds:

```bash
python3 -m jarvis.cli improvement cycle-from-file \
  --domain fitness_apps \
  --source app_store_reviews \
  --input-path ./analysis/fitness_feedback.jsonl \
  --input-format jsonl \
  --min-cluster-count 1 \
  --proposal-limit 5 \
  --report-path ./output/improvement/fitness_inbox_report.json
```

Use the scheduling-friendly wrapper script:

```bash
./scripts/run_improvement_cycle.sh \
  fitness_apps \
  app_store_reviews \
  ./analysis/fitness_feedback.jsonl \
  --report-path ./output/improvement/fitness_inbox_report.json
```

Run hypothesis experiments directly from backtest/paper-trade artifact JSON:

```bash
python3 -m jarvis.cli improvement run-experiment-artifact \
  --hypothesis-id hyp_your_id_here \
  --artifact-path ./analysis/market_ml_backtest_eval.json
```

Pull external/local feedback feeds into normalized JSONL files:

```bash
python3 -m jarvis.cli improvement pull-feeds \
  --config-path ./configs/improvement_pipeline_example.json
```

For live fitness market ingestion from App Store + Google Play CSV exports, use:

```bash
python3 -m jarvis.cli improvement pull-feeds \
  --config-path ./configs/improvement_fitness_market_live_example.json
```

This config uses source presets (`apple_app_store_reviews_csv`, `google_play_reviews_csv`)
and `write_mode=append_dedupe` so recurring exports stay idempotent while both stores land in one combined JSONL stream at
`./analysis/fitness_market_feedback.jsonl` for shared displeasure mining.

Generate a week-over-week fitness frustration leaderboard (current week vs prior week):

```bash
python3 -m jarvis.cli improvement fitness-leaderboard \
  --input-path ./analysis/fitness_market_feedback.jsonl \
  --lookback-days 7 \
  --app-fields app_name,app,product,source_context.app \
  --own-app-aliases myapp,my_app_ios,my_app_android \
  --min-cross-app-count 2 \
  --output-path ./output/improvement/fitness_frustration_leaderboard.json
```

The leaderboard now includes:
- `shared_market_displeasures`: frustrations seen across multiple competitor apps.
- `white_space_candidates`: market pains not currently present in your own app aliases.
- `top_apps_current` per cluster plus window-level app coverage diagnostics (`app_resolution`).

Scheduling-friendly wrapper:

```bash
./scripts/run_improvement_fitness_leaderboard.sh \
  ./analysis/fitness_market_feedback.jsonl \
  --output-path ./output/improvement/fitness_frustration_leaderboard.json
```

Auto-seed hypothesis queue entries from top `new/rising` leaderboard frustrations:

```bash
python3 -m jarvis.cli improvement seed-from-leaderboard \
  --leaderboard-path ./output/improvement/fitness_frustration_leaderboard.json \
  --trends new,rising \
  --entry-source shared_market_displeasures \
  --fallback-entry-source leaderboard \
  --min-cross-app-count 2 \
  --limit 8 \
  --output-path ./output/improvement/fitness_leaderboard_seed_report.json
```

For whitespace-first strategy (competitor pains not observed in your own app aliases), switch to:

```bash
python3 -m jarvis.cli improvement seed-from-leaderboard \
  --leaderboard-path ./output/improvement/fitness_frustration_leaderboard.json \
  --entry-source white_space_candidates \
  --fallback-entry-source leaderboard \
  --trends new,rising \
  --min-cross-app-count 2 \
  --limit 8
```

Scheduling-friendly wrapper:

```bash
./scripts/run_improvement_seed_from_leaderboard.sh \
  ./output/improvement/fitness_frustration_leaderboard.json \
  --output-path ./output/improvement/fitness_leaderboard_seed_report.json
```

Draft controlled experiment jobs directly from that seed report (artifact templates + guardrails + target sample size):

```bash
python3 -m jarvis.cli improvement draft-experiment-jobs \
  --seed-report-path ./output/improvement/fitness_leaderboard_seed_report.json \
  --pipeline-config-path ./configs/improvement_fitness_market_live_example.json \
  --artifacts-dir ./analysis/improvement/experiment_artifacts \
  --output-path ./output/improvement/fitness_experiment_draft_report.json
```

You can also prioritize draft selection from benchmark-ranked frustrations (for multi-domain next-step automation):

```bash
python3 -m jarvis.cli improvement draft-experiment-jobs \
  --benchmark-report-path ./configs/improvement_operator_knowledge_stack/output/operator_cycle_seeded_all_domains/frustration_benchmark_report.json \
  --benchmark-min-opportunity 2.0 \
  --pipeline-config-path ./configs/improvement_fitness_market_live_example.json \
  --output-path ./output/improvement/benchmark_priority_draft_report.json
```

This command also writes an updated pipeline config (`*.drafted.json` by default) with appended
`experiment_jobs` stubs so you can run controlled tests in one step, then execute:

```bash
python3 -m jarvis.cli improvement daily-pipeline \
  --config-path ./configs/improvement_fitness_market_live_example.drafted.json
```

Scheduling-friendly wrapper:

```bash
./scripts/run_improvement_draft_experiment_jobs.sh \
  ./output/improvement/fitness_leaderboard_seed_report.json \
  --benchmark-report-path ./configs/improvement_operator_knowledge_stack/output/operator_cycle_seeded_all_domains/frustration_benchmark_report.json \
  --pipeline-config-path ./configs/improvement_fitness_market_live_example.json \
  --output-path ./output/improvement/fitness_experiment_draft_report.json
```

Run a config-driven daily pipeline (multiple feedback feeds + experiment artifacts):

```bash
python3 -m jarvis.cli improvement daily-pipeline \
  --config-path ./configs/improvement_pipeline_example.json
```

`daily-pipeline` now supports hypothesis auto-resolution for experiment jobs using
`domain + friction_key` (so you do not need to hardcode `hypothesis_id` in config).
It can also emit per-experiment debug artifacts (`failed_checks`, `root_cause_hints`,
and optional reasoning timelines) for controlled-environment iteration.
It now includes an auto-retest lane: `blocked_guardrail` and `insufficient_data`
outcomes can be re-queued with recommended cohort-size and guardrail-safety targets,
plus side-by-side comparison summaries versus previous runs.

Execute queued retest runs from a prior pipeline report:

```bash
python3 -m jarvis.cli improvement execute-retests \
  --pipeline-report-path ./output/improvement/daily_pipeline_report.json \
  --artifact-dir ./output/improvement/retest_artifacts \
  --output-path ./output/improvement/retest_execution_report.json
```

Scheduling-friendly wrapper:

```bash
./scripts/run_improvement_daily_pipeline.sh \
  ./configs/improvement_pipeline_example.json \
  --strict
```

Feed-pull wrapper:

```bash
./scripts/run_improvement_pull_feeds.sh \
  ./configs/improvement_pipeline_example.json \
```

Retest execution wrapper:

```bash
./scripts/run_improvement_execute_retests.sh \
  ./output/improvement/daily_pipeline_report.json \
  --artifact-dir ./output/improvement/retest_artifacts \
  --strict
```

Run the full operator chain in one command (`pull-feeds -> daily-pipeline -> execute-retests`)
and emit a consolidated operator inbox summary:

```bash
python3 -m jarvis.cli improvement operator-cycle \
  --config-path ./configs/improvement_pipeline_example.json \
  --output-dir ./output/improvement/operator_cycle \
  --strict
```

Operator-cycle wrapper:

```bash
./scripts/run_improvement_operator_cycle.sh \
  ./configs/improvement_pipeline_example.json \
  --output-dir ./output/improvement/operator_cycle \
  --strict
```

Enable auto-seeding + auto-drafting inside operator-cycle
(`pull-feeds -> fitness-leaderboard -> seed-from-leaderboard -> draft-experiment-jobs -> daily-pipeline -> execute-retests`):

```bash
python3 -m jarvis.cli improvement operator-cycle \
  --config-path ./configs/improvement_pipeline_example.json \
  --output-dir ./output/improvement/operator_cycle \
  --seed-enable \
  --draft-enable \
  --strict
```

Run the same seed+draft flow across all knowledge-stack domains in one pass:

```bash
python3 -m jarvis.cli improvement operator-cycle \
  --config-path ./configs/improvement_operator_knowledge_stack.json \
  --output-dir ./configs/improvement_operator_knowledge_stack/output/operator_cycle_seeded \
  --seed-enable \
  --seed-domains quant_finance,kalshi_weather,fitness_apps,market_ml \
  --draft-enable \
  --draft-statuses queued,validated \
  --strict
```

If you already generated a seed report, you can still draft directly from that report:

```bash
python3 -m jarvis.cli improvement operator-cycle \
  --config-path ./configs/improvement_pipeline_example.json \
  --output-dir ./output/improvement/operator_cycle \
  --draft-enable \
  --draft-seed-report-path ./output/improvement/fitness_leaderboard_seed_report.json \
  --strict
```

### Knowledge-Stack Operator Pack (Quant + Kalshi + Fitness + Market ML)

Seed reusable cross-domain hypotheses:

```bash
python3 -m jarvis.cli improvement seed-hypotheses \
  --template-path ./configs/improvement_hypothesis_templates_knowledge_stack.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/seed_report.json
```

Wrapper:

```bash
./scripts/run_improvement_seed_hypotheses.sh \
  ./configs/improvement_hypothesis_templates_knowledge_stack.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/seed_report.json
```

Run the multi-domain operator cycle with controlled artifacts and debug outputs:

```bash
python3 -m jarvis.cli improvement operator-cycle \
  --config-path ./configs/improvement_operator_knowledge_stack.json \
  --output-dir ./configs/improvement_operator_knowledge_stack/output \
  --strict
```

Verify expected-vs-actual verdicts and flag drift:

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

Create a high-priority operator inbox alert automatically when drift is detected:

```bash
python3 -m jarvis.cli improvement verify-matrix-alert \
  --matrix-path ./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json \
  --report-path ./configs/improvement_operator_knowledge_stack/output/daily_pipeline_report.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/matrix_drift_alert_report.json \
  --strict
```

Generate a cross-domain benchmark that ranks recurring pains, trend acceleration, and implementation win-rates:

```bash
python3 -m jarvis.cli improvement benchmark-frustrations \
  --report-path ./configs/improvement_operator_knowledge_stack/output/operator_cycle_seeded_all_domains/operator_inbox_summary.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/operator_cycle_seeded_all_domains/frustration_benchmark_report.json
```

Wrapper:

```bash
./scripts/run_improvement_benchmark_frustrations.sh \
  ./configs/improvement_operator_knowledge_stack/output/operator_cycle_seeded_all_domains/operator_inbox_summary.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/operator_cycle_seeded_all_domains/frustration_benchmark_report.json
```

`verify-matrix-alert` now classifies drift severity as `warn` or `critical` and auto-scales
alert urgency/confidence from mismatch/missing/invalid counts and guardrail regressions.
You can still override with `--alert-urgency` and `--alert-confidence` when needed.

`plans evaluate-promotion` and `plans promote-ready` now enforce a `critical` drift gate
by default and block promotion while unacknowledged critical drift alerts exist.
Unblock by acknowledging the alert:

```bash
python3 -m jarvis.cli interrupts acknowledge <interrupt_id> --actor operator
```

The promotion policy payload now also exposes unblock-ready hints under
`policy.critical_drift_gate_status` (`blocking_interrupt_ids` +
`acknowledge_commands`) and per-alert `acknowledge_command` entries.
For a concise operator view, run:

```bash
python3 -m jarvis.cli plans gate-status <plan_id> <step_id> \
  --single-maintainer-override \
  --allow-no-required-checks \
  --output text \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

To scan recent review steps and print one consolidated blocker queue:

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
  --output text \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

When `--emit-ci-summary-path` is omitted and `GITHUB_STEP_SUMMARY` is set (for example in GitHub
Actions), `gate-status-all` automatically writes the same markdown summary to that step-summary path.
Use `--emit-ci-json-path` when downstream CI jobs need a compact machine-readable artifact instead of parsing stdout.
Use `./configs/improvement_operator_knowledge_stack/github-actions-gate-status-compact.yml` as a
copy-ready workflow template that branches on `blocked_step_count` and `exit_reason` from the compact artifact.
Live workflow path in this repo:
`./.github/workflows/improvement-gate-status-compact.yml`.
Automated reconciler workflow path in this repo:
`./.github/workflows/reconcile-codeowner-review-gate.yml`.

Override gate behavior only when explicitly needed:

```bash
python3 -m jarvis.cli plans promote-ready <plan_id> <step_id> --no-enforce-critical-drift-gate
```

Wrapper:

```bash
./scripts/run_improvement_verify_matrix_alert.sh \
  ./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json \
  ./configs/improvement_operator_knowledge_stack/output/daily_pipeline_report.json \
  --output-path ./configs/improvement_operator_knowledge_stack/output/matrix_drift_alert_report.json \
  --strict
```

The controlled matrix file lives at:

- `./configs/improvement_operator_knowledge_stack/matrices/controlled_experiment_matrix.json`

## Frozen Daily Dialogue Stack (M20B -> M21)

The daily stack is frozen to:

- primary model: `qwen3:14b`
- embed model: `mxbai-embed-large`
- reranker: `BAAI/bge-reranker-v2-m3`
- fallback heuristics: failure-only

`start_jarvis_daily_production.sh` now includes startup warmup (retrieval/model path prime) so first live turns do not pay full cold-start cost.

Start the canonical production runtime:

```bash
./scripts/start_jarvis_daily_production.sh --background
```

Show frozen runtime status and drift checks:

```bash
source .venv-voice/bin/activate
python scripts/jarvis_runtime_status.py --check-server --strict
```

Run an M21 relationship soak smoke pass (bounded):

```bash
source .venv-voice/bin/activate
PYTHONPATH=/Users/dankerbadge/Documents/J.A.R.V.I.S \
python scripts/run_m21_relationship_soak.py \
  --repo-path /Users/dankerbadge/Documents/J.A.R.V.I.S \
  --db-path /Users/dankerbadge/Documents/J.A.R.V.I.S/.jarvis/jarvis.db \
  --loops 1 \
  --max-turns 4 \
  --turn-timeout-seconds 20
```

`/api/presence/reply/prepare` now includes a `presence_ack` object (`text`, `target_ms`, `deferred`, `defer_reason`) so clients can render phase-A acknowledgment explicitly.

## ElevenLabs Actor Voice Provisioning

Provision the full JARVIS actor voice pipeline (clone voice, bind OpenClaw talk/TTS,
render validation samples):

```bash
cp .env.example .env
# edit .env and set ELEVENLABS_API_KEY
./scripts/provision_jarvis_actor_voice.sh
```

Outputs:

- `exports/voice_samples/jarvis_actor_match_sample.mp3`
- `exports/voice_samples/jarvis_actor_match_sample_sdk.mp3`
- `.jarvis/voice/ELEVENLABS_ACTOR_CLONE.json`

If your ElevenLabs plan does not include instant voice cloning, provisioning
automatically falls back to the best available premade voice and still wires
OpenClaw end-to-end. You can force a specific fallback voice:

```bash
./scripts/provision_jarvis_actor_voice.sh \
  --fallback-voice-id JBFqnCBsd6RMkjVDRZzb \
  --fallback-voice-name "George - Warm, Captivating Storyteller"
```

For deep isolation from long actor recordings (8+ minutes) and a Creator-ready
upload bundle:

```bash
.venv-voice/bin/python scripts/ingest_voicemod_actor_references.py \
  --inputs "/Users/dankerbadge/Downloads/JARVIS (1).mp3" "/Users/dankerbadge/Downloads/JARVIS II.mp3" \
  --work-dir analysis/jarvis_study/long_actor_isolation_extended \
  --ffmpeg-isolate --ffmpeg-profile strong \
  --no-replace-existing-ref-clips \
  --build-creator-bundle --creator-bundle-target-sec 210 --creator-bundle-max-clips 70
```

Creator upload bundle output:

- `analysis/jarvis_study/long_actor_isolation_extended/elevenlabs_creator_bundle/clips/`
- `analysis/jarvis_study/long_actor_isolation_extended/elevenlabs_creator_bundle/manifest.csv`


## Clean Release Packaging

Use the packaging script instead of ad-hoc `zip` commands. It uses an explicit allowlist and blocks
forbidden artifacts such as runtime DBs, worktrees, keys, nested milestone zips, and private key/token
patterns.

Build a clean milestone archive:

```bash
./scripts/build_release_zip.sh M9
```

Or set a custom output path:

```bash
./scripts/build_release_zip.sh M9 /absolute/path/JARVIS_M9_CLEAN_WIRED.zip
```

The script prints SHA-256 for verification and aborts if unsafe files are detected in the staged output.

## Wire Zenith To A Real Repo

```bash
export JARVIS_REPO_PATH="/absolute/path/to/your/repo"
python3 -m jarvis.cli demo --repo-path "$JARVIS_REPO_PATH"
```

`Zenith` uses a repo-aware diff engine against real files under `repo_path` and can:

- generate unified patch previews from file edits
- stage protected UI modifications behind approval-gated `P2` actions

## Always-On Event Runtime (Milestone 2)

Run one daemon cycle:

```bash
python3 -m jarvis.cli run-once --repo-path "$JARVIS_REPO_PATH" --db-path ./.jarvis/jarvis.db
```

Run the watcher loop:

```bash
python3 -m jarvis.cli watch --repo-path "$JARVIS_REPO_PATH" --db-path ./.jarvis/jarvis.db --interval 5
```

Approval inbox:

```bash
python3 -m jarvis.cli approvals list --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli approvals approve <approval_id> --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli approvals deny <approval_id> --db-path ./.jarvis/jarvis.db
```

## Git + CI Runtime (Milestone 3)

Use the Git-native connector (default for git repos) and optional JSON CI reports:

```bash
python3 -m jarvis.cli run-once \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --ci-reports-path /absolute/path/to/ci-reports
```

This emits:

- `repo.git_delta` events with branch/base/head/merge-base/commit-range/commit-list/file deltas
- `ci.failure` events from JSON reports
- branch-scoped correlation by indexed entities:
  - `latest_repo_delta:<repo_id>:<branch>`
  - `latest_ci_failure:<repo_id>:<branch>`

## Root-Cause Correlation (Milestone 4)

On `ci.failure` and `repo.git_delta`, the runtime now ranks likely root-cause paths using:

- latest CI failure context
- latest git delta for the same `repo_id + branch`
- recent plan outcomes for the same branch/failure-family

New indexed entity:

- `latest_root_cause_report:<repo_id>:<branch>`

Closed-loop feedback:

- plan outcomes are recorded automatically in `plan_outcomes`
- successful touched paths get positive weight
- failing/regressive touched paths get negative weight

Run:

```bash
python3 -m jarvis.cli run-once \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --ci-reports-path /absolute/path/to/ci-reports \
  --dry-run
```

## Execution Sandbox + Approval Evidence (Milestone 5)

Protected steps now prepare evidence before approval:

- isolated git worktree sandbox
- patch application in sandbox
- bounded preflight checks
- structured approval packet with ranked candidates, diff, preflight, rollback, and recent outcomes

Show a pending approval packet:

```bash
python3 -m jarvis.cli approvals show <approval_id> --db-path ./.jarvis/jarvis.db
```

Manual preflight prep for a plan:

```bash
python3 -m jarvis.cli plans preflight <plan_id> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Continue after approval in sandbox context:

```bash
python3 -m jarvis.cli plans execute-approved <plan_id> <step_id> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

## Layout

- `SYSTEM_SPEC_BASELINE.md`
- `jarvis/runtime.py`
- `jarvis/state_graph.py`
- `jarvis/memory.py`
- `jarvis/security.py`
- `jarvis/daemon.py`
- `jarvis/reactors.py`
- `jarvis/approval_inbox.py`
- `jarvis/connectors/`
- `jarvis/connectors/git_native.py`
- `jarvis/connectors/ci_reports.py`
- `jarvis/correlation.py`
- `jarvis/outcomes.py`
- `jarvis/state_index.py`
- `jarvis/preflight.py`
- `jarvis/approval_packet.py`
- `jarvis/execution_service.py`
- `jarvis/executors/git_worktree.py`
- `jarvis/skills/zenith.py`
- `tests/`

## Remote Branch + PR Orchestration (Milestone 6)

Approved protected steps can now be published into a remote review flow:

- commit prepared sandbox changes with a JARVIS-authored message
- push the sandbox branch to a configured git remote
- generate a durable PR payload with reasoning, ranked evidence, preflight, rollback, and audit refs
- persist publication receipts and PR payloads for later retrieval

Publish an approved step:

```bash
python3 -m jarvis.cli plans publish-approved <plan_id> <step_id> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --remote-name origin \
  --base-branch main
```

Show the generated PR payload:

```bash
python3 -m jarvis.cli plans pr-payload <plan_id> <step_id> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

## Provider-native Review Creation + Status Sync (Milestone 7)

Published approved steps can now become hosted provider review artifacts.

What is added:

- provider abstraction for hosted review backends
- GitHub provider implementation using the provider REST API
- persisted provider review artifact tied to approval + plan + step
- status sync back into runtime state under:
  - `latest_review_artifact:<repo_id>:<branch>`
  - `latest_review_status:<repo_id>:<branch>`

Configure GitHub:

```bash
export JARVIS_GITHUB_TOKEN="<token>"
# optional for testing / enterprise installs
export JARVIS_GITHUB_API_BASE="https://api.github.com"
```

Publish and immediately open a provider review:

```bash
python3 -m jarvis.cli plans publish-approved <plan_id> <step_id> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --remote-name origin \
  --base-branch main \
  --open-review \
  --provider github \
  --provider-repo owner/repo \
  --reviewer octocat
```

Open a provider review after publication:

```bash
python3 -m jarvis.cli plans open-review <plan_id> <step_id> \
  --provider github \
  --provider-repo owner/repo \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Show the stored provider review artifact:

```bash
python3 -m jarvis.cli plans review-artifact <plan_id> <step_id> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Sync the provider review state and checks back into runtime state:

```bash
python3 -m jarvis.cli plans sync-review <plan_id> <step_id> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Sync review feedback by logical repo/pr/branch mapping:

```bash
python3 -m jarvis.cli plans sync-review-feedback <repo_id> <pr_number> <branch> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

## Hosted Review Feedback + Promotion Policy (Milestone 8)

Provider review sync now ingests hosted feedback signals and indexes them into runtime state:

- requested reviewers
- submitted reviews
- issue comments
- review comments
- pull request timeline events

New indexed entities:

- `latest_requested_reviewers:<repo_id>:<branch>`
- `latest_review_summary:<repo_id>:<branch>`
- `latest_review_comments:<repo_id>:<branch>`
- `latest_timeline_cursor:<repo_id>:<branch>`
- `latest_merge_outcome:<repo_id>:<branch>`

Normalize reviewers and labels for an existing provider review:

```bash
python3 -m jarvis.cli plans configure-review <plan_id> <step_id> \
  --reviewer octocat \
  --assignee octocat \
  --label jarvis \
  --label needs-review \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Set only requested reviewers:

```bash
python3 -m jarvis.cli plans request-reviewers <plan_id> <step_id> \
  --reviewer octocat \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Set only labels:

```bash
python3 -m jarvis.cli plans set-labels <plan_id> <step_id> \
  --label jarvis \
  --label needs-review \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Show hosted summary + approval evidence:

```bash
python3 -m jarvis.cli plans review-summary <plan_id> <step_id> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Show hosted issue/review comments:

```bash
python3 -m jarvis.cli plans review-comments <plan_id> <step_id> \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Evaluate draft-to-ready promotion policy without mutating PR state:

```bash
python3 -m jarvis.cli plans evaluate-promotion <plan_id> <step_id> \
  --required-label jarvis \
  --required-label needs-review \
  --required-label protected-change \
  --single-maintainer-override \
  --override-actor "$USER" \
  --override-reason "single-maintainer transitional override" \
  --override-sunset-condition "disable when required checks exist or repo has >1 maintainer" \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Show only critical drift blockers + acknowledge commands:

```bash
python3 -m jarvis.cli plans gate-status <plan_id> <step_id> \
  --required-label jarvis \
  --required-label needs-review \
  --required-label protected-change \
  --single-maintainer-override \
  --override-actor "$USER" \
  --override-reason "single-maintainer transitional override" \
  --override-sunset-condition "disable when required checks exist or repo has >1 maintainer" \
  --output text \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Show a consolidated blocker queue across recent provider reviews:

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
  --required-label jarvis \
  --required-label needs-review \
  --required-label protected-change \
  --single-maintainer-override \
  --override-actor "$USER" \
  --override-reason "single-maintainer transitional override" \
  --override-sunset-condition "disable when required checks exist or repo has >1 maintainer" \
  --output text \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

`--emit-ci-summary-path` overrides `GITHUB_STEP_SUMMARY` when both are available.
For a ready-to-copy workflow branch pattern, use:
`./configs/improvement_operator_knowledge_stack/github-actions-gate-status-compact.yml`.
The active workflow in this repo is:
`./.github/workflows/improvement-gate-status-compact.yml`.
The automated reconciler workflow in this repo is:
`./.github/workflows/reconcile-codeowner-review-gate.yml`.

Single-maintainer safety reconciler (auto-toggle code-owner review gate by collaborator count):

```bash
./scripts/reconcile_codeowner_review_gate.sh \
  --repo-slug Dankerbadge/JARVIS \
  --branch main \
  --min-collaborators 2 \
  --apply
```

The scheduled reconciler workflow uses `JARVIS_ADMIN_GH_TOKEN` (repo-admin token scope) to patch
branch protection safely in GitHub Actions.

Promote to ready for review only if policy passes:

```bash
python3 -m jarvis.cli plans promote-ready <plan_id> <step_id> \
  --required-label jarvis \
  --required-label needs-review \
  --required-label protected-change \
  --single-maintainer-override \
  --override-actor "$USER" \
  --override-reason "single-maintainer transitional override" \
  --override-sunset-condition "disable when required checks exist or repo has >1 maintainer" \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Conservative default:

- if no required checks are configured, policy blocks promotion by default.
- override explicitly with `--allow-no-required-checks` only when you want that behavior.
- if no valid reviewers can be requested (single-maintainer repo), require explicit
  `--single-maintainer-override` and audit metadata (`actor`, `reason`, `sunset_condition`)
  for evaluate/promote commands.

## M9: Cognition + Academics Domain

Milestone 9 adds a bounded cognition loop and a second domain (Academics) into the same
event/state/memory/planning runtime path.

### Run daemon with Academics feed

```bash
python3 -m jarvis.cli run-once \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --academics-feed-path ./academics_feed.json
```

`academics_feed.json` can be either a list of items or `{ "items": [...] }` with event
types such as:

- `assignment_due`
- `exam_scheduled`
- `reading_assigned`
- `grade_update`
- `risk_signal`
- `study_window`

### Cognition artifacts

```bash
python3 -m jarvis.cli thoughts recent --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli thoughts show <thought_id> --db-path ./.jarvis/jarvis.db
```

### Daily synthesis

```bash
python3 -m jarvis.cli synthesis morning --generate --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli synthesis evening --generate --db-path ./.jarvis/jarvis.db
```

### Interrupt policy surfaces

```bash
python3 -m jarvis.cli interrupts list --status all --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli interrupts acknowledge <interrupt_id> --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli interrupts snooze <interrupt_id> --minutes 90 --db-path ./.jarvis/jarvis.db
```

### Academics state surfaces

```bash
python3 -m jarvis.cli academics overview --term-id current_term --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli academics risks --db-path ./.jarvis/jarvis.db
```


## M10: Local Cognition Backend + Release Hygiene Gate

M10 adds a configurable local cognition backend boundary while preserving planner/executor safety.

### Cognition backend config

```bash
export JARVIS_COGNITION_ENABLED=true
export JARVIS_COGNITION_BACKEND=ollama           # production daily profile
export JARVIS_COGNITION_MODEL=qwen3:14b          # keep qwen3:30b for optional deep follow-up
export JARVIS_COGNITION_AUTO_PREFER=qwen3:14b,qwen3:30b,gemma3:27b,qwen3:8b,llama3.2:3b-instruct,llama3.2:3b
export JARVIS_COGNITION_LOCAL_ONLY=true
```

Optional local endpoints:

```bash
export JARVIS_OLLAMA_ENDPOINT=http://127.0.0.1:11434/api/generate
export JARVIS_LLAMACPP_ENDPOINT=http://127.0.0.1:8080/v1/chat/completions
```

Print resolved runtime cognition config:

```bash
python3 -m jarvis.cli thoughts config --db-path ./.jarvis/jarvis.db
```

The cognition backend can assist with:

- hypothesis generation
- skepticism/dead-end diagnosis
- synthesis narrative drafting
- interrupt rationale drafting

Safety boundary is unchanged: backend output never executes actions directly and does not bypass approvals.

Thought artifacts now include backend provenance:

- `backend_name`
- `backend_model`
- `backend_mode` (`heuristic`, `ollama_assisted`, `llama_cpp_assisted`, or `heuristic_fallback`)
- `backend_metrics` (assist/fallback/query/latency/error telemetry)

### Replay/eval tests

```bash
python3 -m unittest tests/test_model_backend_contract.py -v
python3 -m unittest tests/test_cognition_replay.py -v
```

### Release hygiene gate

`build_release_zip.sh` now calls `scripts/verify_release_clean.py` and emits:

- `<zip>.manifest.json`
- `<zip>.scan_report.json`
- zip SHA-256

Run:

```bash
./scripts/build_release_zip.sh M10 /absolute/path/JARVIS_M10_CLEAN_WIRED.zip
```

CI guard:

- `.github/workflows/release-hygiene.yml`

## M11: Live Academics Ingestion + Cross-Domain Evaluation

M11 adds real Academics signal ingestion (calendar + materials/email), suppression-aware
interrupt policy updates, and a backend-comparison evaluation command.

### Run daemon with live Academics signals

```bash
python3 -m jarvis.cli run-once \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --academics-calendar-path ./academics_calendar.ics \
  --academics-materials-path ./academics_materials
```

Optional env defaults:

```bash
export JARVIS_ACADEMICS_FEED_PATH=./academics_feed.json
export JARVIS_ACADEMICS_CALENDAR_PATH=./academics_calendar.ics
export JARVIS_ACADEMICS_MATERIALS_PATH=./academics_materials
```

### Academics operational surfaces

```bash
python3 -m jarvis.cli academics overview --term-id current_term --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli academics risks --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli academics schedule --term-id current_term --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli academics windows --term-id current_term --db-path ./.jarvis/jarvis.db
```

### Compare heuristic vs local model cognition on one DB snapshot

```bash
python3 -m jarvis.cli thoughts evaluate \
  --snapshot-db-path ./.jarvis/jarvis.db \
  --repo-path "$JARVIS_REPO_PATH" \
  --primary-backend heuristic \
  --secondary-backend ollama \
  --secondary-model qwen3:14b
```

Output includes per-backend thought/synthesis/interrupt metrics and `improved_dimensions`.

## M12: Operator Surface + Companion Layer

M12 adds a local operator API/dashboard, real interruption governance controls, and
automatic daily digest archive export.

### Run local operator surface

```bash
python3 -m jarvis.cli serve \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --host 127.0.0.1 \
  --port 8765
```

Open: `http://127.0.0.1:8765`

### New interruption governance controls

```bash
python3 -m jarvis.cli interrupts preferences --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli interrupts focus-mode --domain academics --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli interrupts quiet-hours --start-hour 22 --end-hour 7 --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli interrupts suppress-until --until-iso 2026-04-12T14:00:00+00:00 --reason "deep work" --db-path ./.jarvis/jarvis.db
```

### Daily digest archive surfaces

```bash
python3 -m jarvis.cli archive export --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli archive list --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli archive show 2026-04-11 --db-path ./.jarvis/jarvis.db
```

Digest files are exported under `./.jarvis/archive/` as Markdown, HTML, and JSON.

## M13: Provider-Native Academics Intake (Google Calendar + Gmail)

M13 upgrades Academics ingestion from local-file-only feeds to read-only provider-native
signals with incremental cursors, while keeping the same shared event/state/memory/runtime
path and approval safety boundaries.

### Enable provider-native connectors

```bash
export JARVIS_GOOGLE_API_TOKEN="..."
export JARVIS_GOOGLE_CALENDAR_ID="primary"
export JARVIS_GMAIL_QUERY='newer_than:21d (subject:(exam OR assignment OR syllabus OR deadline OR midterm OR final))'

python3 -m jarvis.cli run-once \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Or pass explicit flags:

```bash
python3 -m jarvis.cli run-once \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --google-calendar-id primary \
  --google-api-token-env JARVIS_GOOGLE_API_TOKEN \
  --gmail-query 'newer_than:21d subject:(exam OR assignment)'
```

### Optional unattended token refresh (recommended for `watch`)

If you run with short-lived Google access tokens, configure refresh credentials so the
connectors can auto-refresh after auth expiry without restarting:

```bash
export JARVIS_GOOGLE_REFRESH_TOKEN="..."
export JARVIS_GOOGLE_CLIENT_ID="..."
export JARVIS_GOOGLE_CLIENT_SECRET="..."
# optional override:
# export JARVIS_GOOGLE_TOKEN_ENDPOINT="https://oauth2.googleapis.com/token"
```

CLI flags are also available:

- `--google-refresh-token` / `--google-refresh-token-env`
- `--google-client-id` / `--google-client-id-env`
- `--google-client-secret` / `--google-client-secret-env`
- `--google-token-endpoint`

### Source provenance in Academics state

Academic artifacts now preserve ingestion provenance for operator visibility:

- `signal_source_kind` (`provider` or `file_import`)
- `signal_provider` (`google_calendar`, `gmail`, `local_calendar`, `local_materials`, `local_feed`)

The operator home payload (`/api/home`) includes `academics.signal_sources` for source breakdown.

## M14: Goal Hierarchy + Personal Context Model

M14 adds a persistent identity model (goal hierarchy + domain weights) and personal-context
signals (stress, energy, sleep, focus budget) into cognition and interruption decisions.

### Identity and personal context controls

```bash
python3 -m jarvis.cli identity show --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli identity set-domain-weight --domain academics --weight 1.25 --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli identity set-goal --goal-id exam_block --label "Protect exam prep" --priority 1 --weight 1.4 --domain academics --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli identity update-context --stress-level 0.78 --energy-level 0.42 --sleep-hours 5.8 --focus-minutes 45 --mode deep_work --db-path ./.jarvis/jarvis.db
```

### Optional personal-context connector for daemon

```bash
export JARVIS_PERSONAL_CONTEXT_PATH=./personal_context.json
python3 -m jarvis.cli run-once --repo-path "$JARVIS_REPO_PATH" --db-path ./.jarvis/jarvis.db
```

`personal_context.json` example:

```json
{
  "stress_level": 0.74,
  "energy_level": 0.46,
  "sleep_hours": 6.0,
  "available_focus_minutes": 60,
  "mode": "deep_work",
  "note": "final prep window"
}
```

Operator/API note: the canonical context field is `available_focus_minutes`. The server accepts
legacy `focus_minutes` for backward compatibility.

### M14 cognition behavior

- thought artifacts now include:
  - `user_model_snapshot`
  - `personal_context_snapshot`
- hypothesis ranking now applies:
  - domain goal weights from identity profile
  - stress/fatigue/focus-budget context modifiers
- interrupt policy now supports:
  - goal-priority threshold shifts
  - stress-aware suppression for non-critical zenith interruptions

## M15: Markets Domain (Suggestion-First)

M15 adds Markets as domain #3 in the same shared event -> state -> memory -> cognition
runtime as Zenith + Academics. This milestone is intentionally suggestion-first: no direct
trade execution actions are introduced.

### Markets connectors (read-only ingestion)

Enable local JSON feeds for markets signals, positions, and event calendar:

```bash
export JARVIS_MARKETS_SIGNALS_PATH=./markets_signals.json
export JARVIS_MARKETS_POSITIONS_PATH=./markets_positions.json
export JARVIS_MARKETS_CALENDAR_PATH=./markets_calendar.json

python3 -m jarvis.cli run-once \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db
```

Or pass explicit flags:

```bash
python3 -m jarvis.cli watch \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --markets-signals-path ./markets_signals.json \
  --markets-positions-path ./markets_positions.json \
  --markets-calendar-path ./markets_calendar.json \
  --interval 5
```

### Markets CLI surfaces

```bash
python3 -m jarvis.cli markets overview --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli markets opportunities --limit 20 --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli markets abstentions --limit 20 --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli markets posture --account-id default --db-path ./.jarvis/jarvis.db
```

### Operator/API surfaces

New markets endpoints:

- `GET /api/markets/overview`
- `GET /api/markets/opportunities`
- `GET /api/markets/abstentions`
- `GET /api/markets/posture`

`/api/home` now includes a `markets` block with risk posture, opportunities, abstentions,
calendar events, and market-domain risks so tri-domain priorities are inspectable in one place.

### Suggestion-first safety boundaries

- Market skill actions remain bounded to `P0`/`P1` plus optional `P2` handoff packet prep.
- `P3`/`P4` direct market execution is intentionally disabled in M15.
- Interrupt policy now applies explicit markets suppression rules under academic focus and stress.

## M16: Market Handoff + Closed-Loop Learning

M16 closes the market learning loop by ingesting external investing-bot handoff outcomes and
mapping them into runtime feedback history used by cognition, correlation, and prioritization.

### Outcome connector (read-only)

Enable handoff outcome ingestion:

```bash
export JARVIS_MARKETS_OUTCOMES_PATH=./markets_outcomes.json

python3 -m jarvis.cli run-once \
  --repo-path "$JARVIS_REPO_PATH" \
  --db-path ./.jarvis/jarvis.db \
  --markets-signals-path ./markets_signals.json \
  --markets-positions-path ./markets_positions.json \
  --markets-calendar-path ./markets_calendar.json \
  --markets-outcomes-path ./markets_outcomes.json
```

Accepted outcome statuses in M16:

- `accepted`
- `rejected`
- `expired`
- `filled`
- `stopped`
- `skipped`

### Closed-loop state and learning behavior

- New state artifacts:
  - `latest_market_handoff:<handoff_id>`
  - `latest_market_outcome:<handoff_id>`
- Daemon now records market-outcome learning events into plan outcomes with market status mapping:
  - `filled -> success`
  - `accepted -> partial`
  - `rejected|expired -> failure`
  - `stopped -> regression`
  - `skipped -> partial`

### New Markets surfaces

CLI:

```bash
python3 -m jarvis.cli markets handoffs --limit 20 --db-path ./.jarvis/jarvis.db
python3 -m jarvis.cli markets outcomes --limit 20 --db-path ./.jarvis/jarvis.db
```

API:

- `GET /api/markets/handoffs`
- `GET /api/markets/outcomes`

`/api/home` and `/api/markets/overview` now include:

- latest handoffs
- latest outcomes
- outcome evaluation summary (`by_status` rollup)

### M16 guardrails

- Direct market execution remains disabled (`P3/P4` still off for markets).
- Outcomes are ingestion-only and auditable.
- Handoff/outcome learning does not bypass planner/executor approval boundaries.

## M17: Consciousness-First Lift (OpenClaw Contracts)

M17 introduces canonical cross-boundary signal ingest plus file-backed consciousness artifacts.
The focus is continuity, provenance, trust boundaries, and replay-safe operation.

### Canonical ingest contract

New module: `jarvis/signals.py`

- `SignalEnvelope` + `Provenance` (`jarvis.signal.v1`)
- payload sanitization/redaction defaults
- replay-safe dedupe (`SignalIngestStore`)

New API endpoints:

- `POST /api/ingest`
- `POST /api/ingest/signal` (alias)
- `GET /api/ingest/signals`

Optional token gating:

- set `JARVIS_INGEST_TOKEN` to require `X-JARVIS-Token` (or bearer token)

### Consciousness surfaces

New module: `jarvis/consciousness.py`

Generated files (`.jarvis/mind/`):

- `SOUL.md`
- `IDENTITY.md`
- `USER.md`
- `TOOLS.md`
- `AGENTS.md`
- `HEARTBEAT.md`
- `BOOT.md`
- `MEMORY.md`

New API endpoints:

- `GET /api/consciousness/surfaces`
- `POST /api/consciousness/refresh`
- `GET /api/consciousness/events`

### Memory telemetry

`MemoryStore` now writes append-only JSONL events at:

- `.jarvis/memory/.dreams/events.jsonl`

This captures memory writes, recalls, ingest lifecycle, cognition cycles, and digest exports.

### Identity consciousness contract

`IdentityStateStore` now persists `consciousness_contract_json` and exposes it through runtime
and operator APIs.

New API endpoints:

- `GET /api/identity/consciousness-contract`
- `POST /api/identity/consciousness-contract`

### OpenClaw bridge helper

New module: `jarvis/openclaw_bridge.py`

- private/loopback-first host guard
- deny-list policy for high-risk tools
- allow-list option for constrained `/tools/invoke` usage

## M18 Planning

`MILESTONE_18_SPEC.md` defines the next execution stage:

- OpenClaw WebSocket presence bridge
- SecretRef-aware bridge identity/token contract
- one-consciousness multi-surface projection policy
- pushback/override calibration artifacts

## M18 P1: Persistent Gateway Loop + Continuity Router

M18 P1 adds a persistent OpenClaw Gateway loop that treats OpenClaw as presence/transport while
keeping JARVIS cognition authoritative.

New modules:

- `jarvis/openclaw_gateway_client.py`
  - stateful websocket loop with reconnect/backoff and heartbeat
  - SecretRef token resolution (`env:` / `file:`)
  - loopback/private-host default guard
- `jarvis/openclaw_event_router.py`
  - routes gateway events through canonical `jarvis.signal.v1`
  - updates continuity/session state per surface
- `jarvis/surface_session_state.py`
  - persists `surface_id`, `session_id`, `operator_identity`, `last_relationship_mode`,
    `last_consciousness_revision`, `last_seen_contract_hash`, and status transitions
- `jarvis/openclaw_reply_orchestrator.py`
  - applies relationship-mode policy and structured pushback before outbound replies
- `jarvis/node_command_broker.py`
  - capability classification (`readonly`, `notification_ui`, `control_plane`, `exec_like`)
  - blocks/reroutes exec-like commands into approval gates
- `jarvis/taskflow_presence_runner.py`
  - heartbeat + reattachment cycle substrate for durable presence runs

New/expanded presence API endpoints:

- `GET /api/presence/sessions`
- `GET /api/presence/gateway-loop`
- `POST /api/presence/gateway-loop/configure`
- `POST /api/presence/gateway-loop/start`
- `POST /api/presence/gateway-loop/stop`
- `POST /api/presence/gateway-loop/pump`
- `POST /api/presence/node-command/broker`
- `POST /api/presence/reply/prepare`
- `POST /api/presence/taskflow-cycle`

CLI additions on `run-once` and `watch`:

- `--openclaw-gateway-ws-url`
- `--openclaw-gateway-token-ref` / `--openclaw-gateway-token-ref-env`
- `--openclaw-gateway-owner-id`
- `--openclaw-gateway-enable`
- `--openclaw-gateway-allow-remote`
- `--openclaw-gateway-connect-timeout`
- `--openclaw-gateway-heartbeat`

## M18 P2: Live Gateway Contract Binding + Continuity Handshake

M18 P2 adds a version-pinned Gateway protocol profile and pairing-aware handshake state.

New module + profile:

- `jarvis/openclaw_protocol_profile.py`
- `jarvis/protocol/openclaw_gateway_v2026_04_2.json`

P2 behavior:

- Gateway client now loads a protocol profile (id/path), renders attach/subscribe/heartbeat frames from templates, and normalizes incoming event aliases.
- Pairing lifecycle is tracked in loop state (`pending`, `approved`, `revoked`, `rotated`) and commands stay disabled until approved.
- Event router now persists additional continuity markers in session metadata:
  - consciousness contract hash
  - user model revision hash
  - pushback calibration revision hint
- Pairing events update known node state when the node is already registered.

New/expanded APIs:

- `GET /api/presence/gateway-profile`
- `POST /api/presence/gateway-loop/configure` now accepts:
  - `client_name`
  - `protocol_profile_id`
  - `protocol_profile_path`

CLI additions on `run-once` and `watch`:

- `--openclaw-gateway-client-name`
- `--openclaw-gateway-profile-id`
- `--openclaw-gateway-profile-path`

## M18 P3: Handshake-Bound Presence + Continuity-Gated Replies

M18 P3 strengthens the live Gateway contract so OpenClaw remains embodiment/transport while JARVIS
remains reasoning authority.

What changed:

- Protocol profile now supports explicit connect handshake binding:
  - `wire.connect_template`
  - `handshake.require_connect_ack`
  - `handshake.ack_events`
  - `handshake.reject_events`
- Gateway loop now tracks handshake lifecycle and only marks command readiness when:
  - gateway connection is live
  - connect handshake is acknowledged (or not required)
  - node pairing is approved
- Reply orchestration now includes continuity envelope checks:
  - session-bound continuity hash validation
  - explicit continuity re-sync notice on hash mismatch
  - optional time-tradeoff framing surface
- TaskFlow presence cycles now expose BOOT/HEARTBEAT checklist extraction and reconnect/missed-session signals.

New gateway status fields:

- `connect_handshake_required`
- `connect_handshake_state` (`pending` / `acked` / `rejected` / `not_required`)
- `connect_handshake_sent_at`
- `connect_handshake_acked_at`
- `connect_handshake_ack_event_type`

## M18 P4: Soak Validation + Continuity Freeze

M18 P4 adds operator-visible validation for three separate trust axes and same-JARVIS continuity.

New runtime capabilities:

- `get_presence_trust_axes(...)`
  - evaluates gateway handshake axis, pairing/token axis, and command-policy axis independently
- `get_presence_continuity_snapshot(...)`
  - captures active vs session continuity for:
    - `contract_hash`
    - `relationship_mode`
    - `user_model_revision`
    - `pushback_calibration_revision`
- `check_presence_continuity_freeze(...)`
  - cross-surface continuity validation report
- `run_openclaw_gateway_soak(...)`
  - bounded soak runner for gateway loop + trust-axis timeline capture

New/expanded presence API endpoints:

- `GET /api/presence/trust-axes`
- `GET /api/presence/continuity-snapshot`
- `POST /api/presence/continuity-freeze-check`
- `POST /api/presence/gateway-loop/soak`
- `POST /api/presence/gateway-loop/node-soak`

### Live Soak Quickstart (Local Gateway)

If `openclaw config get gateway.auth.token` is redacted in your shell tooling, read the local auth token directly
from `~/.openclaw/openclaw.json` and export it before running soak:

```bash
export JARVIS_OPENCLAW_GATEWAY_TOKEN="$(jq -r '.gateway.auth.token' ~/.openclaw/openclaw.json)"
python3 - <<'PY'
from pathlib import Path
from jarvis.runtime import JarvisRuntime

repo = Path("/Users/dankerbadge/Documents/J.A.R.V.I.S")
runtime = JarvisRuntime(db_path=repo / ".jarvis" / "jarvis.db", repo_path=repo)
try:
    runtime.configure_openclaw_gateway_loop(
        ws_url="ws://127.0.0.1:18789",
        token_ref="env:JARVIS_OPENCLAW_GATEWAY_TOKEN",
        protocol_profile_id="openclaw_gateway_v2026_04_2",
        enabled=True,
    )
    print(runtime.run_openclaw_gateway_soak(loops=14, expect_pairing_approved=False)["ok"])
finally:
    runtime.close()
PY
```

## M18 P5: Real Node-Role Embodiment Soak

M18 P5 adds a live node-role soak that validates device pairing trust, reconnect behavior,
token rotation, reject-cycle handling, and cross-surface continuity freeze from a real node host.

New runtime capability:

- `run_openclaw_node_embodiment_soak(...)`
  - starts a real `openclaw node run` process in an isolated profile
  - waits for pending pair request, approves, and verifies connected state
  - captures trust axes (`handshake`, `pairing/token`, `command_policy`)
  - validates reconnect after process restart
  - validates `node.pair.request` + approve token rotation (`old token invalid`, `new token valid`)
  - optionally runs a reject cycle with a second profile
  - performs DM + node continuity freeze check

Presence API endpoint:

- `POST /api/presence/gateway-loop/node-soak`

Request fields:

- `ws_url` (optional)
- `token_ref` (optional)
- `owner_id` (optional, default `primary_operator`)
- `client_name` (optional, default `jarvis`)
- `node_display_name` (optional)
- `profile_prefix` (optional)
- `probe_command` (optional, default `notifications.send`)
- `pairing_timeout_seconds` (optional, default `45.0`)
- `reconnect_timeout_seconds` (optional, default `35.0`)
- `run_reject_cycle` (optional, default `true`)

Quick local invocation:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/presence/gateway-loop/node-soak \
  -H 'Content-Type: application/json' \
  -d '{
    "ws_url": "ws://127.0.0.1:18789",
    "token_ref": "env:JARVIS_OPENCLAW_GATEWAY_TOKEN",
    "owner_id": "primary_operator",
    "run_reject_cycle": true
  }'
```

## M19: Voice, Latency, and Relationship Polish

M19 keeps one Jarvis mind across text, voice, and node surfaces while improving timing and delivery quality.

What changed:

- Voice is now bound to the same reply governance path (continuity + mode + pushback + time framing).
- Reply preparation now emits a latency ladder:
  - `phase_a_presence` (immediate acknowledgement)
  - `phase_b_first_useful` (short useful answer)
  - `phase_c_deep_followup` (deeper reasoning/tradeoff pass)
- Reply rendering now includes explicit relationship-mode framing in output (`equal` / `strategist` / `butler`).
- Tone balance controller added:
  - profile weights (`calmness`, `warmth`, `challenge`, `deference`, `compression`, `humor`)
  - imbalance detection + calibration hints
  - persistent snapshot storage for trend inspection
- Event routing now classifies talk/voice surfaces as channel type `voice`.

New runtime capabilities:

- `prepare_openclaw_voice_reply(...)`
- `get_presence_tone_balance(...)`
- `start_voice_continuity_soak(...)`
- `record_voice_continuity_soak_turn(...)`
- `get_voice_continuity_soak_report(...)`

New/expanded presence API endpoints:

- `POST /api/presence/voice/reply/prepare`
- `GET /api/presence/tone-balance`
- `POST /api/presence/voice/soak/start`
- `POST /api/presence/voice/soak/turn`
- `GET /api/presence/voice/soak/report`
- `GET /api/presence/voice/pack`
- `GET /api/presence/voice/readiness`
- `GET /api/presence/voice/diagnostics`
- `GET /api/presence/voice/tuning`
- `GET /api/presence/voice/tuning/overrides`
- `POST /api/presence/voice/tuning/overrides`
- `POST /api/presence/voice/tuning/overrides/reset`

Quick voice reply invocation:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/presence/voice/reply/prepare \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "Give me the short strategic update first.",
    "surface_id": "voice:owner",
    "session_id": "voice-1",
    "high_stakes": true,
    "uncertainty": 0.68
  }'
```

The prepared voice payload now includes `voice_asset_pack` and a `voice.asset_pack`
summary so OpenClaw/Talk routing can keep voice continuity tied to the active local pack.
This includes `quality_tier` and `continuity_ready` flags for fast runtime health checks.
It also includes `voice_readiness` (checklist + confidence + production-readiness signal).
It now includes `voice_diagnostics` (soak-driven continuity confidence + strict-continuity gates).
It now includes `voice_tuning_profile` (bounded speed/stability/latency profile with confidence).
It now scores clip clarity from pack manifests (`hiss/silence/harmonicity/clarity deltas`) and
feeds that into readiness and tuning decisions.
It now also computes cadence and annunciation quality from pack manifests (`duration/silence/hiss/harmonicity`)
and feeds those into readiness checks, tuning bias, and directive shaping.
When the active pack profile is `actor_match`, tuning applies an actor-likeness cadence anchor.
When movie-match score is strong, directive voice label upgrades to `jarvis-movie-match`.
It now applies continuity smoothing across turns in the same voice session with anti-jitter deadbands
plus low-pass/asymmetric step limits, flow inertia, oscillation guardrails, and a short-history
anchor plus directional follow-through and plateau-release control to prevent abrupt directive drift.

Inspect the active pack directly:

```bash
curl -sS http://127.0.0.1:8765/api/presence/voice/pack
```

Inspect readiness + tuning recommendations:

```bash
curl -sS http://127.0.0.1:8765/api/presence/voice/readiness
```

Inspect continuity diagnostics (pack + soak performance gates):

```bash
curl -sS "http://127.0.0.1:8765/api/presence/voice/diagnostics?limit=200"
```

Inspect tuning profile used by the runtime:

```bash
curl -sS "http://127.0.0.1:8765/api/presence/voice/tuning?limit=200"
```

Inspect manual tuning overrides + recent override events:

```bash
curl -sS "http://127.0.0.1:8765/api/presence/voice/tuning/overrides?events_limit=10"
```

Apply manual tuning overrides (example):

```bash
curl -sS -X POST http://127.0.0.1:8765/api/presence/voice/tuning/overrides \
  -H 'Content-Type: application/json' \
  -d '{
    "patch": {
      "strict_mode_required": true,
      "prefer_stability": true,
      "speed_bias": -0.02,
      "cadence_bias": 0.04,
      "annunciation_bias": 0.05
    },
    "actor": "operator"
  }'
```

### Live Continuity Soak (2–3 Day Run)

Start a run:

```bash
curl -sS -X POST http://127.0.0.1:8765/api/presence/voice/soak/start \
  -H 'Content-Type: application/json' \
  -d '{"label":"m19-talk-soak","metadata":{"window":"3d"}}'
```

Record a turn (repeat during real usage):

```bash
curl -sS -X POST http://127.0.0.1:8765/api/presence/voice/soak/turn \
  -H 'Content-Type: application/json' \
  -d '{
    "run_id": "vsr_...",
    "draft": {
      "text": "Short strategic answer first, then deeper options.",
      "surface_id": "voice:owner",
      "session_id": "talk-1",
      "high_stakes": true,
      "uncertainty": 0.65
    },
    "observed_latencies_ms": {
      "phase_a_presence": 880,
      "phase_b_first_useful": 2400,
      "phase_c_deep_followup": 4900
    },
    "interrupted": true,
    "interruption_recovered": true,
    "expected_mode": "strategist",
    "pushback_outcome": "accepted"
  }'
```

Inspect report:

```bash
curl -sS "http://127.0.0.1:8765/api/presence/voice/soak/report?run_id=vsr_...&limit=500"
```

### Production Voice Path (OpenClaw Shell)

Use one production speech path and keep the local Python loop as fallback only.

Files:

- `configs/openclaw.voice.production.template.json`
- `scripts/setup_openclaw_voice_prod.sh`
- `scripts/start_openclaw_voice_production.sh`

Dry-run (diagnostics + merged preview only):

```bash
./scripts/setup_openclaw_voice_prod.sh
```

Apply merged config to `~/.openclaw/openclaw.json`:

```bash
./scripts/setup_openclaw_voice_prod.sh --apply
```

Install prereqs + apply config in one command:

```bash
./scripts/setup_openclaw_voice_prod.sh --install-prereqs --apply
```

Generated preview:

- `exports/openclaw.voice.production.preview.json`

Local STT model path used by template:

```bash
export WHISPER_CPP_MODEL="/Users/dankerbadge/Documents/J.A.R.V.I.S/.jarvis/stt/models/ggml-base.en.bin"
```

Optional provider keys:

```bash
export ELEVENLABS_API_KEY="..."
export OPENAI_API_KEY="..."
```

### One-Command Production Voice Boot

Use this from a fresh terminal. It applies production OpenClaw voice config, restarts
Gateway, and starts JARVIS API for the OpenClaw Talk surface.

```bash
cd /Users/dankerbadge/Documents/J.A.R.V.I.S
./scripts/start_openclaw_voice_production.sh --open-dashboard
```

Notes:

- Production voice surface: OpenClaw Talk Mode (macOS app / companion).
- Fallback test harness only: `python scripts/jarvis_voice_chat.py --start-server`
- The production config auto-selects a working provider in this order:
  `elevenlabs` (if key set) -> `openai` (if key set) -> `microsoft`.
- Use `--foreground-server` if you want JARVIS API attached to the current terminal.
- Startup now prints active voice pack continuity info (pack/profile/clip counts/quality tier) when present.
- Runtime readiness now also evaluates manifest-derived clip clarity quality when available.

### Actor Voice Isolation Pack (No-Noise Training Clips)

Build and install a curated JARVIS actor clip pack into workspace assets:

```bash
python scripts/install_jarvis_actor_voice_pack.py --profile strict
```

Output location:

- `.jarvis/voice/training_assets/jarvis_actor_isolated_v1`
- `.jarvis/voice/ACTIVE_VOICE_PACK.json`
- `exports/JARVIS_ACTOR_ISOLATED_VOICE_PACK_STRICT_<date>.zip`

Use `--profile extended` for broader phrase coverage when you need more variety.
Use `--profile actor_match` for higher actor-likeness coverage with bounded clarity guards and
automatic outlier trimming based on robust actor-match scoring.
`actor_match` also applies movie-reference window weighting from `analysis/jarvis_study/voice_study_metrics.json`.

Build the stronger `master_v2` refinement pack from installed actor clips:

```bash
source .venv-voice/bin/activate
python scripts/build_jarvis_actor_voice_master_v2.py
```

This writes and activates:

- `.jarvis/voice/training_assets/jarvis_actor_isolated_v2_master`
- `.jarvis/voice/ACTIVE_VOICE_PACK.json`
- `exports/JARVIS_ACTOR_ISOLATED_VOICE_PACK_MASTER_V2_<date>.zip`

Confirm backend state:

```bash
curl -sS http://127.0.0.1:8765/api/cognition/config
```
