# M23 Phase-1 Progress (2026-04-18)

## Implemented

- Extracted workflow internals from `jarvis/runtime.py` into a dedicated package:
  - `jarvis/workflows/plan_repository.py`
  - `jarvis/workflows/planner.py`
  - `jarvis/workflows/executor.py`
  - `jarvis/workflows/__init__.py`
- Switched `JarvisRuntime` construction path to instantiate extracted components:
  - `WorkflowPlanRepository`
  - `WorkflowPlanner`
  - `WorkflowExecutor`
- Added regression coverage:
  - `tests/test_workflow_extraction.py`
- Added explicit workflow step-state contract:
  - `jarvis/workflows/models.py` (`queued`, `running`, `blocked`, `approved`, `succeeded`, `failed`, `compensated`)
- Added append-only step-attempt journal:
  - `plan_step_attempts` table
  - `PlanRepository.record_step_attempt(...)`
  - `PlanRepository.list_step_attempts(...)`
  - deterministic `attempt_number` sequencing per `(plan_id, step_id)`
- Added workflow transition validation:
  - explicit allowed state transitions in `jarvis/workflows/models.py`
  - `PlanRepository.record_step_attempt(...)` now rejects invalid transitions
- Added legacy-schema compatibility:
  - auto-migrate `plan_step_attempts` table when `attempt_number` is missing
  - backfill historical attempt numbers during migration
- Wired step-attempt state writes in executor flow:
  - queued at plan save
  - running at execution start
  - approved on valid approval path
  - blocked on approval/warrant gating
  - failed on missing tool or tool execution exception
  - succeeded on completed tool call
- Removed duplicate workflow class definitions from `jarvis/runtime.py` and retained compatibility aliases:
  - `PlanRepository = WorkflowPlanRepository`
  - `Planner = WorkflowPlanner`
  - `Executor = WorkflowExecutor`
- Added runtime accessor and test coverage for step attempts:
  - `JarvisRuntime.list_plan_step_attempts(...)`
  - `tests/test_workflow_step_states.py`
- Added reasoning-ledger scaffold:
  - `jarvis/reasoning/schema.py`
  - `jarvis/reasoning/store.py`
  - `jarvis/reasoning/tracer.py`
- Added reasoning replayer scaffold:
  - `jarvis/reasoning/replayer.py`
  - replay APIs exposed through runtime
- Added mirrored reasoning writes from workflow execution:
  - executor now opens/finalizes per-step traces and appends decision events
- Added runtime reasoning accessors:
  - `JarvisRuntime.list_decision_traces(...)`
  - `JarvisRuntime.get_decision_trace(...)`
  - `JarvisRuntime.replay_step_decision_timeline(...)`
  - `JarvisRuntime.replay_plan_decision_timeline(...)`
- Split review-service bootstrap out of `runtime.py`:
  - `jarvis/runtime_services.py`
  - runtime now uses `build_default_review_service()`
- Added reasoning capture tests:
  - `tests/test_reasoning_trace_capture.py`
- Added reasoning replay tests:
  - `tests/test_reasoning_replayer.py`
- Added decision candidate/selection capture:
  - `decision_candidates` and `decision_selected_actions` tables
  - tracer APIs for candidate add/select
  - executor emits candidate + selected action per step trace
- Added suggestion-engine scaffold:
  - `jarvis/suggestions/models.py`
  - `jarvis/suggestions/detectors.py`
  - `jarvis/suggestions/ranker.py`
  - `jarvis/suggestions/engine.py`
  - runtime API: `JarvisRuntime.list_suggestion_candidates(...)`
- Added suggestion tests:
  - `tests/test_suggestion_engine.py`
- Added project-planning scaffold:
  - `jarvis/planning/project_graph.py`
  - `jarvis/planning/milestones.py`
  - `jarvis/planning/next_action_ranker.py`
  - `jarvis/planning/devloop.py`
  - runtime APIs:
    - `ingest_project_signal(...)`
    - `list_project_graph(...)`
    - `list_project_actions(...)`
    - `propose_project_next_actions(...)`
    - `summarize_project_milestones(...)`
- Added project-planning tests:
  - `tests/test_project_planning_scaffold.py`
- Added workflow compensation journal + reasoning integration:
  - `plan_step_compensations` table in `jarvis/workflows/plan_repository.py`
  - repository APIs:
    - `record_step_compensation(...)`
    - `list_step_compensations(...)`
  - replay integration:
    - `jarvis/reasoning/replayer.py` now merges compensation events
  - executor integration:
    - protected-step tool failures now write compensation records and `StepState.COMPENSATED`
    - reasoning traces emit `compensation.applied` and finalize as `compensated`
  - runtime API:
    - `JarvisRuntime.list_plan_step_compensations(...)`
- Added workflow compensation tests:
  - `tests/test_workflow_compensation.py`
- Added implicit suggestion-feedback loop into learning materialization:
  - `jarvis/learning/datasets.py` now infers feedback from step-attempt transitions
    (for example blocked -> approved/succeeded) when explicit feedback is absent
  - inferred feedback is persisted via `SuggestionFeedbackStore` with metadata:
    - `inferred: true`
    - `source: step_attempts`
- Added learning feedback inference test:
  - `tests/test_learning_materialization.py::test_materialization_infers_feedback_from_blocked_to_success_transition`
- Added learning policy promotion gates:
  - runtime gate evaluator in `JarvisRuntime._evaluate_learning_promotion_gate(...)`
  - `promote_learning_policy(...)` now evaluates policy report + gate thresholds before promotion
  - gate controls:
    - `min_examples`
    - `min_avg_utility`
    - `min_top_action_score`
    - `min_top_action_samples`
    - `require_ranked_actions`
    - `enforce_gates`
  - promotion response now includes:
    - `promoted`
    - `gate`
    - `report`
- Added runtime artifact adapters into project planning:
  - `JarvisRuntime.ingest_project_signals_from_plan_outcomes(...)`
  - `JarvisRuntime.ingest_project_signals_from_review_artifacts(...)`
  - these bridge runtime outcome/review state into `ProjectDevLoop` signal ingestion.
- Added bulk project-signal backfill path:
  - `JarvisRuntime.backfill_project_signals(...)`
  - supports multi-source ingestion:
    - plan outcomes
    - review artifacts
    - merge outcomes
  - uses deduped signal envelopes before routing into `ProjectDevLoop`.
- Added project backfill idempotency markers:
  - `project_backfill_markers` table in `jarvis/planning/project_graph.py`
  - new store methods:
    - `has_backfill_marker(...)`
    - `record_backfill_marker(...)`
  - runtime backfill now supports:
    - `skip_seen` (default `True`)
    - `skipped_existing_count` in response
  - repeated backfills now avoid re-ingesting previously seen markers unless `skip_seen=False`.
- Added bounded backfill source cursors + resume hints:
  - `JarvisRuntime.backfill_project_signals(...)` now supports:
    - global cursor: `since_updated_at`
    - per-source overrides:
      - `since_outcomes_at`
      - `since_review_artifacts_at`
      - `since_merge_outcomes_at`
  - source adapters now honor cursor filters:
    - `PlanRepository.list_recent_outcomes_global(..., since_recorded_at=...)`
    - `SecurityManager.list_recent_review_artifacts(..., since_updated_at=...)`
    - `SecurityManager.list_recent_merge_outcomes(..., since_updated_at=...)`
  - backfill response now exposes cursor progression:
    - `source_cursors.<source>.since`
    - `source_cursors.<source>.next_since`
    - `source_cursors.<source>.fetched_count`
    - `next_since_updated_at`
  - review/merge dedupe keys now include source `updated_at` so fresh updates can be re-ingested when timestamps advance.
- Added persisted cursor profiles for incremental backfill resume:
  - new table in `jarvis/planning/project_graph.py`:
    - `project_backfill_cursors`
  - new store methods:
    - `upsert_backfill_cursor(...)`
    - `list_backfill_cursors(...)`
  - new runtime helpers:
    - `get_project_backfill_cursor_profile(...)`
    - `save_project_backfill_cursor_profile(...)`
    - `save_project_backfill_cursors_from_result(...)`
  - supports per-project, per-profile, per-source cursor checkpoints plus global `next_since_updated_at`.
- Added runtime cursor-profile-aware backfill defaults:
  - `backfill_project_signals(...)` now supports:
    - `load_since_from_cursor_profile`
    - `cursor_profile_key`
  - when enabled, unset `since_*` filters are auto-resolved from saved cursor profile state.
  - backfill response now includes:
    - `effective_since`
    - `cursor_profile` (`loaded`, `profile_key`, `defaults_applied`)
- Added operator helper to preview effective backfill inputs before ingest:
  - `preview_project_backfill_cursor_inputs(...)`
  - returns resolved global/per-source `since_*` and cursor-profile provenance.
- Added one-step atomic backfill + cursor persistence helper:
  - `run_project_backfill_with_cursor_profile(...)`
  - flow:
    - run backfill (optionally loading `since_*` defaults from cursor profile)
    - persist returned cursor state
    - return both `backfill` and updated `cursor_profile` in one response.
- Added compact run-and-summarize helper for operator ergonomics:
  - `run_project_backfill_with_cursor_profile_summary(...)`
  - includes compact summary output:
    - counts (`signals_count`, `skipped_existing_count`, `would_ingest_count`, `persisted_marker_count`)
    - source/type aggregates (`source_counts`, `signal_type_counts`, `top_signal_types`)
    - cursor movement (`from`, `to`, `after`, `changed`, `persisted`) for global + per-source cursors
  - added reusable summarizer:
    - `summarize_project_backfill_run(...)`.
- Added structured dry-run support for backfill helpers:
  - `backfill_project_signals(..., dry_run=True)` now resolves candidate signals without:
    - ingesting signals/actions
    - writing backfill markers
  - dry-run diagnostics include:
    - `dry_run`
    - `would_ingest_count`
    - `persisted_marker_count`
  - `run_project_backfill_with_cursor_profile(..., dry_run=True)` now:
    - runs dry-run backfill
    - skips cursor persistence
    - returns `cursor_persisted: false`.
- Added backfill input provenance diagnostics:
  - `backfill_project_signals(...)` and `preview_project_backfill_cursor_inputs(...)` now include
    `cursor_profile.resolution_source` with per-source labels:
    - `explicit`
    - `profile.source`
    - `profile.global`
    - `global.explicit`
    - `global.profile`
    - `none`
  - this makes it explicit which input path resolved each effective `since_*` value.
- Added supporting security accessors:
  - `SecurityManager.list_recent_review_artifacts(...)`
  - `SecurityManager.list_recent_merge_outcomes(...)`
- Added learning promotion presets + audit trail:
  - presets in runtime:
    - `strict`
    - `conservative`
    - `aggressive`
  - adaptive-policy gate overrides supported via:
    - `learning.promotion_gates.defaults`
    - `learning.promotion_gates.presets.<preset>`
  - `promote_learning_policy(...)` now records audit entries for both blocked and promoted decisions.
  - new runtime API:
    - `list_learning_promotion_audits(...)`
  - new registry audit storage:
    - `learning_policy_audit` table
    - `LearningPolicyRegistry.record_promotion_audit(...)`
    - `LearningPolicyRegistry.list_promotion_audits(...)`
- Added promotion history linking:
  - promoted policy metadata now stores `promotion_audit_id`
  - new registry helper:
    - `LearningPolicyRegistry.update_policy_metadata(...)`
  - runtime promotion flow now writes audit then links policy metadata to the audit id.
- Added learning gate profile operator controls:
  - runtime APIs:
    - `get_learning_gate_profile(...)`
    - `set_learning_gate_profile(...)`
  - profile updates flow through adaptive policy via:
    - `learning.promotion_gates.defaults`
    - `learning.promotion_gates.presets.<preset>`
- Added policy lifecycle controls without deleting historical lineage:
  - registry policy status fields:
    - `policy_status`
    - `superseded_by_policy_id`
    - `disabled_at`
    - `disabled_by`
    - `disable_reason`
  - runtime lifecycle APIs:
    - `disable_learning_policy(...)`
    - `activate_learning_policy(...)`
    - `rollback_learning_policy(...)`
  - lifecycle events are recorded in `learning_policy_audit` with decisions:
    - `superseded`
    - `disabled`
    - `activated`
    - `rolled_back`
- Added policy-status transition guardrail for explicit operator intent:
  - `LearningPolicyRegistry.set_policy_status(...)` now validates transition edges.
  - direct `superseded -> disabled` is blocked by default and requires:
    - `allow_superseded_disable=True`
  - runtime API wiring:
    - `disable_learning_policy(..., allow_superseded_disable=False)`
  - when explicitly allowed, policy metadata records:
    - `allow_superseded_disable: true`
- Added active-policy resolver helper:
  - runtime API:
    - `get_active_learning_policy(task_family=...)`
  - returns current active champion policy for downstream ranking/execution lookup in a single call.
- Added optional fallback behavior for active-policy diagnostics:
  - `get_active_learning_policy(task_family=..., fallback_to_latest=True)`
  - when no active policy exists, resolver can return the latest policy in the family for operator/debug surfaces.
- Added tests:
  - `tests/test_project_signal_adapters.py`
  - `tests/test_learning_materialization.py::test_promotion_gate_blocks_without_examples`
  - `tests/test_learning_materialization.py::test_promotion_presets_adaptive_overrides_and_audits`
  - `tests/test_learning_materialization.py::test_learning_gate_profile_and_policy_lifecycle_controls`
- Expanded lifecycle test coverage for active-policy resolution transitions:
  - `tests/test_learning_materialization.py::test_learning_gate_profile_and_policy_lifecycle_controls`
    now asserts active champion before disable, after disable (`None`), and after rollback.
- Added transition-guardrail regression coverage:
  - `tests/test_learning_materialization.py::test_policy_status_guardrail_requires_explicit_superseded_disable_intent`
    validates blocked transition without flag and successful explicit override path.
- Added active-policy fallback regression:
  - `tests/test_learning_materialization.py::test_get_active_learning_policy_fallback_to_latest_when_no_active`
    validates `None` without fallback and latest-policy resolution with fallback enabled.
- Added full transition-matrix regression coverage:
  - `tests/test_learning_materialization.py::test_policy_status_transition_matrix_guardrails`
  - covers status edges for `active`, `disabled`, `superseded`, including:
    - no-op transitions
    - allowed directional transitions
    - blocked `superseded -> disabled` without explicit intent
    - allowed override path with `allow_superseded_disable=True`.
- Added policy-transition audit enrichment:
  - transition audits now carry explicit:
    - `from_status`
    - `to_status`
  - applied to lifecycle and promotion transition decisions (`superseded`, `disabled`, `activated`, `rolled_back`, `promoted`) for cleaner timeline filtering.
- Expanded lifecycle assertions for transition audit metadata:
  - `tests/test_learning_materialization.py` now verifies `from_status`/`to_status` on:
    - disable
    - rollback
    - explicit superseded-disable override.
- Added cursor backfill regression:
  - `tests/test_project_signal_adapters.py::test_bulk_backfill_project_signals_since_cursor_filters_sources`
- Added cursor profile persistence regression:
  - `tests/test_project_signal_adapters.py::test_backfill_cursor_profile_persistence_helpers`
- Added cursor-profile auto-load regression:
  - `tests/test_project_signal_adapters.py::test_backfill_project_signals_can_load_since_defaults_from_cursor_profile`
  - validates profile-driven filtering and preview of effective `since_*` inputs.
- Added atomic backfill helper regression:
  - `tests/test_project_signal_adapters.py::test_run_project_backfill_with_cursor_profile_persists_and_reuses_cursors`
  - validates first run ingestion from seeded cursor and second run reusing persisted cursor.
- Added dry-run backfill regression:
  - `tests/test_project_signal_adapters.py::test_run_project_backfill_with_cursor_profile_dry_run_does_not_ingest_or_persist`
  - validates dry-run candidate resolution with zero marker persistence and zero cursor updates.
- Added cursor provenance diagnostics regressions:
  - `tests/test_project_signal_adapters.py::test_preview_backfill_cursor_inputs_resolution_source_labels`
  - expanded assertions in cursor-profile backfill tests to validate `resolution_source` labels.
- Added run-and-summarize regressions:
  - `tests/test_project_signal_adapters.py::test_run_project_backfill_with_cursor_profile_summary_compact_output`
  - `tests/test_project_signal_adapters.py::test_run_project_backfill_with_cursor_profile_summary_dry_run_cursor_movement`
  - validates summary aggregates and cursor movement semantics for real and dry-run paths.
- Added bounded candidate-pool sampling metadata for backfill auditability:
  - `backfill_project_signals(...)` now returns:
    - `sampling.candidate_pool_count`
    - `sampling.candidate_scan_limit`
    - `sampling.candidate_scanned_count`
    - `sampling.candidate_unscanned_count`
  - this makes dry-run bounded-scan effects explicit when deduped candidate pools exceed scan caps.
- Added compact summary payload filtering for large runs:
  - `run_project_backfill_with_cursor_profile_summary(...)` now supports:
    - `include_raw_signals` (default `False`)
  - default summary mode now omits raw signal rows from `backfill.signals` and exposes:
    - `backfill.signals_omitted`
    - `backfill.signals_omitted_count`
  - operators can opt into full raw payloads with `include_raw_signals=True`.
- Added compact summary filtering for ingestion payloads:
  - `run_project_backfill_with_cursor_profile_summary(...)` now supports:
    - `include_raw_ingestions` (default `False`)
  - default summary mode now omits raw ingestion rows from `backfill.ingestions` and exposes:
    - `backfill.ingestions_omitted`
    - `backfill.ingestions_omitted_count`
  - operators can opt into full ingestion payloads with `include_raw_ingestions=True`.
- Added summary exposure of candidate-pool diagnostics:
  - `summarize_project_backfill_run(...)` now includes:
    - `candidate_pool_count`
    - `candidate_scan_limit`
    - `candidate_scanned_count`
    - `candidate_unscanned_count`
- Added per-source sampling diagnostics for scan-budget visibility:
  - `backfill_project_signals(...).sampling` now includes:
    - `candidate_pool_by_source`
    - `candidate_scanned_by_source`
    - `candidate_unscanned_by_source`
  - `summarize_project_backfill_run(...)` now surfaces these same per-source counters in compact summary output.
- Added optional summary caps for high-cardinality aggregates:
  - `summarize_project_backfill_run(...)` now supports:
    - `max_source_counts`
    - `max_signal_type_counts`
  - summary now includes cap metadata:
    - `source_counts_metadata` (`total_keys`, `returned_keys`, `omitted_keys`, `cap`)
    - `signal_type_counts_metadata` (`total_keys`, `returned_keys`, `omitted_keys`, `cap`)
  - `run_project_backfill_with_cursor_profile_summary(...)` now forwards both cap parameters.
- Added operator docs for cursor-profile workflows:
  - `M23_BACKFILL_CURSOR_RUNBOOK.md` (preview, dry-run, compact summary, cursor persist, override pattern)
  - `README.md` now links the M23 backfill cursor runbook.
- Added CLI/operator wrapper for backfill summary execution:
  - new command:
    - `python -m jarvis.cli plans backfill-project-signals <project_id> ...`
  - wraps `run_project_backfill_with_cursor_profile_summary(...)` with:
    - safe dry-run default (requires `--execute` to persist)
    - profile presets (`quick`, `balanced`, `deep`)
    - explicit source/cursor overrides and raw payload toggles
    - optional summary cap flags:
      - `--max-source-counts`
      - `--max-signal-type-counts`
    - output modes:
      - `--summary-only`
      - `--json-compact`
      - `--output pretty`
      - `--output warnings`
      - `--color auto|always|never`
    - operator warning hints:
      - `operator_hints_count`
      - `operator_hints[]` with warning codes:
        - `candidate_scan_clipped`
        - `source_counts_capped`
        - `signal_type_counts_capped`
      - clipping recommendations are preset-aware (for example `quick` suggests `--preset balanced`).
      - warning suppression support:
        - `--suppress-warning-code <code>` (repeatable)
        - payload metadata:
          - `operator_hints_total_count`
          - `operator_hints_suppressed_count`
          - `operator_hints_suppression` (`requested_codes`, `applied_codes`, `suppressed_count`, `remaining_count`, `total_count`)
      - warning severity filter support:
        - `--min-warning-severity info|warning|error`
        - payload metadata:
          - `operator_hints_filtered_by_severity_count`
          - `operator_hints_severity_filter` (`requested_min_severity`, `filtered_out_count`, `remaining_count`, `total_count`)
      - optional exit code policy for automation:
        - `--exit-code-policy off|warning|error`
        - `--warning-exit-code <N>`
        - `--error-exit-code <N>`
        - payload metadata:
          - `exit_code_policy`
          - `max_warning_severity`
          - `exit_code`
          - `exit_triggered`
  - command output now includes:
    - `preset`
    - `resolved_options`
    - full `result` payload for operator diagnostics.
- Added new regressions for compact/sampling semantics:
  - `tests/test_project_signal_adapters.py::test_backfill_project_signals_dry_run_sampling_reports_pre_cap_pool`
  - expanded summary tests to validate:
    - default signal omission behavior
    - `include_raw_signals=True` passthrough behavior
    - default ingestion omission behavior
    - `include_raw_ingestions=True` passthrough behavior
    - summary candidate-pool counters.
  - expanded sampling assertions to validate per-source pool/scanned/unscanned diagnostics.
  - added cap-regression:
    - `tests/test_project_signal_adapters.py::test_run_project_backfill_with_cursor_profile_summary_applies_count_caps`
    - validates key-capping and `*_metadata` omission diagnostics.
- Added CLI wrapper regression coverage:
  - `tests/test_cli_backfill_project_signals.py`
  - validates:
    - preset resolution + override precedence
    - default dry-run command behavior
    - execute-mode persistence with raw payload opt-ins.
    - summary cap option resolution (`max_source_counts`, `max_signal_type_counts`).
    - summary-only compact output shape.
    - operator hint emission for clipping + capped aggregates.
    - pretty output rendering with explicit color-mode controls.
    - warnings-only output mode for automation checks (clean + warning paths).
    - warning suppression by code (partial + full suppression paths).
    - warning severity filtering (including high-threshold suppression to zero warnings).
    - exit-code policy behavior (`warning` exits, `error` non-exit for warning-only hints).
- Added warning-policy profiles for backfill CLI automation ergonomics:
  - parser support:
    - `--warning-policy-profile default|strict|quiet`
  - resolver now correctly applies profile defaults when explicit warning flags are omitted.
  - explicit warning flags continue to override profile defaults.
  - warnings-only payload and pretty output now include `warning_policy_profile`.
  - unattended env defaults:
    - `JARVIS_BACKFILL_WARNING_POLICY_PROFILE`
    - `JARVIS_BACKFILL_SUPPRESS_WARNING_CODES`
    - `JARVIS_BACKFILL_MIN_WARNING_SEVERITY`
    - `JARVIS_BACKFILL_EXIT_CODE_POLICY`
    - `JARVIS_BACKFILL_WARNING_EXIT_CODE`
    - `JARVIS_BACKFILL_ERROR_EXIT_CODE`
  - env suppression values are merged with profile defaults and explicit `--suppress-warning-code` values.
  - new regression coverage:
    - profile resolution defaults/overrides (`strict`, `quiet`)
    - `quiet` profile suppression behavior
    - `strict` profile exit-code behavior
    - env-driven profile/suppression resolution and command-path suppression
    - env-driven min-severity + exit-policy + exit-code behavior
- Added CI-facing runbook examples for warning-policy modes:
  - strict gate mode (non-zero exits on warning/error)
  - quiet monitor mode (suppressed capping noise, zero-exit monitoring)
- Added machine-readable warning-policy config support for non-shell operators:
  - CLI flag:
    - `--warning-policy-config /abs/path/warning-policy.json`
  - config fields:
    - `warning_policy_profile`
    - `suppress_warning_codes`
    - `min_warning_severity`
    - `exit_code_policy`
    - `warning_exit_code`
    - `error_exit_code`
  - resolution precedence:
    - explicit CLI flags > config file > env defaults > profile defaults
  - output now includes:
    - `warning_policy_config_path`
  - regression coverage:
    - config-driven resolver behavior
    - config-driven command exit policy behavior
- Added repo-level warning-policy defaults for team-wide deterministic automation:
  - automatic config discovery (when explicit config flag is absent):
    - `<repo>/.jarvis/backfill.warning_policy.json`
    - `<repo>/.jarvis/backfill_warning_policy.json`
  - explicit config continues to override repo defaults.
  - output now includes:
    - `warning_policy_config_source` (`explicit` or `repo_default`)
  - regression coverage:
    - resolver repo-default discovery
    - explicit-over-repo precedence
    - command-path repo-default exit policy behavior
- Added reusable scheduled backfill snippet pack:
  - `configs/backfill_workflow_snippets/README.md`
  - `configs/backfill_workflow_snippets/warning-policy-strict.json`
  - `configs/backfill_workflow_snippets/warning-policy-quiet.json`
  - `configs/backfill_workflow_snippets/github-actions-backfill-strict.yml`
  - `configs/backfill_workflow_snippets/github-actions-backfill-quiet.yml`
  - `scripts/run_backfill_project_signals.sh` (wrapper for schedulers/CI)
- Expanded operator docs for precedence debugging and snippet discoverability:
  - `M23_BACKFILL_CURSOR_RUNBOOK.md` now includes:
    - reusable template references
    - precedence debugging quick-check guidance
  - `README.md` M23 section now links snippet pack docs.
- Added warning-policy provenance telemetry block for automation dashboards:
  - backfill payloads now include:
    - `warning_policy_resolution`
  - provenance includes source attribution for:
    - profile
    - min severity
    - exit policy
    - warning/error exit codes
    - suppression code sources
  - warnings-only output includes the same provenance block.
  - pretty output now surfaces quick provenance hints:
    - `profile_source`
    - `exit_policy_source`
- Added warnings-only audit export helper for scheduled jobs:
  - `scripts/export_backfill_warning_audit.py`
  - helper behavior:
    - runs backfill in `--output warnings --summary-only --json-compact` mode
    - writes timestamped audit artifacts to `output/backfill_warning_audit/*.json`
    - preserves CLI exit code for gate semantics while still exporting payload
    - enriches payload with `_audit` metadata (`command`, `exit_code`, `exported_at`)
  - scheduled snippet templates now include artifact upload patterns:
    - `configs/backfill_workflow_snippets/github-actions-backfill-strict.yml`
    - `configs/backfill_workflow_snippets/github-actions-backfill-quiet.yml`
- Added compact dashboard-facing policy payload mode:
  - CLI:
    - `--output policy`
  - emits compact JSON with:
    - warning-policy profile/config/provenance
    - warning status/count/codes
    - exit policy and trigger outcome
    - compact signal summary
  - new regression coverage:
    - `tests/test_cli_backfill_project_signals.py::test_cmd_plans_backfill_project_signals_policy_output_mode`
- Added per-field fallback telemetry for invalid warning-policy inputs:
  - `warning_policy_resolution.fallbacks[]`
  - `warning_policy_resolution.has_fallbacks`
  - fallback events now capture:
    - field name
    - invalid input value
    - input source
    - fallback value + fallback source
  - new regression coverage:
    - `tests/test_cli_backfill_project_signals.py::test_resolve_project_backfill_options_invalid_env_values_emit_fallbacks`
- Added warning-policy resolution checksum for drift detection:
  - payloads now include:
    - `warning_policy_checksum`
  - checksum is derived from canonicalized `warning_policy_resolution`.
  - exposed across:
    - default JSON output
    - warnings output
    - policy output
    - pretty output header line
- Added policy-audit drift comparison helper:
  - `scripts/compare_backfill_policy_audits.py`
  - compares two warning-audit artifacts and reports field-level deltas for:
    - policy profile/checksum/config source/path
    - exit policy / max warning severity
    - warning-code set
  - default behavior exits non-zero on detected drift (configurable).
- Added warning-policy drift guardrails to audit export flow:
  - `scripts/export_backfill_warning_audit.py` now supports:
    - `--compare-with-latest`
    - `--baseline-audit`
    - `--enforce-stable-policy-source`
    - `--enforce-stable-policy-checksum`
    - `--drift-exit-code`
  - export payload `_audit.policy_drift` now records:
    - baseline path
    - changed fields/differences
    - guardrail flags and violations
    - effective exit decision metadata
  - strict scheduled template now enforces guardrails by default:
    - `configs/backfill_workflow_snippets/github-actions-backfill-strict.yml`
  - quiet scheduled template now compares against latest without hard-fail:
    - `configs/backfill_workflow_snippets/github-actions-backfill-quiet.yml`
- Added audit rollup helper for warning telemetry:
  - `scripts/summarize_backfill_warning_audits.py`
  - summarizes recent artifacts with:
    - total runs / status counts
    - warning-code counts
    - policy checksum distribution
    - drift + guardrail trigger counts
    - latest run snapshot per project
- Added compact multi-project dashboard rollup mode:
  - `scripts/summarize_backfill_warning_audits.py` now supports:
    - `--rollup-mode full|dashboard`
  - dashboard mode emits one compact payload with:
    - aggregate totals
    - per-project run counts
    - per-project drift/guardrail counters
    - latest run snapshot per project (`status`, warning profile/checksum, path)
  - new regression coverage in:
    - `tests/test_backfill_audit_scripts.py::test_summarize_and_prune_scripts`
- Added optional dashboard threshold alerts:
  - `scripts/summarize_backfill_warning_audits.py` now supports:
    - `--dashboard-alert-guardrail-triggered-count-threshold`
    - `--dashboard-alert-guardrail-triggered-rate-threshold`
    - `--dashboard-alert-policy-drift-changed-count-threshold`
    - `--dashboard-alert-policy-drift-changed-rate-threshold`
    - `--dashboard-alert-project-guardrail-triggered-count-threshold`
  - dashboard output now includes `alerts` with:
    - `enabled` / `triggered`
    - threshold values
    - computed metrics and triggered rules
  - supports zero-run `--allow-empty` dashboard payloads with normalized alert metadata.
- Added optional baseline pin mode for strict scheduled guardrails:
  - strict workflow template now supports:
    - `BACKFILL_POLICY_BASELINE_AUDIT`
  - when set, strict export uses `--baseline-audit` against a fixed artifact path.
- Added optional strict baseline-required guardrail:
  - audit exporter now supports:
    - `--require-baseline`
    - `--missing-baseline-exit-code`
  - strict template exposes:
    - `BACKFILL_REQUIRE_BASELINE=true|false`
  - policy drift audit metadata now includes:
    - `baseline_missing`
    - `require_baseline`
    - `missing_baseline_exit_code`
- Added compact policy-drift summary mode for fast terminal/dashboard triage:
  - `scripts/compare_backfill_policy_audits.py` now supports:
    - `--summary-only`
    - `--json-compact`
  - summary output includes:
    - changed flag
    - changed field count/list
    - before/after profile + checksum
- Added per-project retention/pruning helper for audit artifacts:
  - `scripts/prune_backfill_warning_audits.py`
  - supports:
    - per-project retention (`--keep-per-project`)
    - optional age cutoff (`--max-age-hours`)
    - dry-run default with explicit `--execute`
- Added optional audit export field minimization profile for storage control:
  - `scripts/export_backfill_warning_audit.py` now supports:
    - `--export-profile full|minimal`
  - minimal profile retains drift-compare/summarize compatibility while reducing stored fields.
  - scheduled templates now expose export-profile toggles:
    - strict template: `BACKFILL_AUDIT_EXPORT_PROFILE` (default `full`)
    - quiet template: `BACKFILL_AUDIT_EXPORT_PROFILE` (default `minimal`)
- Added high-frequency minimal export tuning knobs + metadata:
  - `scripts/export_backfill_warning_audit.py` minimal profile now supports explicit controls:
    - `--minimal-warning-code-limit`
    - `--minimal-omit-signal-summary`
    - `--minimal-omit-policy-drift-differences`
  - minimal payload now carries compact tuning metadata:
    - `warning_code_limit`
    - `warning_codes_truncated`
    - `_audit.minimal_export`
  - scheduled templates now surface high-frequency env controls:
    - `BACKFILL_AUDIT_MIN_WARNING_CODE_LIMIT`
    - `BACKFILL_AUDIT_MIN_OMIT_SIGNAL_SUMMARY`
    - `BACKFILL_AUDIT_MIN_OMIT_POLICY_DRIFT_DIFFERENCES`
  - snippet docs + runbook examples now include high-frequency lean export mode.
- Added compare-helper projection profiles for narrower drift contracts:
  - `scripts/compare_backfill_policy_audits.py` now supports:
    - `--projection-profile full|policy_core`
  - `policy_core` ignores path/severity/code noise and compares policy core fields only.
- Added optional soft-noise guardrail mode for export enforcement:
  - `scripts/export_backfill_warning_audit.py` now supports:
    - `--drift-projection-profile full|policy_core`
    - `--enforce-stable-policy-core`
  - drift audit metadata now includes:
    - `projection_profile`
    - `policy_core_changed`
    - `policy_core_changed_fields`
  - this allows warning-code/severity/path churn to remain soft noise while still enforcing policy-core drift contracts.
- Added compare/summarize bridge payload helper for scheduled chat/inbox deltas:
  - `scripts/build_backfill_warning_bridge.py`
  - emits compact per-project latest + delta payloads with:
    - policy drift changed fields (projection-aware)
    - warning count deltas
    - status/guardrail transition markers
  - scheduled workflow templates now emit bridge artifacts:
    - `configs/backfill_workflow_snippets/github-actions-backfill-strict.yml`
    - `configs/backfill_workflow_snippets/github-actions-backfill-quiet.yml`
  - bridge payload is uploaded alongside warning-audit artifacts.
- Added operator-facing bridge markdown renderer:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--format markdown`
    - `--markdown-max-projects`
  - markdown briefing includes:
    - compact header metrics for window/projection/project counts
    - per-project latest state + delta fields
    - optional truncation note when project rows are capped
  - JSON output remains default/backward-compatible for scheduled integrations.
- Added optional bridge gating thresholds for burst detection:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--bridge-alert-policy-drift-count-threshold`
    - `--bridge-alert-policy-drift-rate-threshold`
    - `--bridge-alert-guardrail-count-threshold`
    - `--bridge-alert-guardrail-rate-threshold`
    - `--bridge-alert-exit-code`
  - bridge payload now includes `alerts` with:
    - `enabled` / `triggered`
    - triggered rules and metrics
    - threshold snapshot + configured exit code
  - script now returns configured non-zero exit code when alert rules trigger.
  - scheduled workflow templates now expose bridge alert env toggles in bridge step:
    - `configs/backfill_workflow_snippets/github-actions-backfill-strict.yml`
    - `configs/backfill_workflow_snippets/github-actions-backfill-quiet.yml`
- Added optional bridge severity tiers by rule family:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--bridge-alert-policy-drift-severity warn|error`
    - `--bridge-alert-guardrail-severity warn|error`
  - alert payload now includes:
    - `triggered_warn_rules`
    - `triggered_error_rules`
    - `max_triggered_severity`
    - `exit_triggered`
    - `severities`
  - non-zero bridge exit now triggers only when an `error`-severity rule fires
    (warn-only alerts stay zero-exit while still surfaced in payload/markdown).
- Added summarize->bridge adapter for single-command ops bundles:
  - `scripts/summarize_backfill_warning_audits.py` now supports:
    - `--include-bridge`
    - `--bridge-projection-profile`
    - `--bridge-include-markdown`
    - `--bridge-markdown-max-projects`
    - bridge alert threshold/severity flags (`--bridge-alert-*`)
  - summary payloads can now include:
    - `bridge` (JSON bridge payload)
    - `bridge_markdown` (optional markdown briefing text)
  - adapter path reuses bridge-builder logic for consistent deltas/alerts.
- Added shared bridge alert contract helper for cross-script consistency:
  - new module:
    - `scripts/backfill_warning_bridge_alerts.py`
  - shared helper now owns:
    - threshold normalization (`int`/`rate`)
    - bridge alert config resolution from CLI namespaces
    - bridge alert rule evaluation (including severity-tier + exit semantics)
  - both scripts now import the same helper:
    - `scripts/build_backfill_warning_bridge.py`
    - `scripts/summarize_backfill_warning_audits.py`
  - summarize bridge bundle path no longer depends on bridge script private
    normalization/evaluation functions.
- Added markdown publish helper with dry-run webhook validation:
  - new script:
    - `scripts/publish_bridge_markdown.py`
  - helper supports:
    - payload dry-run validation (`--dry-run`)
    - optional webhook posting (`--webhook-url`)
    - deterministic JSON output for scheduler artifacts (`--json-compact`)
    - sanitized webhook target reporting + payload preview metadata
  - workflow templates now use helper instead of inline Python blocks:
    - `configs/backfill_workflow_snippets/github-actions-backfill-strict.yml`
    - `configs/backfill_workflow_snippets/github-actions-backfill-quiet.yml`
  - new publish toggles in workflow steps:
    - `BACKFILL_BRIDGE_MARKDOWN_DRY_RUN=true|false`
  - added helper regression:
    - `tests/test_backfill_audit_scripts.py::test_publish_bridge_markdown_helper_dry_run_and_skip`
- Added per-project severity override support for bridge alerts:
  - bridge/summarize CLIs now support:
    - `--bridge-alert-project-severity-override <project_id>=warn|error`
    - repeatable and supports `project_id:warn|error` variant
  - shared alert contract now applies overrides per project before alert-severity
    classification:
    - triggered rule severity is now determined from contributing project severities
    - warn-overridden projects can avoid non-zero exits while global family default
      remains `error`
  - alert payload now includes:
    - `project_severity_overrides`
    - `project_severity_overrides_applied`
    - `project_severity_overrides_unused`
    - per-family warn/error contribution metrics
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_ALERT_PROJECT_SEVERITY_OVERRIDES=alpha=warn,beta=warn`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py`
      - bridge per-project warn-override non-exit path
      - summarize->bridge bundle override pass-through
- Added per-family scope controls for per-project severity overrides:
  - project override syntax now supports optional scope suffix:
    - `project_id=warn@policy_only`
    - `project_id=warn@guardrail_only`
    - `project_id=warn@both` (default)
  - shared alert contract now applies scoped override severity only to the
    selected family while leaving the other family on default severity.
  - alert payload now includes scoped metadata:
    - `project_severity_override_scopes`
    - `project_severity_overrides_resolved`
  - CLI help now documents scope suffix usage in:
    - `scripts/build_backfill_warning_bridge.py`
    - `scripts/summarize_backfill_warning_audits.py`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py`
      - `policy_only` override keeps guardrail family at default severity
      - `guardrail_only` override downgrades guardrail-triggered exit behavior.
- Added bridge-rule suppression controls for monitor lanes:
  - bridge/summarize CLIs now support:
    - `--bridge-alert-suppress-rule <rule_name>`
    - repeatable and supports comma-separated values
  - shared alert contract now evaluates suppression after rule trigger
    calculation and exposes both effective and raw triggered rule views.
  - suppressed triggered rules no longer contribute to:
    - `alerts.triggered`
    - `alerts.exit_triggered`
    - effective `triggered_*` rule families
  - telemetry now includes:
    - `triggered_rules_raw`
    - `triggered_warn_rules_raw`
    - `triggered_error_rules_raw`
    - `suppressed_triggered_rules`
    - `suppressed_rules_requested|applied|unused`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_ALERT_SUPPRESS_RULES=policy_drift_count_threshold,...`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py`
      - bridge suppression zero-exit path
      - summarize->bridge suppression pass-through
- Added per-project suppression scope controls for suppressed rule matching:
  - bridge/summarize CLIs now support:
    - `--bridge-alert-project-suppress-scope <project_id>@policy_only|guardrail_only|both`
  - suppression matching now supports project+family scoped control:
    - suppressed rules only apply when a configured project contributes to the
      matching family trigger surface.
  - scoped suppression metadata now includes:
    - `project_suppression_scopes`
    - `project_suppression_scopes_applied`
    - `project_suppression_scopes_unused`
    - per-rule `suppression_scope_matched`
    - per-rule `suppression_project_matches`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_ALERT_PROJECT_SUPPRESS_SCOPES=alpha@policy_only,...`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py`
      - scoped suppression match path (`alpha@policy_only`)
      - scoped suppression miss path (`alpha@guardrail_only`)
      - summarize->bridge scoped suppression pass-through.
- Added markdown publish retry/backoff controls for webhook lanes:
  - `scripts/publish_bridge_markdown.py` now supports:
    - `--retry-attempts`
    - `--retry-backoff-seconds`
    - `--retry-backoff-multiplier`
    - `--retry-max-backoff-seconds`
    - `--retry-on-http-status` (repeatable/comma-separated)
  - publish payload now includes retry diagnostics:
    - `retry_policy`
    - `attempt_count`
    - `retries_attempted`
    - `retry_scheduled_count`
    - `attempts[]` per-attempt status/error/retry metadata
  - retry behavior:
    - retries supported for configured HTTP status codes
    - retries also apply to request/network failures
    - exponential backoff with configurable cap/multiplier
  - workflow templates now expose publish retry env controls:
    - `BACKFILL_BRIDGE_MARKDOWN_RETRY_ATTEMPTS`
    - `BACKFILL_BRIDGE_MARKDOWN_RETRY_BACKOFF_SECONDS`
    - `BACKFILL_BRIDGE_MARKDOWN_RETRY_BACKOFF_MULTIPLIER`
    - `BACKFILL_BRIDGE_MARKDOWN_RETRY_MAX_BACKOFF_SECONDS`
    - `BACKFILL_BRIDGE_MARKDOWN_RETRY_HTTP_STATUSES`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_publish_bridge_markdown_helper_retries_and_backoff`
    - uses a local flaky HTTP server (`500,500,200`) to validate retry + success flow.
- Added markdown publish retry jitter controls for high-concurrency lanes:
  - `scripts/publish_bridge_markdown.py` now supports:
    - `--retry-jitter-seconds`
    - `--retry-jitter-seed`
  - publish payload retry diagnostics now include:
    - `retry_policy.jitter_seconds`
    - `retry_policy.jitter_seed`
    - per-attempt `base_backoff_seconds`
    - per-attempt `jitter_seconds`
    - per-attempt `backoff_seconds` (base + jitter)
  - workflow templates now expose publish jitter env controls:
    - `BACKFILL_BRIDGE_MARKDOWN_RETRY_JITTER_SECONDS`
    - `BACKFILL_BRIDGE_MARKDOWN_RETRY_JITTER_SEED`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_publish_bridge_markdown_helper_retry_jitter_seeded`
    - uses deterministic jitter assertions (`--retry-jitter-seed 7`) with a flaky HTTP server (`500,500,200`).
- Extracted shared workflow snippet bridge-alert arg helper:
  - added:
    - `configs/backfill_workflow_snippets/helpers/bridge_args.sh`
  - helper functions:
    - `backfill_bridge_append_csv_args`
    - `build_backfill_bridge_alert_args`
  - both strict/quiet workflow templates now source the helper in:
    - `Build bridge payload`
    - `Publish bridge markdown brief (optional)`
  - removed duplicated bridge-alert arg assembly blocks from those step run scripts.
- Added scoped suppression visibility in bridge markdown alert summaries:
  - `scripts/build_backfill_warning_bridge.py` markdown output now includes:
    - `Suppressed Triggered Rules`
    - `Suppressed Rules Requested|Applied|Unused`
    - `Project Suppression Scopes` + `Applied|Unused`
    - per-triggered-rule detail rows with:
      - `suppressed`
      - `scope_matched`
      - `scope_projects`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas`
    - markdown mode assertions now verify scoped suppression fields and rule detail rendering.
- Added publish retry attempt timeline metadata for incident forensics:
  - `scripts/publish_bridge_markdown.py` now emits:
    - top-level:
      - `first_attempt_started_at`
      - `last_attempt_finished_at`
    - per-attempt:
      - `started_at`
      - `finished_at`
      - `elapsed_ms`
      - `next_attempt_at`
  - behavior notes:
    - `next_attempt_at` is populated only when retry is scheduled with positive backoff.
    - zero-backoff retries keep `next_attempt_at=null`.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_publish_bridge_markdown_helper_retries_and_backoff`
    - `tests/test_backfill_audit_scripts.py::test_publish_bridge_markdown_helper_retry_jitter_seeded`
    - verifies timestamp parseability/order + `next_attempt_at` semantics.
- Added bridge markdown family-grouped rule summaries and suppression digest counters:
  - `scripts/build_backfill_warning_bridge.py` markdown output now includes:
    - `Triggered Rules By Family` (`policy_only`/`guardrail_only`/`both`)
    - `Suppressed Triggered Rules By Family`
    - `Suppression Digest Counts` (requested/applied/unused + scope counts)
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas`
    - markdown mode assertions now verify family grouping + suppression digest lines.
- Added optional failed-webhook response body preview capture in publish helper:
  - `scripts/publish_bridge_markdown.py` now supports:
    - `--error-body-preview-chars`
  - when enabled and HTTP error responses provide a body:
    - per-attempt `error_body_preview` is emitted
    - top-level `last_error_body_preview` is emitted on terminal error
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_ERROR_BODY_PREVIEW_CHARS`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_publish_bridge_markdown_helper_error_body_preview`
    - validates non-zero exit payload, per-attempt preview, and top-level last preview.
- Added optional compact bridge markdown alert mode + triggered-rule detail cap:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-alert-compact`
    - `--markdown-triggered-rule-detail-max`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-alert-compact`
    - `--bridge-markdown-triggered-rule-detail-max`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_ALERT_COMPACT`
    - `BACKFILL_BRIDGE_MARKDOWN_TRIGGERED_RULE_DETAIL_MAX`
  - behavior:
    - compact mode preserves family/digest lines and suppresses verbose per-rule/per-scope lines.
    - detail cap truncates triggered rule detail rows and emits a truncation marker.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas`
      - compact markdown assertions
      - detail-cap truncation assertions
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through assertion coverage.
- Added optional publish retry diagnostics verbosity mode:
  - `scripts/publish_bridge_markdown.py` now supports:
    - `--retry-diagnostics-mode full|minimal`
  - behavior:
    - `full` preserves rich per-attempt timing/backoff fields
    - `minimal` emits compact per-attempt entries (`attempt_number/http_status/error/success/will_retry`)
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_RETRY_DIAGNOSTICS_MODE`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_publish_bridge_markdown_helper_retry_diagnostics_minimal`
    - validates compact attempt payload shape and retry flow behavior.
- Added optional bridge markdown suppression section toggle:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-hide-suppression-section`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-hide-suppression-section`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_HIDE_SUPPRESSION_SECTION`
  - behavior:
    - hides suppression-focused lines (`Suppressed Triggered Rules*`, suppression digest counts, per-scope suppression blocks)
    - retains core alert + family summary lines
    - triggered rule detail rows omit suppression-specific fields when suppression section is hidden
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas` (direct bridge markdown mode)
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle` (summarize bridge-markdown pass-through).
- Added optional publish dry-run compact output mode:
  - `scripts/publish_bridge_markdown.py` now supports:
    - `--dry-run-output-mode full|preview_only`
  - behavior:
    - `full` preserves existing dry-run payload shape
    - `preview_only` emits compact preview payload and suppresses retry envelope fields
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_publish_bridge_markdown_helper_dry_run_and_skip`
    - validates `preview_only` output mode and absence of retry envelope fields.
- Added optional bridge markdown family project listing lines:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-include-family-projects`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-include-family-projects`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_INCLUDE_FAMILY_PROJECTS`
  - behavior:
    - emits `Triggered Family Projects` lines grouped by `policy_only|guardrail_only|both`
    - each family includes `warn/error/all` project lists.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas`
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`.
- Added workflow template env plumbing for publish dry-run output mode:
  - strict/quiet templates now expose:
    - `BACKFILL_BRIDGE_MARKDOWN_DRY_RUN_OUTPUT_MODE=full|preview_only`
  - publish step wiring now forwards:
    - `--dry-run-output-mode "${BACKFILL_BRIDGE_MARKDOWN_DRY_RUN_OUTPUT_MODE}"`.
- Added bridge markdown family-project listing source mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-source triggered|all_current`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-source triggered|all_current`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_SOURCE`
  - behavior:
    - `triggered`: derives family project lists from triggered rule surfaces
    - `all_current`: derives family project lists from current per-project state (`policy_drift_changed` / `guardrail_triggered`) with severity resolution.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas`
      - direct markdown mode asserts `Family Projects (all_current)` lane.
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through asserts `all_current` output.
- Added bridge markdown family-project list cap for compact alerts:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-max-items <N>`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-max-items <N>`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_MAX_ITEMS`
  - behavior:
    - caps each family/severity project list independently in markdown output
    - emits `(+N more)` truncation markers per list when capped
    - includes `Family Projects Max Items` line in markdown output for operator context
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas`
      - direct bridge markdown path validates cap marker output (`all=[none (+1 more)]`)
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through validates cap marker output.
- Added bridge markdown family-project ordering mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-order alphabetical|severity_then_project`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-order alphabetical|severity_then_project`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_ORDER`
  - behavior:
    - `alphabetical`: deterministic alpha sort for family `all` lists
    - `severity_then_project`: deterministic `error` then `warn` ordering for family `all` lists
    - markdown now includes `Family Projects Order` line for operator traceability
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_ordering_modes`
      - validates alphabetical vs severity-first ordering deltas
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through validates order flag wiring.
- Added optional family-project count summaries in markdown alerts:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-include-counts`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-include-counts`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_INCLUDE_COUNTS`
  - behavior:
    - when enabled, markdown emits `Family Projects Counts` with per-family
      `warn/error/all` counters aligned to current source/severity/order selection
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas`
      - direct bridge markdown path validates count summary line
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through validates count summary line.
- Added optional empty-family suppression for markdown family-project lists:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-hide-empty-families`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-hide-empty-families`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_HIDE_EMPTY_FAMILY_PROJECTS`
  - behavior:
    - when enabled, family rows with zero `all` projects are omitted
    - markdown now includes `Family Projects Empty Families: hidden|shown` for clarity
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas`
      - validates hidden-empty-family markdown behavior
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through validates hidden-empty-family output.
- Added optional family-project count-only markdown mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-mode full|counts_only`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-mode full|counts_only`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_MODE`
  - behavior:
    - `full`: renders family project ID lists plus optional count summaries
    - `counts_only`: suppresses family project ID list rows and emits compact
      per-family count summaries for ultra-compact markdown
    - markdown now includes `Family Projects Mode` line for operator traceability
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_daily_deltas`
      - direct markdown path validates counts-only rendering semantics
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through validates counts-only mode.
- Added optional family-project count sorting mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-count-order by_family|by_total_desc`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-count-order by_family|by_total_desc`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_ORDER`
  - behavior:
    - `by_family`: renders compact count summaries in canonical family order
      (`policy_only`, `guardrail_only`, `both`)
    - `by_total_desc`: renders compact count summaries by descending `all` count
    - markdown now includes `Family Projects Count Order` line for operator traceability
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_count_order_modes`
      - validates `by_family` and `by_total_desc` count-line ordering.
- Added optional family-project count visibility threshold:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-count-min-all <N>`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-count-min-all <N>`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_MIN_ALL`
  - behavior:
    - filters compact family count rows to families with `all >= N`
    - markdown now includes `Family Projects Count Min All` line for traceability
    - when threshold removes all rows, count summary emits `none`
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_count_order_modes`
      - validates threshold filtering keeps only qualifying families
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through validates threshold and empty-result behavior.
- Added optional family-project count threshold mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-count-threshold-mode off|all_min`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-count-threshold-mode off|all_min`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_THRESHOLD_MODE`
  - behavior:
    - `off`: count-min-all is ignored (families are shown regardless of threshold)
    - `all_min`: applies `--markdown-family-projects-count-min-all` filter
    - markdown now includes `Family Projects Count Threshold Mode` line for operator traceability
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_count_order_modes`
      - validates `off` vs `all_min` threshold behavior
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through validates `all_min` path.
- Added optional family-project count row cap for counts-only mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-count-top-n <N>`
    - markdown now includes `Family Projects Count Top N` and `Family Projects Count Rows` (`shown`, `total`, `omitted`) when cap is active.
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-count-top-n <N>`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TOP_N`
  - behavior:
    - applies the count-row cap after threshold + ordering processing
    - preserves deterministic ordering for `by_total_desc` via `all`, then `error`, then `warn`, then family name
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_count_order_modes`
      - validates capped rows + row-metadata summary for direct markdown path.
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - summarize bridge-markdown pass-through validates cap + row-metadata summary.
- Expanded family-project source selection with union mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-source triggered|all_current|triggered_or_current`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-source triggered|all_current|triggered_or_current`
  - behavior:
    - `triggered_or_current` merges family project buckets from triggered rules and current-state derivation.
    - markdown now includes `Family Projects Source` for explicit source traceability in both `full` and `counts_only` modes.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_ordering_modes`
      - validates union-source rendering and source-trace line.
- Added optional family-project count row rendering mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-count-render-mode full_fields|nonzero_buckets`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-count-render-mode full_fields|nonzero_buckets`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_RENDER_MODE`
  - behavior:
    - `full_fields`: preserves `warn`, `error`, and `all` columns in every family count row.
    - `nonzero_buckets`: suppresses zero-valued `warn`/`error` buckets while always keeping `all`.
    - markdown now includes `Family Projects Count Render Mode` for operator traceability.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_count_order_modes`
      - validates direct markdown nonzero-bucket rendering.
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - validates summarize bridge-markdown pass-through rendering.
- Added optional family-project count row visibility mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-count-visibility-mode all_rows|nonzero_all`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-count-visibility-mode all_rows|nonzero_all`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_VISIBILITY_MODE`
  - behavior:
    - visibility filtering is now independent of list-row hide-empty behavior.
    - `all_rows`: count rows are retained even when `all=0`.
    - `nonzero_all`: count rows with `all=0` are hidden.
    - markdown now includes `Family Projects Count Visibility Mode` for traceability.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - validates `all_rows` default (including zero-total family rows) and `nonzero_all` filtering behavior.
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_count_order_modes`
      - validates visibility mode trace line in direct markdown output.
- Added optional family-project count export mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-count-export-mode inline|table`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-count-export-mode inline|table`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_EXPORT_MODE`
  - behavior:
    - `inline`: preserves current compact single-line family count summary.
    - `table`: emits a markdown table (`Family/Warn/Error/All`) using filtered/sorted/capped count rows.
    - markdown now includes `Family Projects Count Export Mode` for operator traceability.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - validates summarize bridge-markdown table export output.
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_count_order_modes`
      - validates direct bridge table export output ordering.
- Added optional family-project count table style mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-count-table-style full|minimal`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-count-table-style full|minimal`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_STYLE`
  - behavior:
    - `full`: table export always includes `Warn` and `Error` columns.
    - `minimal`: table export suppresses zero-only `Warn`/`Error` columns across displayed rows.
    - markdown now includes `Family Projects Count Table Style` for operator traceability.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - validates summarize bridge-markdown minimal table layout.
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_count_order_modes`
      - validates direct bridge minimal table layout and ordering.
- Added optional family-project count table-empty mode:
  - `scripts/build_backfill_warning_bridge.py` now supports:
    - `--markdown-family-projects-count-table-empty-mode inline_none|table_empty`
  - `scripts/summarize_backfill_warning_audits.py` now passes through:
    - `--bridge-markdown-family-projects-count-table-empty-mode inline_none|table_empty`
  - workflow templates now expose env plumbing:
    - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_EMPTY_MODE`
  - behavior:
    - `inline_none`: empty table exports fall back to inline `Family Projects Counts: \`none\``.
    - `table_empty`: emits deterministic empty table output with `(none)` row.
    - markdown now includes `Family Projects Count Table Empty Mode` for traceability.
  - regression coverage expanded in:
    - `tests/test_backfill_audit_scripts.py::test_summarize_script_include_bridge_bundle`
      - validates summarize bridge-markdown deterministic empty-table output.
    - `tests/test_backfill_audit_scripts.py::test_bridge_script_family_project_count_order_modes`
      - validates direct bridge deterministic empty-table output with minimal table style.
- Added optional workflow snippet step for markdown brief publishing:
  - both templates now include:
    - `Publish bridge markdown brief (optional)` step
  - publish step supports:
    - GitHub step summary append (`$GITHUB_STEP_SUMMARY`)
    - optional webhook POST (`BACKFILL_BRIDGE_MARKDOWN_WEBHOOK_URL`)
    - row cap via `BACKFILL_BRIDGE_MARKDOWN_MAX_PROJECTS`
    - gate toggle via `BACKFILL_BRIDGE_MARKDOWN_PUBLISH=true|false`
  - bridge artifacts now upload markdown files:
    - `output/backfill_warning_bridge/*.md`
- Added lightweight script-level smoke tests for automation helpers:
  - `tests/test_backfill_audit_scripts.py`
  - coverage includes:
    - compare helper (`policy_core` projection summary mode)
    - summarize helper rollup
    - prune helper dry-run and execute paths
    - bridge helper daily delta payload
    - bridge markdown briefing output
    - bridge threshold alert exit behavior
    - bridge warn-tier non-exit behavior
    - summarize->bridge bundle output mode
    - policy-core noise suppression in compare/export projection helpers
    - dashboard threshold alert triggers
- Updated dialogue model-policy expectations to align with critique/refinement rendering while preserving model-route assertions:
  - `tests/test_dialogue_model_policy.py`
- Added compensation-aware learning ranking penalties:
  - `jarvis/learning/features.py` now emits:
    - `failed_attempts`
    - `compensated_attempts`
    - `recent_failures` (failed + compensated)
  - `jarvis/learning/ranker.py` now penalizes actions by:
    - `compensation_rate`
    - `failure_rate`
    - `blocked_rate`
  - ranked action outputs now include:
    - `compensation_rate`
    - `failure_rate`
    - `blocked_rate`
    - `penalty`
- Added ranker penalty test:
  - `tests/test_learning_ranker_penalties.py`

## Why This Matters

This is the first structural boundary cut for the durable-operator redesign:

- runtime composition remains stable for callers (`JarvisRuntime` API unchanged),
- workflow orchestration now has its own package boundary,
- future phases can add durable step states and replay semantics without forcing a single giant `runtime.py` edit.

## Validation

Executed with local virtualenv:

- `./.venv-voice/bin/python -m pytest -q tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_workflow_extraction.py tests/test_workflow_step_states.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_suggestion_engine.py tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_learning_materialization.py tests/test_suggestion_engine.py tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_project_planning_scaffold.py tests/test_learning_materialization.py tests/test_suggestion_engine.py tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_workflow_compensation.py tests/test_project_planning_scaffold.py tests/test_learning_materialization.py tests/test_suggestion_engine.py tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_workflow_compensation.py tests/test_project_planning_scaffold.py tests/test_learning_materialization.py tests/test_suggestion_engine.py tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_workflow_compensation.py tests/test_project_signal_adapters.py tests/test_project_planning_scaffold.py tests/test_learning_materialization.py tests/test_suggestion_engine.py tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_learning_ranker_penalties.py tests/test_workflow_compensation.py tests/test_project_signal_adapters.py tests/test_project_planning_scaffold.py tests/test_learning_materialization.py tests/test_suggestion_engine.py tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_learning_ranker_penalties.py tests/test_workflow_compensation.py tests/test_project_signal_adapters.py tests/test_project_planning_scaffold.py tests/test_learning_materialization.py tests/test_suggestion_engine.py tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_learning_ranker_penalties.py tests/test_workflow_compensation.py tests/test_project_signal_adapters.py tests/test_project_planning_scaffold.py tests/test_learning_materialization.py tests/test_suggestion_engine.py tests/test_reasoning_replayer.py tests/test_reasoning_trace_capture.py tests/test_workflow_transition_validation.py tests/test_workflow_step_states.py tests/test_workflow_extraction.py tests/test_runtime.py tests/test_event_runtime.py tests/test_signal_ingest.py tests/test_markets_runtime.py tests/test_adaptive_policy_loop.py tests/test_runtime_review_sync.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_dialogue_model_policy.py tests/test_project_signal_adapters.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_learning_materialization.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_project_signal_adapters.py tests/test_learning_materialization.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_project_signal_adapters.py tests/test_learning_materialization.py tests/test_dialogue_model_policy.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_project_signal_adapters.py tests/test_cli_backfill_project_signals.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_project_signal_adapters.py tests/test_learning_materialization.py tests/test_dialogue_model_policy.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `./.venv-voice/bin/python -m pytest -q`
- `./.venv-voice/bin/python -m pytest -q tests/test_cli_backfill_project_signals.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/build_backfill_warning_bridge.py --help`
- `python3 scripts/summarize_backfill_warning_audits.py --help`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/build_backfill_warning_bridge.py --help`
- `python3 scripts/summarize_backfill_warning_audits.py --help`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/build_backfill_warning_bridge.py --help`
- `python3 scripts/summarize_backfill_warning_audits.py --help`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/build_backfill_warning_bridge.py --help`
- `python3 scripts/summarize_backfill_warning_audits.py --help`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/build_backfill_warning_bridge.py --help`
- `python3 scripts/summarize_backfill_warning_audits.py --help`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `bash -n ./scripts/run_backfill_project_signals.sh`
- `./scripts/run_backfill_project_signals.sh` (usage-path smoke; no args)
- `python3 ./scripts/export_backfill_warning_audit.py --help`
- `python3 ./scripts/export_backfill_warning_audit.py alpha --profile-key nightly --preset quick --policy-config ./configs/backfill_workflow_snippets/warning-policy-quiet.json --repo-path /Users/dankerbadge/Documents/J.A.R.V.I.S --db-path /Users/dankerbadge/Documents/J.A.R.V.I.S/.jarvis/jarvis.db --output-dir /Users/dankerbadge/Documents/J.A.R.V.I.S/output/backfill_warning_audit`
- minimal export-profile smoke run:
  - `python3 scripts/export_backfill_warning_audit.py ... --export-profile minimal`
  - generated minimal artifact size in temp run: `1117` bytes
  - compare/summarize compatibility verified on minimal artifact set.
- `python3 ./scripts/compare_backfill_policy_audits.py --help`
- `python3 ./scripts/compare_backfill_policy_audits.py /Users/dankerbadge/Documents/J.A.R.V.I.S/output/backfill_warning_audit/alpha_20260418T073411Z.json /Users/dankerbadge/Documents/J.A.R.V.I.S/output/backfill_warning_audit/alpha_20260418T073411Z.json`
- `python3 ./scripts/compare_backfill_policy_audits.py /private/tmp/backfill_audit_guardrail/alpha_20260418T074157Z.json /private/tmp/backfill_audit_guardrail/alpha_20260418T074158Z.json --summary-only --json-compact --allow-changes`
- `python3 ./scripts/compare_backfill_policy_audits.py /private/tmp/backfill_audit_guardrail/alpha_20260418T074157Z.json /private/tmp/backfill_audit_guardrail/alpha_20260418T074158Z.json --projection-profile policy_core --summary-only --json-compact --allow-changes`
- guardrail smoke run (fresh output dir, quiet baseline -> strict guarded compare):
  - first run exits `0`
  - second run exits configured drift code (`7`) with `policy_drift_guardrail_triggered=true`
- `python3 -m py_compile scripts/export_backfill_warning_audit.py scripts/compare_backfill_policy_audits.py`
- `python3 ./scripts/summarize_backfill_warning_audits.py --help`
- `python3 ./scripts/summarize_backfill_warning_audits.py --input-dir /private/tmp/backfill_audit_guardrail --since-hours 48`
- `python3 -m py_compile scripts/summarize_backfill_warning_audits.py`
- `python3 ./scripts/prune_backfill_warning_audits.py --help`
- `python3 ./scripts/prune_backfill_warning_audits.py --input-dir /private/tmp/backfill_audit_guardrail --keep-per-project 1 --json-compact`
- `python3 -m py_compile scripts/prune_backfill_warning_audits.py`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m py_compile scripts/backfill_warning_bridge_alerts.py scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m py_compile scripts/publish_bridge_markdown.py scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/publish_bridge_markdown.py --help`
- `python3 scripts/build_backfill_warning_bridge.py --help` (verify project override flag)
- `python3 scripts/summarize_backfill_warning_audits.py --help` (verify project override flag)
- `python3 scripts/build_backfill_warning_bridge.py --help` (verify suppress-rule flag)
- `python3 scripts/summarize_backfill_warning_audits.py --help` (verify suppress-rule flag)
- `python3 scripts/publish_bridge_markdown.py --help` (verify retry/backoff flags)
- `python3 scripts/build_backfill_warning_bridge.py --help` (verify scoped override help text)
- `python3 scripts/summarize_backfill_warning_audits.py --help` (verify scoped override help text)
- `python3 scripts/build_backfill_warning_bridge.py --help` (verify project suppress scope help text)
- `python3 scripts/summarize_backfill_warning_audits.py --help` (verify project suppress scope help text)
- `python3 -m py_compile scripts/publish_bridge_markdown.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/publish_bridge_markdown.py --help` (verify jitter flags)
- `bash -n configs/backfill_workflow_snippets/helpers/bridge_args.sh`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py tests/test_backfill_audit_scripts.py`
- `python3 -m py_compile scripts/publish_bridge_markdown.py tests/test_backfill_audit_scripts.py scripts/build_backfill_warning_bridge.py`
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py tests/test_backfill_audit_scripts.py`
- `python3 -m py_compile scripts/publish_bridge_markdown.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/publish_bridge_markdown.py --help` (verify error-body preview flag)
- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py scripts/publish_bridge_markdown.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/build_backfill_warning_bridge.py --help` (verify markdown compact/detail flags)
- `python3 scripts/summarize_backfill_warning_audits.py --help` (verify bridge markdown compact/detail flags)
- `python3 -m py_compile scripts/publish_bridge_markdown.py scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 scripts/publish_bridge_markdown.py --help` (verify retry diagnostics mode flag)
- `python3 scripts/build_backfill_warning_bridge.py --help` (verify suppression-section toggle flag)
- `python3 scripts/summarize_backfill_warning_audits.py --help` (verify suppression-section pass-through flag)
- `python3 scripts/publish_bridge_markdown.py --help` (verify dry-run output mode flag)
- `python3 scripts/build_backfill_warning_bridge.py --help` (verify family-project listing flag)
- `python3 scripts/summarize_backfill_warning_audits.py --help` (verify family-project listing pass-through flag)
- `python3 scripts/build_backfill_warning_bridge.py --help` (verify family-project source mode flag)
- `python3 scripts/summarize_backfill_warning_audits.py --help` (verify family-project source mode pass-through flag)
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`
- `bash -n configs/backfill_workflow_snippets/helpers/bridge_args.sh configs/backfill_workflow_snippets/github-actions-backfill-strict.yml configs/backfill_workflow_snippets/github-actions-backfill-quiet.yml`
- require-baseline smoke run (empty output dir):
  - `python3 scripts/export_backfill_warning_audit.py ... --compare-with-latest --require-baseline --missing-baseline-exit-code 8`
  - exits with `8` and exports audit payload with `baseline_missing=true`.
- `python3 -m py_compile scripts/export_backfill_warning_audit.py`
- JSON parse validation:
  - `configs/backfill_workflow_snippets/warning-policy-strict.json`
  - `configs/backfill_workflow_snippets/warning-policy-quiet.json`
- `./.venv-voice/bin/python -m pytest -q tests/test_cli_backfill_project_signals.py`
- `./.venv-voice/bin/python -m pytest -q tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `181 passed`
- latest targeted run: `30 passed` (`tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post env-warning updates): `33 passed` (`tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post env-exit-policy updates): `35 passed` (`tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post warning-policy-config updates): `37 passed` (`tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post repo-default warning-policy updates): `40 passed` (`tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post policy-output mode): `41 passed` (`tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post fallback telemetry): `42 passed` (`tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post script-smoke coverage): `44 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post high-frequency minimal knobs): `46 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post dashboard rollup mode): `46 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post policy-core guardrail + bridge payload): `49 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post dashboard threshold alerts): `49 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post bridge markdown renderer): `49 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post bridge gating thresholds): `49 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post bridge severity tiers + summarize bridge bundle): `50 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post shared bridge alert contract helper): `50 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post markdown publish dry-run helper): `51 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post per-project bridge severity overrides): `51 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post bridge-rule suppression controls): `52 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post markdown publish retry/backoff controls): `53 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post scoped project severity overrides): `53 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post scoped project suppression controls): `53 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post publish retry jitter controls): `54 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post bridge-arg helper extraction): `54 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post markdown scoped suppression summary): `54 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post publish retry attempt timeline metadata): `54 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post markdown family-grouped suppression digest): `54 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post failed-response body preview capture): `55 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post markdown compact/detail controls): `55 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post retry diagnostics verbosity mode): `56 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post suppression-section toggle pass-through): `56 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post dry-run compact output mode): `56 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project listing pass-through): `56 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post dry-run output mode template env plumbing): `56 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project source mode + template env plumbing): `56 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project max-items cap + template env plumbing): `56 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project ordering mode + template env plumbing): `57 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count summaries + template env plumbing): `57 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project empty-family suppression + template env plumbing): `57 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project counts-only mode + template env plumbing): `57 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count-order mode + template env plumbing): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count-min-all threshold + template env plumbing): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count-threshold mode + template env plumbing): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count-top-n cap + count-row metadata): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project source union mode + source trace line): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count render mode + template env plumbing): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count visibility mode + template env plumbing): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count export mode + template env plumbing): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count table-style mode + template env plumbing): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)
- latest targeted run (post family-project count table-empty mode + template env plumbing): `58 passed` (`tests/test_backfill_audit_scripts.py` + `tests/test_cli_backfill_project_signals.py` + `tests/test_project_signal_adapters.py`)

## Next Increment

1. Add optional family-project count table-family-label mode (`raw|title`) for display formatting.
2. Add summarize bridge-markdown pass-through for table-family-label mode.
3. Add workflow template env plumbing and runbook updates for table-family-label mode.

## Increment Update (2026-04-18): Family-Label Mode for Count Tables

Completed:

1. Added bridge markdown table-family-label mode with explicit CLI control:
   - `--markdown-family-projects-count-table-family-label-mode raw|title`
   - default remains `raw` for backward-compatible output.
2. Added summarize bridge-markdown pass-through:
   - `--bridge-markdown-family-projects-count-table-family-label-mode raw|title`
   - forwarded into `build_backfill_warning_bridge._render_markdown_bridge(...)`.
3. Added markdown traceability line:
   - `Family Projects Count Table Family Label Mode: \`raw|title\``.
4. Added table-row family-label formatting behavior:
   - `raw`: unchanged family keys (`policy_only`, `guardrail_only`, `both`).
   - `title`: human-readable labels (`Policy Only`, `Guardrail Only`, `Both`).
   - empty sentinel row remains `(none)`.
5. Added workflow env plumbing in strict/quiet snippets:
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_FAMILY_LABEL_MODE=raw|title`
   - propagated into markdown argument assembly.
6. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
7. Extended tests for both paths:
   - summarize include-bridge markdown path (`bridge-markdown-*` pass-through)
   - direct bridge markdown rendering path
   - assertions for both `raw` default and `title` transformed labels.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add optional table column-header label mode (`raw|title`) for `Family` header parity with row-label mode.
2. Add optional family label map override for table mode (`policy_only=...,guardrail_only=...,both=...`) to support operator-preferred terminology.
3. Extend docs/tests/templates for those two display-format controls.

## Increment Update (2026-04-18): Table Header + Family-Label Overrides

Completed:

1. Added bridge markdown table header-label mode:
   - `--markdown-family-projects-count-table-header-label-mode raw|title`
   - default `title` to preserve existing header output.
2. Added bridge markdown table family-label override map:
   - `--markdown-family-projects-count-table-family-label-override family=label`
   - supports repeated flags and comma-separated values.
3. Added summarize bridge-markdown pass-through for both new controls:
   - `--bridge-markdown-family-projects-count-table-header-label-mode raw|title`
   - `--bridge-markdown-family-projects-count-table-family-label-override family=label`
4. Added markdown traceability lines:
   - `Family Projects Count Table Header Label Mode: \`...\``
   - `Family Projects Count Table Family Label Overrides: \`...|none\``
5. Rendering behavior updates (table export mode):
   - header label mode:
     - `title` -> `Family`
     - `raw` -> `family`
   - family label override map applies per family key for table rows:
     - `policy_only`, `guardrail_only`, `both`
   - override labels win over row label mode transformation.
6. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_HEADER_LABEL_MODE=raw|title`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_FAMILY_LABEL_OVERRIDES=policy_only=...,guardrail_only=...,both=...`
7. Updated docs/runbook:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Extended tests for both summarize + direct bridge paths:
   - default header mode + default override trace lines
   - header raw mode (`| family | ... |`)
   - explicit override rows (`Policy Lane`, `Guardrail Lane`, `Cross Lane`).

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add optional table metric-column label mode (`raw|title`) for `warn/error/all` headers.
2. Add optional metric-label override map (`warn=...,error=...,all=...`) for table headers.
3. Extend docs/templates/tests for metric-column label controls.

## Increment Update (2026-04-18): Table Metric Header Label Controls

Completed:

1. Added bridge markdown table metric-label mode:
   - `--markdown-family-projects-count-table-metric-label-mode raw|title`
   - default `title` to preserve existing `Warn|Error|All` headers.
2. Added bridge markdown table metric-label override map:
   - `--markdown-family-projects-count-table-metric-label-override metric=label`
   - supported metrics: `warn`, `error`, `all`
   - supports repeated flags and comma-separated values.
3. Added summarize bridge-markdown pass-through for both controls:
   - `--bridge-markdown-family-projects-count-table-metric-label-mode raw|title`
   - `--bridge-markdown-family-projects-count-table-metric-label-override metric=label`
4. Added markdown traceability lines:
   - `Family Projects Count Table Metric Label Mode: \`...\``
   - `Family Projects Count Table Metric Label Overrides: \`...|none\``
5. Updated table header rendering behavior:
   - mode-based header labels for metrics (`warn/error/all`)
   - explicit metric overrides take precedence over mode labels.
6. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_METRIC_LABEL_MODE=raw|title`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_METRIC_LABEL_OVERRIDES=warn=...,error=...,all=...`
7. Updated docs/runbook:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Extended tests for summarize + direct bridge paths:
   - default metric trace lines
   - raw metric mode headers (`warn|error|all`)
   - custom metric override headers (for example `Warning|Critical|Total`).

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add optional stable table-row family ordering mode independent of count-order sorting (`canonical|sorted|count_order`) for display consistency.
2. Add optional markdown trace line for rendered table schema signature (header + included columns) to simplify downstream parser robustness.
3. Extend docs/templates/tests for those display-contract controls.

## Increment Update (2026-04-18): Table Row Order + Schema Signature Trace

Completed:

1. Added bridge markdown table row-order mode:
   - `--markdown-family-projects-count-table-row-order-mode count_order|canonical|sorted`
   - default `count_order` preserves prior table ordering behavior.
2. Added optional schema-signature trace toggle:
   - `--markdown-family-projects-count-table-include-schema-signature`
   - when enabled in table export mode, markdown emits:
     - `Family Projects Counts Table Schema Signature: <json>`
     - includes rendered `columns` and `headers` arrays.
3. Added summarize bridge-markdown pass-through for both controls:
   - `--bridge-markdown-family-projects-count-table-row-order-mode count_order|canonical|sorted`
   - `--bridge-markdown-family-projects-count-table-include-schema-signature`
4. Added markdown traceability lines:
   - `Family Projects Count Table Row Order Mode: \`...\``
   - `Family Projects Count Table Include Schema Signature: \`True|False\``
5. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_ROW_ORDER_MODE=count_order|canonical|sorted`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_TABLE_INCLUDE_SCHEMA_SIGNATURE=true|false`
6. Updated docs/runbook:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
7. Extended tests for summarize + direct bridge paths:
   - default trace-line assertions
   - canonical and sorted row-order rendering checks
   - schema-signature trace output checks.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add optional inline count-summary label controls (`family key` / `bucket key` display profile) to align inline output with table label customization.
2. Add strict validation/reporting for malformed override tokens (currently silently ignored) with optional diagnostics line in markdown.
3. Extend docs/templates/tests for inline label profile and override diagnostics controls.

## Increment Update (2026-04-18): Inline Label Profiles + Override Diagnostics

Completed:

1. Added inline count-summary label profile controls:
   - `--markdown-family-projects-count-inline-family-label-mode raw|title`
   - `--markdown-family-projects-count-inline-bucket-label-mode raw|title`
   - defaults remain `raw` for backward-compatible inline output.
2. Added malformed override diagnostics toggle:
   - `--markdown-family-projects-count-label-override-diagnostics`
   - when enabled, markdown includes malformed token detail for table family/metric override inputs.
3. Added summarize bridge-markdown pass-through for all three controls:
   - `--bridge-markdown-family-projects-count-inline-family-label-mode raw|title`
   - `--bridge-markdown-family-projects-count-inline-bucket-label-mode raw|title`
   - `--bridge-markdown-family-projects-count-label-override-diagnostics`
4. Added markdown trace lines:
   - `Family Projects Count Inline Family Label Mode: ...`
   - `Family Projects Count Inline Bucket Label Mode: ...`
   - `Family Projects Count Label Override Diagnostics: True|False`
   - optional `Family Projects Count Label Override Diagnostics Detail: ...`
5. Implemented stricter override token parsing (family + metric):
   - malformed tokens are collected (`missing '='`, invalid key, empty label)
   - valid entries still apply with last-write-wins behavior.
6. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_INLINE_FAMILY_LABEL_MODE=raw|title`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_INLINE_BUCKET_LABEL_MODE=raw|title`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS=true|false`
7. Updated docs/runbook:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Extended tests for summarize + direct bridge paths:
   - default inline/diagnostics trace lines
   - inline title-mode rendering assertions
   - malformed override diagnostics assertions.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add optional diagnostics severity mode for malformed override tokens (`off|note|warn`) with stricter CI-friendly signaling in markdown.
2. Add optional machine-readable diagnostics JSON line for label-control configuration resolution to simplify downstream parsers.
3. Extend docs/templates/tests for diagnostics severity + structured diagnostics output.

## Increment Update (2026-04-18): Diagnostics Severity + Structured Diagnostics JSON

Completed:

1. Added malformed override diagnostics severity mode:
   - `--markdown-family-projects-count-label-override-diagnostics-severity off|note|warn`
   - default `off` for backward compatibility.
2. Added machine-readable diagnostics JSON toggle:
   - `--markdown-family-projects-count-label-override-diagnostics-json`
3. Added summarize bridge-markdown pass-through for both controls:
   - `--bridge-markdown-family-projects-count-label-override-diagnostics-severity off|note|warn`
   - `--bridge-markdown-family-projects-count-label-override-diagnostics-json`
4. Added markdown signaling lines for CI-friendly parsing:
   - `Family Projects Count Label Override Diagnostics Severity Mode`
   - `Family Projects Count Label Override Diagnostics Triggered`
   - `Family Projects Count Label Override Diagnostics Severity`
   - `Family Projects Count Label Override Diagnostics JSON`
   - optional `Family Projects Count Label Override Diagnostics JSON Detail`
5. Added strict override token parsing with malformed-token collection for both families and metrics.
6. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_SEVERITY`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON`
7. Updated docs/runbook and expanded tests for summarize + direct bridge paths.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add per-scope diagnostics breakdown counters in markdown (`family_malformed_count`, `metric_malformed_count`, `resolved_family_count`, `resolved_metric_count`).
2. Add optional diagnostics policy line summarizing whether malformed tokens should fail CI in strict pipelines (dry-run signaling only).
3. Extend docs/templates/tests for diagnostics counters + policy signaling.

## Increment Update (2026-04-18): Diagnostics Severity Policy + Structured JSON Detail

Completed:

1. Added diagnostics severity policy mode for malformed override tokens:
   - `--markdown-family-projects-count-label-override-diagnostics-severity off|note|warn`
   - default `off` (backward-compatible signaling).
2. Added structured diagnostics JSON detail toggle:
   - `--markdown-family-projects-count-label-override-diagnostics-json`
3. Added summarize bridge-markdown pass-through for both controls:
   - `--bridge-markdown-family-projects-count-label-override-diagnostics-severity off|note|warn`
   - `--bridge-markdown-family-projects-count-label-override-diagnostics-json`
4. Added markdown policy/signal lines:
   - `Family Projects Count Label Override Diagnostics Severity Mode`
   - `Family Projects Count Label Override Diagnostics Triggered`
   - `Family Projects Count Label Override Diagnostics Severity`
   - `Family Projects Count Label Override Diagnostics JSON`
   - optional `Family Projects Count Label Override Diagnostics JSON Detail`.
5. Structured diagnostics JSON now includes:
   - diagnostics enabled/triggered/severity fields
   - resolved + malformed family override details
   - resolved + malformed metric override details
   - inline/table label-mode resolution snapshot.
6. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_SEVERITY`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON`
7. Updated docs/runbook and extended tests for summarize + direct bridge paths.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add diagnostics counter line (`family_malformed_count`, `metric_malformed_count`, `resolved_family_count`, `resolved_metric_count`) for compact pipeline checks.
2. Add optional policy recommendation line for strict CI (`fail_ci_recommended=true|false`) without changing script exit behavior.
3. Extend docs/templates/tests for diagnostics counters + policy recommendation signaling.

## Increment Update (2026-04-18): Diagnostics Counters + Strict CI Recommendation Signal

Completed:

1. Added strict CI recommendation mode for malformed override diagnostics:
   - `--markdown-family-projects-count-label-override-ci-policy-mode off|strict`
   - default `off` (no behavior change to exit codes).
2. Added summarize bridge pass-through for the same control:
   - `--bridge-markdown-family-projects-count-label-override-ci-policy-mode off|strict`
3. Added compact diagnostics counters line in markdown bridge output:
   - `Family Projects Count Label Override Diagnostics Counters`
   - includes `resolved_family_count`, `resolved_metric_count`, `family_malformed_count`, `metric_malformed_count`.
4. Added policy signaling lines in markdown bridge output:
   - `Family Projects Count Label Override CI Policy Mode`
   - `Family Projects Count Label Override Fail CI Recommended`
5. Extended diagnostics JSON detail payload to include:
   - `ci_policy_mode`
   - `fail_ci_recommended`
   - `counters` object with resolved/malformed family+metric counts.
6. Added workflow env plumbing in strict/quiet snippets:
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_CI_POLICY_MODE`
7. Updated docs/runbook and tests for summarize + direct bridge paths.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add diagnostics counters JSON-only export mode (single compact line) for log ingestors that avoid full diagnostics payload.
2. Add optional diagnostics key-prefix mode (`bridge_` vs `count_override_`) for easier downstream field namespacing.
3. Extend docs/templates/tests for compact-json export mode + key-prefix signaling.

## Increment Update (2026-04-18): Diagnostics JSON Compact Mode + Key Prefix Signaling

Completed:

1. Added diagnostics JSON mode controls for label-override diagnostics:
   - `--markdown-family-projects-count-label-override-diagnostics-json-mode full|compact`
   - `--bridge-markdown-family-projects-count-label-override-diagnostics-json-mode full|compact`
2. Added compact diagnostics JSON key-prefix controls:
   - `--markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode bridge_|count_override_`
   - `--bridge-markdown-family-projects-count-label-override-diagnostics-json-key-prefix-mode bridge_|count_override_`
3. Added markdown policy/signaling lines:
   - `Family Projects Count Label Override Diagnostics JSON Mode`
   - `Family Projects Count Label Override Diagnostics JSON Key Prefix Mode`
4. Implemented compact diagnostics JSON detail payload mode:
   - emits a single flat counters/status JSON object (prefixed keys)
   - avoids nested `counters` / `family` / `metric` blocks for log ingestors.
5. Kept full diagnostics JSON detail mode as the default (`full`) for backward-compatible nested diagnostics payloads.
6. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_KEY_PREFIX_MODE`
7. Updated docs/runbook and extended tests for summarize + direct bridge paths:
   - default mode/prefix trace assertions (`full`, `bridge_`)
   - compact mode + `count_override_` prefixed payload assertions.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add diagnostics JSON compact profile presets (`compact_min`, `compact_full`) to standardize ingestion payload shapes.
2. Add optional compact payload include-modes for malformed token lists (`none|counts_only|counts_plus_tokens`) without reintroducing heavy nested payloads.
3. Extend docs/templates/tests for compact profile presets + include-mode signaling.

## Increment Update (2026-04-18): Compact Diagnostics Profiles + Include Modes

Completed:

1. Added compact diagnostics JSON profile presets for label-override diagnostics:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-profile compact_min|compact_full`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-profile ...`
2. Added compact diagnostics JSON include-mode controls:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode none|counts_only|counts_plus_tokens`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode ...`
3. Added markdown trace lines for compact diagnostics policy signaling:
   - `Family Projects Count Label Override Diagnostics JSON Compact Profile`
   - `Family Projects Count Label Override Diagnostics JSON Compact Include Mode`
4. Extended compact JSON payload behavior:
   - `compact_min`: flat status + policy keys, with include-mode-controlled counters/tokens.
   - `compact_full`: flat status + policy + context keys (inline/table mode metadata), still non-nested.
   - include-mode semantics:
     - `none`: omits counters + malformed-token lists.
     - `counts_only`: includes resolved/malformed counts.
     - `counts_plus_tokens`: includes counts + malformed token arrays.
5. Retained `full` JSON mode default behavior, adding explicit compact preset/include-mode metadata to full payload.
6. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_PROFILE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_INCLUDE_MODE`
7. Updated docs/runbook and expanded tests to cover:
   - default compact preset + include mode (`compact_min`, `counts_only`)
   - `compact_full + counts_plus_tokens`
   - `compact_min + none` include mode.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics token-list truncation controls (`max_tokens_per_scope`, `overflow_suffix`) to keep payloads stable for noisy malformed input.
2. Add compact diagnostics malformed-token sorting mode (`input_order|lexicographic`) for deterministic ingestion pipelines.
3. Extend docs/templates/tests for truncation + sorting controls and trace signaling.

## Increment Update (2026-04-18): Compact Token Truncation + Sorting Controls

Completed:

1. Added compact diagnostics malformed-token sorting control:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sort-mode input_order|lexicographic`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-sort-mode ...`
2. Added compact diagnostics malformed-token max-per-scope control:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-max-per-scope <N>` (`0` keeps all)
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-max-per-scope <N>`
3. Added compact diagnostics malformed-token overflow-suffix control:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix "+{omitted} more"`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix ...`
4. Added markdown trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Sort Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Max Per Scope`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix`
5. Extended compact diagnostics payload behavior (`compact` mode):
   - always emits compact token policy keys (`compact_token_sort_mode`, `compact_token_max_per_scope`, `compact_token_overflow_suffix`)
   - `counts_plus_tokens` now supports deterministic token ordering and per-scope truncation
   - emits `family_malformed_tokens_omitted_count` + `metric_malformed_tokens_omitted_count` for truncation diagnostics.
6. Extended full diagnostics payload metadata with compact-token policy fields:
   - `json_compact_token_sort_mode`
   - `json_compact_token_max_per_scope`
   - `json_compact_token_overflow_suffix`
7. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SORT_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MAX_PER_SCOPE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_OVERFLOW_SUFFIX`
8. Updated docs/runbook and expanded tests for summarize + direct bridge paths, including:
   - default policy-line assertions
   - `compact_full + counts_plus_tokens` omitted-count assertions
   - deterministic truncation test (`lexicographic`, max=2, overflow=`[+{omitted}]`).

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics token-scope mode (`family_only|metric_only|both`) for targeted ingestion payload slimming.
2. Add compact diagnostics token-dedup mode (`off|on`) to suppress repeated malformed tokens before truncation.
3. Extend docs/templates/tests for token-scope + dedup controls and trace signaling.

## Increment Update (2026-04-18): Compact Token Scope + Dedup Controls

Completed:

1. Added compact diagnostics malformed-token scope-mode control:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode family_only|metric_only|both`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-mode ...`
2. Added compact diagnostics malformed-token dedup-mode control:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-dedup-mode off|on`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-dedup-mode ...`
3. Added markdown trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Scope Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Dedup Mode`
4. Extended compact diagnostics payload behavior (`compact` mode):
   - emits `compact_token_scope_mode` + `compact_token_dedup_mode` policy keys.
   - `counts_plus_tokens` now honors scope filtering:
     - `family_only` emits only family malformed-token keys
     - `metric_only` emits only metric malformed-token keys
     - `both` emits both scopes.
   - optional dedup (`on`) is applied before sort/truncation.
5. Extended full diagnostics payload metadata with:
   - `json_compact_token_scope_mode`
   - `json_compact_token_dedup_mode`
6. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SCOPE_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_DEDUP_MODE`
7. Updated docs/runbook and expanded tests for summarize + direct bridge paths, including deterministic scope+dedup validation:
   - `family_only + dedup=on + lexicographic + max_per_scope=2 + overflow suffix`.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics token-case-normalization mode (`preserve|lower`) for cross-pipeline key/value normalization.
2. Add optional compact diagnostics token-character sanitization mode (`off|ascii_safe`) for brittle downstream sinks.
3. Extend docs/templates/tests for normalization + sanitization controls and policy trace lines.

## Increment Update (2026-04-18): Compact Token Normalization + Sanitization Controls

Completed:

1. Added compact diagnostics malformed-token normalization-mode control:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-normalization-mode preserve|lower`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-normalization-mode ...`
2. Added compact diagnostics malformed-token sanitization-mode control:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-mode off|ascii_safe`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-sanitization-mode ...`
3. Added markdown trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Normalization Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Mode`
4. Extended compact diagnostics payload behavior (`compact` mode):
   - emits `compact_token_normalization_mode` + `compact_token_sanitization_mode` policy keys.
   - malformed tokens are now normalization/sanitization aware before dedup/sort/truncation.
   - overflow suffix rendering also follows normalization/sanitization policy.
5. Extended full diagnostics payload metadata with:
   - `json_compact_token_normalization_mode`
   - `json_compact_token_sanitization_mode`
6. Added workflow env plumbing (strict/quiet snippets):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_NORMALIZATION_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SANITIZATION_MODE`
7. Updated docs/runbook and expanded tests for summarize + direct bridge paths, including deterministic scope+dedup+normalization+s sanitization validation:
   - `family_only + dedup=on + normalization=lower + sanitization=ascii_safe + lexicographic + max_per_scope=2 + custom overflow suffix`.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics token-sanitization replacement-char control for `ascii_safe` mode (default `_`) to avoid empty-token collapse in edge cases.
2. Add compact diagnostics token-min-length filter to suppress noisy micro-tokens before truncation.
3. Extend docs/templates/tests for replacement-char + min-length controls and policy trace lines.

## Increment Finalization (2026-04-18): Replacement-Char + Min-Length Test Parity

Completed final parity sweep for the compact token sanitization replacement-char + min-length controls added in this increment.

What was finalized:

1. Expanded summarize + direct-bridge test assertions to cover new markdown trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Sanitization Replacement Char`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Min Length`
2. Expanded diagnostics JSON assertions (full + compact) to cover new policy metadata keys:
   - `json_compact_token_sanitization_replacement_char`
   - `json_compact_token_min_length`
   - `count_override_compact_token_sanitization_replacement_char`
   - `count_override_compact_token_min_length`
3. Extended compact-limited CLI test cases to pass explicit override values:
   - replacement char: `-`
   - min length: `3`
4. Updated compact-limited malformed-token expectations to match effective `ascii_safe` replacement behavior:
   - tokens now include `ba-dtoken`
   - omitted-count expectations updated accordingly.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics malformed-token include-mode `counts_plus_tokens_if_truncated` to reduce token noise when no truncation occurs.
2. Add compact diagnostics malformed-token overflow-suffix suppression toggle for strict JSON sinks.
3. Extend docs/templates/tests for conditional-token emission + overflow-suffix suppression controls.

## Increment Update (2026-04-18): Conditional Token Emission + Overflow-Suffix Suppression

Completed:

1. Added compact diagnostics include-mode variant:
   - `counts_plus_tokens_if_truncated`
   - CLI (bridge): `--markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode ...`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-include-mode ...`
2. Added compact diagnostics overflow-suffix emission mode:
   - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix-mode include|suppress`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-overflow-suffix-mode include|suppress`
3. Extended markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Overflow Suffix Mode`
4. Extended compact/full diagnostics JSON metadata with:
   - `compact_token_overflow_suffix_mode` (prefixed in compact mode)
   - `json_compact_token_overflow_suffix_mode` (full mode)
5. Updated compact token rendering behavior:
   - overflow suffix is appended only when `overflow_suffix_mode=include`
   - suffix is omitted (while `*_omitted_count` remains accurate) when `overflow_suffix_mode=suppress`
6. Updated compact include-mode behavior:
   - `counts_plus_tokens_if_truncated` emits malformed token lists only when truncation occurs (`*_omitted_count > 0` per scope)
   - counts/counters still emit under this mode.
7. Added workflow snippet env + plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_OVERFLOW_SUFFIX_MODE`
8. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
   to include the new include-mode value and overflow-suffix-mode control.
9. Expanded summarize + direct bridge tests to assert new mode traces and JSON keys, and added explicit coverage for:
   - `counts_plus_tokens_if_truncated`
   - `overflow_suffix_mode=suppress`
   - suffix-free token lists under truncation.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics token-omitted-count visibility mode (`always|if_truncated_only|off`) for payload-shaping consistency across strict sinks.
2. Add compact diagnostics token-list non-empty guard mode (`off|require_nonempty_tokens`) to suppress empty/sparse malformed-token keys after sanitation/min-length filtering.
3. Extend docs/templates/tests for omitted-count visibility + non-empty token guard controls.

## Increment Update (2026-04-18): Omitted-Count Visibility + Non-Empty Token Guard

Completed:

1. Added compact diagnostics omitted-count visibility mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode always|if_truncated_only|off`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-omitted-count-visibility-mode ...`
2. Added compact diagnostics token-list guard mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode off|require_nonempty_tokens`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-guard-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Omitted Count Visibility Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token List Guard Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_omitted_count_visibility_mode`
     - `compact_token_list_guard_mode`
   - full-mode keys:
     - `json_compact_token_omitted_count_visibility_mode`
     - `json_compact_token_list_guard_mode`
5. Updated compact token payload emission behavior:
   - omitted-count keys now follow mode:
     - `always`: always emit omitted-count key for emitted token scopes
     - `if_truncated_only`: emit omitted-count key only when omitted > 0
     - `off`: never emit omitted-count key
   - token-list guard mode:
     - `require_nonempty_tokens`: suppresses token scope keys when compact token list is empty after normalization/sanitization/min-length filtering
6. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_OMITTED_COUNT_VISIBILITY_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_LIST_GUARD_MODE`
7. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Expanded tests for summarize + direct bridge parity:
   - policy trace + JSON metadata assertions for both new modes
   - compact-limited scenario now validates omitted-count suppression (`off`)
   - added sparse-token guard scenario verifying `require_nonempty_tokens` suppresses empty malformed-token keys.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics token-list key mode (`always|if_nonempty|if_truncated`) to further standardize strict-sink payload shapes.
2. Add compact diagnostics token-scope fallback mode (`selected_only|auto_expand_when_empty`) to avoid empty compact payloads under aggressive filtering.
3. Extend docs/templates/tests for token-list key mode + scope fallback behavior.

## Increment Update (2026-04-18): Token List Key Mode + Scope Fallback

Completed:

1. Added compact diagnostics token-list key mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode always|if_nonempty|if_truncated`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-list-key-mode ...`
2. Added compact diagnostics token-scope fallback mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode selected_only|auto_expand_when_empty`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-fallback-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token List Key Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Scope Fallback Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_list_key_mode`
     - `compact_token_scope_fallback_mode`
   - full-mode keys:
     - `json_compact_token_list_key_mode`
     - `json_compact_token_scope_fallback_mode`
5. Updated compact token emission behavior:
   - list key mode controls whether `*_malformed_tokens` keys emit per scope:
     - `always`
     - `if_nonempty`
     - `if_truncated`
   - scope fallback mode controls token-scope expansion when selected scopes emit no token-list keys:
     - `selected_only`
     - `auto_expand_when_empty`
6. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_LIST_KEY_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SCOPE_FALLBACK_MODE`
7. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Expanded tests for summarize + direct bridge parity:
   - default policy traces + JSON metadata for new modes
   - limited compact scenario assertions for explicit mode overrides
   - sparse-token selected-only scenario remains empty-scope-safe
   - new fallback scenario verifies `auto_expand_when_empty` emits family scope tokens when metric-only selection is empty under aggressive filtering.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics token-list truncation indicator mode (`off|summary_only|per_scope`) for deterministic strict-sink summaries without token payload expansion.
2. Add compact diagnostics scope-priority mode (`family_first|metric_first`) used by auto-expand fallback when both fallback scopes are eligible.
3. Extend docs/templates/tests for truncation-indicator + scope-priority controls.

## Increment Update (2026-04-18): Truncation Indicator Mode + Scope Priority Mode

Completed:

1. Added compact diagnostics token-list truncation indicator mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode off|summary_only|per_scope`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-truncation-indicator-mode ...`
2. Added compact diagnostics scope-priority mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode family_first|metric_first`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-scope-priority-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Truncation Indicator Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Scope Priority Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_truncation_indicator_mode`
     - `compact_token_scope_priority_mode`
   - full-mode keys:
     - `json_compact_token_truncation_indicator_mode`
     - `json_compact_token_scope_priority_mode`
5. Extended compact token emission behavior:
   - `summary_only` adds compact summary key: `*_malformed_tokens_truncated`
   - `per_scope` adds scope keys: `*_<scope>_malformed_tokens_truncated`
   - scope-priority now controls fallback scope ordering (`family_first|metric_first`) when auto-expand fallback runs.
6. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_TRUNCATION_INDICATOR_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SCOPE_PRIORITY_MODE`
7. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Expanded tests for summarize + direct bridge parity:
   - explicit CLI coverage for both new controls
   - markdown policy trace assertions for both controls
   - compact JSON metadata assertions for both controls
   - truncation indicator assertions for `summary_only` and `per_scope` outputs.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics fallback emission policy mode to control whether auto-expand fallback emits first-success scope only or all eligible scopes.
2. Add compact diagnostics fallback source marker mode for traceability (`off|summary|per_scope`) in strict sinks.
3. Extend docs/templates/tests for fallback emission/source-marker controls.

## Increment Update (2026-04-18): Fallback Emission Mode + Fallback Source Marker Mode

Completed:

1. Added compact diagnostics fallback emission policy mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-emission-mode first_success_only|all_eligible`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-emission-mode ...`
2. Added compact diagnostics fallback source marker mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode off|summary|per_scope`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Emission Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_fallback_emission_mode`
     - `compact_token_fallback_source_marker_mode`
   - full-mode keys:
     - `json_compact_token_fallback_emission_mode`
     - `json_compact_token_fallback_source_marker_mode`
5. Extended compact token fallback behavior:
   - `first_success_only`: auto-expand fallback stops at first scope emitting token-list keys.
   - `all_eligible`: auto-expand fallback emits all eligible fallback scopes.
6. Added fallback source markers for strict sinks:
   - `summary`: emits `*_fallback_used` + `*_fallback_source_scopes`
   - `per_scope`: emits `*_<scope>_fallback_source` booleans (+ `*_fallback_used`)
7. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_FALLBACK_EMISSION_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_FALLBACK_SOURCE_MARKER_MODE`
8. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
9. Expanded tests for summarize + direct bridge parity:
   - explicit CLI coverage for fallback-emission/source-marker controls
   - markdown policy trace assertions for both controls
   - compact JSON metadata assertions for both controls
   - fallback behavior assertions verifying all-eligible emission + per-scope markers.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics fallback activation mode to control when source markers emit (`always|fallback_only`).
2. Add compact diagnostics fallback selected-scope marker mode for traceability when fallback is bypassed by selected scopes.
3. Extend docs/templates/tests for fallback activation + selected-scope marker controls.

## Increment Update (2026-04-18): Fallback Marker Activation Mode + Selected Scope Marker Mode

Completed:

1. Added compact diagnostics fallback source-marker activation mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode always|fallback_only`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-fallback-source-marker-activation-mode ...`
2. Added compact diagnostics selected-scope marker mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode off|summary|per_scope`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-selected-scope-marker-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Fallback Source Marker Activation Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Selected Scope Marker Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_fallback_source_marker_activation_mode`
     - `compact_token_selected_scope_marker_mode`
   - full-mode keys:
     - `json_compact_token_fallback_source_marker_activation_mode`
     - `json_compact_token_selected_scope_marker_mode`
5. Extended compact token marker behavior:
   - fallback markers now honor activation mode:
     - `always`: emit fallback marker keys regardless of fallback usage.
     - `fallback_only`: emit fallback marker keys only when fallback contributes emitted scopes.
   - selected-scope markers added when fallback is bypassed and selected scopes emit keys:
     - `summary`: emits `*_selected_source_used` + `*_selected_source_scopes`
     - `per_scope`: emits `*_<scope>_selected_source` booleans.
6. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_FALLBACK_SOURCE_MARKER_ACTIVATION_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_SELECTED_SCOPE_MARKER_MODE`
7. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Expanded tests for summarize + direct bridge parity:
   - explicit CLI coverage for fallback marker activation + selected-scope marker controls
   - markdown policy trace assertions for both controls
   - compact JSON metadata assertions for both controls
   - fallback scenarios assert selected markers are absent and fallback markers remain scoped to actual fallback usage under `fallback_only`.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics marker key naming mode (`default|short`) to reduce payload key length in strict sinks while preserving deterministic mapping.
2. Add compact diagnostics marker suppression mode to omit marker metadata when malformed token payloads are absent.
3. Extend docs/templates/tests for marker naming/suppression controls.

## Increment Update (2026-04-18): Marker Key Naming Mode + Marker Suppression Mode

Completed:

1. Added compact diagnostics marker-key naming mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode default|short`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-naming-mode ...`
2. Added compact diagnostics marker suppression mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode off|omit_when_no_token_payload`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-suppression-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Naming Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Suppression Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_marker_key_naming_mode`
     - `compact_token_marker_suppression_mode`
   - full-mode keys:
     - `json_compact_token_marker_key_naming_mode`
     - `json_compact_token_marker_suppression_mode`
5. Added short marker-key aliases (`marker-key-naming-mode=short`) for strict sinks:
   - summary markers:
     - `fallback_used -> fb_used`
     - `fallback_source_scopes -> fb_scopes`
     - `selected_source_used -> sel_used`
     - `selected_source_scopes -> sel_scopes`
     - `malformed_tokens_truncated -> tokens_trunc`
   - per-scope markers:
     - `<scope>_fallback_source -> <scope>_fb_source`
     - `<scope>_selected_source -> <scope>_sel_source`
     - `<scope>_malformed_tokens_truncated -> <scope>_tokens_trunc`
6. Added marker suppression behavior (`marker-suppression-mode=omit_when_no_token_payload`):
   - when malformed-token list payload keys are absent, marker metadata is omitted for:
     - fallback markers
     - selected-scope markers
     - truncation indicator markers (summary/per-scope)
7. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_KEY_NAMING_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUPPRESSION_MODE`
8. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
9. Expanded tests for summarize + direct bridge parity:
   - default metadata assertions for both new controls in full JSON mode
   - short-key marker assertions for selected-source and fallback-source scenarios
   - suppression assertions proving marker keys are omitted when token payload keys are absent.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics marker summary visibility mode (`always|if_true_only`) so boolean markers can be suppressed when false while preserving short/default key mapping.
2. Add compact diagnostics marker scope-order mode (`canonical|priority`) so per-scope marker emission order can follow canonical family/metric or active scope-priority order.
3. Extend docs/templates/tests for marker visibility and scope-order controls.

## Increment Update (2026-04-18): Marker Summary Visibility Mode + Marker Scope Order Mode

Completed:

1. Added compact diagnostics marker summary visibility mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-visibility-mode always|if_true_only`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-visibility-mode ...`
2. Added compact diagnostics marker scope-order mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-scope-order-mode canonical|priority`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-scope-order-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Visibility Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Scope Order Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_marker_summary_visibility_mode`
     - `compact_token_marker_scope_order_mode`
   - full-mode keys:
     - `json_compact_token_marker_summary_visibility_mode`
     - `json_compact_token_marker_scope_order_mode`
5. Extended marker emission behavior:
   - `marker-summary-visibility-mode=if_true_only` suppresses boolean marker keys when value is `false` while preserving short/default marker-key mapping.
   - summary list markers (for example fallback scope lists) remain available when configured, while associated false booleans are omitted under `if_true_only`.
6. Extended per-scope marker ordering behavior:
   - `marker-scope-order-mode=priority` follows active scope priority (`family_first|metric_first`).
   - `marker-scope-order-mode=canonical` emits per-scope marker keys in canonical `family, metric` order.
   - applies to per-scope fallback/selected/truncation marker families.
7. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUMMARY_VISIBILITY_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SCOPE_ORDER_MODE`
8. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
9. Expanded tests for summarize + direct bridge parity:
   - explicit CLI coverage for both new controls
   - markdown policy trace assertions for both controls
   - compact JSON metadata assertions for both controls
   - boolean-marker suppression assertions (`if_true_only`) where fallback bool keys are omitted but summary list keys remain
   - canonical per-scope order assertions under `metric_first` priority to verify scope-order override.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics marker list visibility mode (`always|if_nonempty`) for summary list markers (`*_fb_scopes`, `*_sel_scopes`) in strict sinks.
2. Add compact diagnostics marker key-prefix mode (`inherit|markers`) to optionally isolate marker keys under a marker-specific prefix branch while preserving compact flat payload defaults.
3. Extend docs/templates/tests for list-visibility and marker-prefix controls.

## Increment Update (2026-04-18): Marker List Visibility Mode + Marker Key Prefix Mode

Completed:

1. Added compact diagnostics marker list-visibility mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-list-visibility-mode always|if_nonempty`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-list-visibility-mode ...`
2. Added compact diagnostics marker key-prefix mode:
   - bridge: `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode inherit|markers`
   - summarize pass-through: `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-key-prefix-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker List Visibility Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Key Prefix Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_marker_list_visibility_mode`
     - `compact_token_marker_key_prefix_mode`
   - full-mode keys:
     - `json_compact_token_marker_list_visibility_mode`
     - `json_compact_token_marker_key_prefix_mode`
5. Extended marker emission behavior:
   - summary list marker visibility:
     - `always`: emit summary list markers (`*_fb_scopes`, `*_sel_scopes`) even when empty.
     - `if_nonempty`: emit only when list values exist.
   - marker key-prefix routing:
     - `inherit`: marker keys follow existing compact prefix behavior.
     - `markers`: marker keys route under a marker branch (for example `count_override_marker_sel_used`, `count_override_marker_fb_source`, `count_override_marker_tokens_trunc`) while non-marker compact payload keys remain unchanged.
6. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_LIST_VISIBILITY_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_KEY_PREFIX_MODE`
7. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Expanded tests for summarize + direct bridge parity:
   - default metadata assertions for both new controls in full JSON mode
   - explicit CLI coverage for both new controls in compact marker-focused scenarios
   - marker-prefix assertions confirming `markers` mode key routing
   - summary-list visibility assertions confirming `if_nonempty` suppresses empty fallback scope list markers.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics marker boolean-type split visibility mode to independently control fallback/selected/truncation boolean marker emission.
2. Add compact diagnostics marker summary-list ordering mode (`insertion|lexicographic`) for deterministic list-marker ordering across strict sinks.
3. Extend docs/templates/tests for marker type-split visibility and summary-list ordering controls.

## Increment Update (2026-04-18): Marker Boolean-Type Visibility + Marker Summary-List Ordering

Completed:

1. Added compact diagnostics marker boolean-type split visibility mode:
   - bridge:
     `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode all|fallback_only|selected_only|truncation_only|fallback_selected|fallback_truncation|selected_truncation|none`
   - summarize pass-through:
     `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-boolean-type-visibility-mode ...`
2. Added compact diagnostics marker summary-list ordering mode:
   - bridge:
     `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode insertion|lexicographic`
   - summarize pass-through:
     `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-list-order-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Boolean Type Visibility Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary List Order Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_marker_boolean_type_visibility_mode`
     - `compact_token_marker_summary_list_order_mode`
   - full-mode keys:
     - `json_compact_token_marker_boolean_type_visibility_mode`
     - `json_compact_token_marker_summary_list_order_mode`
5. Extended marker emission behavior:
   - boolean marker families can be independently enabled/disabled for fallback, selected, and truncation marker keys.
   - summary-list marker values now support deterministic ordering:
     - `insertion`: preserve emission order.
     - `lexicographic`: sort list marker values before emission.
6. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_BOOLEAN_TYPE_VISIBILITY_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUMMARY_LIST_ORDER_MODE`
7. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Expanded tests for summarize + direct bridge parity:
   - default metadata assertions for both controls in full JSON mode.
   - compact marker-focused coverage for boolean-type visibility split behavior.
   - explicit behavioral ordering assertions proving `lexicographic` summary-list ordering rewrites multi-scope list markers from priority insertion order to canonical lexical order.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics marker summary-family visibility mode (`all|fallback_only|selected_only|none`) for list marker families (`*_fb_scopes` vs `*_sel_scopes`).
2. Add compact diagnostics marker per-scope family visibility mode (`all|fallback_only|selected_only|truncation_only|none`) for per-scope marker keys.
3. Extend docs/templates/tests for family-specific marker visibility controls and strict-sink parity.

## Increment Update (2026-04-21): Marker Summary-Family + Per-Scope Family Visibility

Completed:

1. Added compact diagnostics marker summary-family visibility mode:
   - bridge:
     `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-family-visibility-mode all|fallback_only|selected_only|none`
   - summarize pass-through:
     `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-family-visibility-mode ...`
2. Added compact diagnostics marker per-scope family visibility mode:
   - bridge:
     `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-per-scope-family-visibility-mode all|fallback_only|selected_only|truncation_only|none`
   - summarize pass-through:
     `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-per-scope-family-visibility-mode ...`
3. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Family Visibility Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Per Scope Family Visibility Mode`
4. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_marker_summary_family_visibility_mode`
     - `compact_token_marker_per_scope_family_visibility_mode`
   - full-mode keys:
     - `json_compact_token_marker_summary_family_visibility_mode`
     - `json_compact_token_marker_per_scope_family_visibility_mode`
5. Extended marker emission behavior:
   - summary-list marker families (`*_fb_scopes`, `*_sel_scopes`) now obey summary-family visibility gating.
   - per-scope marker families (`*_fb_source`, `*_sel_source`, `*_tokens_trunc`) now obey per-scope family visibility gating.
   - existing boolean-type visibility gating remains intact and composes with new family gates.
6. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUMMARY_FAMILY_VISIBILITY_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PER_SCOPE_FAMILY_VISIBILITY_MODE`
7. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Expanded tests for summarize + direct bridge parity:
   - default full-json metadata assertions for both controls.
   - compact profile assertions for both controls in existing limited scenarios.
   - explicit behavior checks for summary-family suppression (selected summary scopes suppressed while selected boolean marker remains).
   - explicit behavior checks for per-scope family suppression (selected per-scope markers retained while fallback/truncation per-scope markers are suppressed).

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add compact diagnostics marker summary-boolean family visibility mode (`all|fallback_only|selected_only|truncation_only|none`) scoped to summary boolean markers (`*_used`, `*_tokens_trunc`).
2. Add compact diagnostics marker profile shortcuts (for example `strict_minimal`, `strict_verbose`) to bundle coherent marker visibility/order defaults behind one flag.
3. Extend docs/templates/tests for marker profile shortcuts and family-scoped summary boolean visibility.

## Increment Update (2026-04-21): Summary-Boolean Family Visibility + Marker Profile Shortcuts

Completed:

1. Added compact diagnostics summary-boolean marker family visibility mode:
   - bridge:
     `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-boolean-family-visibility-mode all|fallback_only|selected_only|truncation_only|none`
   - summarize pass-through:
     `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-summary-boolean-family-visibility-mode ...`
2. Added compact diagnostics marker profile shortcut mode:
   - bridge:
     `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode off|strict_minimal|strict_verbose`
   - summarize pass-through:
     `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-mode ...`
3. Added profile-aware marker defaulting (without clobbering explicit non-default overrides):
   - `strict_minimal` applies a reduced-noise marker posture.
   - `strict_verbose` applies a high-visibility deterministic marker posture.
4. Added markdown policy trace lines:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Summary Boolean Family Visibility Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode`
5. Extended compact/full diagnostics JSON policy metadata with:
   - compact-prefixed keys:
     - `compact_token_marker_summary_boolean_family_visibility_mode`
     - `compact_token_marker_profile_mode`
   - full-mode keys:
     - `json_compact_token_marker_summary_boolean_family_visibility_mode`
     - `json_compact_token_marker_profile_mode`
6. Extended marker emission behavior:
   - summary boolean markers now have independent family gating for fallback/selected/truncation.
   - profile shortcuts compose with existing boolean-type, summary-family, and per-scope-family controls.
7. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_SUMMARY_BOOLEAN_FAMILY_VISIBILITY_MODE`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PROFILE_MODE`
8. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
9. Expanded tests for summarize + direct bridge parity:
   - default full-json metadata assertions for summary-boolean family visibility and profile mode.
   - compact limited-scenario assertions for both new controls.
   - explicit strict-minimal profile behavior checks confirming selected summary markers are retained while fallback summary markers are suppressed under profile defaults.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add explicit profile precedence diagnostics (for each marker control, indicate whether effective value came from explicit input, profile defaulting, or baseline default).
2. Add marker profile `"strict_debug"` for maximal traceability (`per_scope`-friendly defaults, canonical ordering, full family visibility).
3. Extend docs/templates/tests for profile precedence diagnostics and new profile behavior across summarize/direct parity.

## Increment Update (2026-04-21): Profile Precedence Diagnostics + strict_debug Marker Profile

Completed:

1. Added explicit marker profile precedence diagnostics per marker control:
   - effective source labels:
     - `explicit_input`
     - `profile_default`
     - `baseline_default`
2. Added compact/full JSON diagnostics keys for precedence sources:
   - compact-prefixed keys:
     - `compact_token_marker_profile_mode_source`
     - `compact_token_marker_<control>_source` (for each marker control)
   - full-mode keys:
     - `json_compact_token_marker_profile_mode_source`
     - `json_compact_token_marker_<control>_source`
3. Added markdown trace lines for profile precedence diagnostics:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Mode Source`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Precedence`
4. Added marker profile `strict_debug`:
   - CLI support in bridge/summarize profile choices.
   - profile defaults tuned for maximal traceability:
     - canonical scope order
     - full family visibility controls
     - marker-focused key prefixing and deterministic ordering behavior.
5. Extended profile defaulting logic to:
   - apply profile defaults only when a control remains at baseline default.
   - preserve explicit non-default user inputs.
6. Extended workflow snippet env + arg docs/plumbing for updated profile choices:
   - strict/quiet snippets remain wired with profile mode env pass-through.
7. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Expanded tests for summarize + direct bridge parity:
   - default full-json source assertions for precedence diagnostics.
   - compact limited scenario assertions for source labels.
   - strict-minimal profile source assertions (`profile_default` vs `explicit_input`).
   - strict-debug profile behavior assertions for per-scope traceability markers.

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `16/16` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `58 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add optional profile-precedence export compaction mode (`full|summary_only`) so strict sinks can choose between per-control source keys and a condensed profile-precedence summary.
2. Add a stable marker-profile signature field (hash over effective marker controls) for quick drift detection in compact diagnostics.
3. Extend docs/templates/tests for precedence compaction and profile-signature drift checks across summarize/direct parity.

## Increment Update (2026-04-21): Marker Precedence Compaction + Profile Signature

Completed:

1. Added compact diagnostics marker precedence export mode:
   - bridge:
     `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-precedence-export-mode full|summary_only`
   - summarize pass-through:
     `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-precedence-export-mode ...`
2. Added stable marker-profile signature hash over effective marker controls:
   - compact-prefixed key:
     - `compact_token_marker_profile_signature`
   - full-mode key:
     - `json_compact_token_marker_profile_signature`
3. Added explicit precedence-export metadata:
   - compact-prefixed key:
     - `compact_token_marker_precedence_export_mode`
   - full-mode key:
     - `json_compact_token_marker_precedence_export_mode`
4. Added `summary_only` compaction behavior for marker precedence sources:
   - suppresses per-control `*_source` marker precedence keys in diagnostics JSON
   - emits condensed summary key instead:
     - compact-prefixed key:
       - `compact_token_marker_precedence_summary`
     - full-mode key:
       - `json_compact_token_marker_precedence_summary`
5. Added markdown trace lines for new controls:
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Precedence Export Mode`
   - `Family Projects Count Label Override Diagnostics JSON Compact Token Marker Profile Signature`
   - summary-mode precedence line:
     - `... Marker Profile Precedence Summary`
6. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PRECEDENCE_EXPORT_MODE`
7. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
8. Expanded tests for summarize + direct bridge parity:
   - default full-mode assertions for precedence export mode + signature
   - summary-only assertions for precedence-summary emission + per-control source suppression
   - summarize bundle pass-through assertions for the new bridge option

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `18/18` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `60 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add optional marker-profile signature drift-check controls (expected signature + match mode) so strict sinks can signal/act on marker-profile drift directly in compact diagnostics.
2. Extend compact/full diagnostics payloads and markdown policy traces with signature drift metadata.
3. Extend docs/templates/tests for signature drift controls across summarize/direct parity.

## Increment Update (2026-04-21): Marker Profile Signature Drift Checks

Completed:

1. Added compact diagnostics marker-profile signature drift-check controls:
   - bridge:
     - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-expected <64hex>`
     - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-match-mode off|warn|strict`
   - summarize pass-through:
     - `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-expected ...`
     - `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-match-mode ...`
2. Added markdown policy trace lines for signature drift checks:
   - `... Marker Profile Signature Expected`
   - `... Marker Profile Signature Expected Valid`
   - `... Marker Profile Signature Match Mode`
   - `... Marker Profile Signature Match`
   - optional drift trace line when mismatch is active in `warn|strict` modes
3. Extended compact/full diagnostics JSON metadata with drift-check fields:
   - compact-prefixed keys:
     - `compact_token_marker_profile_signature_expected`
     - `compact_token_marker_profile_signature_expected_valid`
     - `compact_token_marker_profile_signature_match_mode`
     - `compact_token_marker_profile_signature_match`
     - `compact_token_marker_profile_signature_drift_detected`
   - full-mode keys:
     - `json_compact_token_marker_profile_signature_expected`
     - `json_compact_token_marker_profile_signature_expected_valid`
     - `json_compact_token_marker_profile_signature_match_mode`
     - `json_compact_token_marker_profile_signature_match`
     - `json_compact_token_marker_profile_signature_drift_detected`
4. Added strict drift escalation behavior:
   - when `marker_profile_signature_match_mode=strict` and signature drift is detected,
     diagnostics now force `fail_ci_recommended=True`.
5. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PROFILE_SIGNATURE_EXPECTED`
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PROFILE_SIGNATURE_MATCH_MODE`
6. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
7. Expanded tests for summarize + direct bridge parity:
   - direct bridge strict mismatch assertions for drift metadata + `fail_ci_recommended=true`
   - summarize bundle pass-through assertions for signature drift metadata

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `20/20` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `62 passed` targeted pytest suite.

## Next Increment (Updated)

1. Add explicit non-zero exit control for strict marker-profile signature drift (beyond diagnostics `fail_ci_recommended` hints).
2. Extend bridge + summarize entrypoints, templates, docs, and parity tests for drift-exit behavior.
3. Keep alert-exit behavior unchanged and layer drift-exit as an opt-in strict sink control.

## Increment Update (2026-04-21): Strict Signature-Drift Exit Control

Completed:

1. Added explicit strict signature-drift exit controls:
   - bridge:
     - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-drift-exit-code <N>`
   - summarize:
     - `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-drift-exit-code <N>`
2. Added strict drift detection in markdown output handling:
   - trigger condition:
     - marker-profile signature drift detected in rendered compact diagnostics metadata
     - signature match mode is `strict`
     - drift exit code is configured as non-zero
3. Exit behavior:
   - drift exit is opt-in (`0` disables)
   - drift exit only applies when existing alert exit code is not already triggered in bridge script
   - summarize script now returns configured drift exit code when bundled bridge markdown reports strict signature drift
4. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PROFILE_SIGNATURE_DRIFT_EXIT_CODE`
5. Updated docs:
   - `configs/backfill_workflow_snippets/README.md`
   - `M23_BACKFILL_CURSOR_RUNBOOK.md`
6. Expanded tests for summarize + direct bridge parity:
   - direct bridge strict signature drift returns configured exit code
   - summarize bundle strict signature drift returns configured exit code
   - existing drift diagnostics assertions remain intact

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `22/22` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `64 passed` targeted pytest suite.

## Next Increment (Updated)

1. Replace markdown-string scanning in strict signature-drift exit gating with dedicated structured runtime telemetry.
2. Expose markdown drift telemetry in summarize bundle payloads for downstream automation/policy checks.
3. Extend docs/tests to assert telemetry presence and telemetry-based exit behavior.

## Increment Update (2026-04-21): Structured Signature-Drift Telemetry + Exit Gating

Completed:

1. Added structured runtime telemetry channel in markdown bridge renderer:
   - new optional renderer arg:
     - `markdown_runtime_telemetry: dict[str, object] | None`
   - telemetry fields now include:
     - `marker_profile_signature`
     - `marker_profile_signature_expected`
     - `marker_profile_signature_expected_valid`
     - `marker_profile_signature_match_mode`
     - `marker_profile_signature_match`
     - `marker_profile_signature_drift_detected`
     - `marker_profile_signature_strict_mode`
     - `marker_profile_signature_drift_exit_eligible`
2. Replaced markdown substring scanning in exit gating with structured telemetry checks:
   - bridge script strict drift exit now uses `marker_profile_signature_drift_exit_eligible` from runtime telemetry.
   - summarize script strict drift exit now uses bundled telemetry instead of parsing markdown text.
3. Added summarize bundle payload field:
   - `bridge_markdown_telemetry` (emitted when `--bridge-include-markdown` is enabled)
4. Added explicit strict drift exit controls:
   - bridge:
     - `--markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-drift-exit-code <N>`
   - summarize:
     - `--bridge-markdown-family-projects-count-label-override-diagnostics-json-compact-token-marker-profile-signature-drift-exit-code <N>`
5. Extended workflow snippet env + arg plumbing (strict/quiet):
   - `BACKFILL_BRIDGE_MARKDOWN_FAMILY_PROJECTS_COUNT_LABEL_OVERRIDE_DIAGNOSTICS_JSON_COMPACT_TOKEN_MARKER_PROFILE_SIGNATURE_DRIFT_EXIT_CODE`
6. Updated docs:
   - `configs/backfill_workflow_snippets/README.md` (bundle telemetry note + new env control)
   - `M23_BACKFILL_CURSOR_RUNBOOK.md` (bundle telemetry note + new CLI control)
7. Expanded tests:
   - bridge strict drift exit returns configured code
   - summarize strict drift exit returns configured code
   - summarize bundle now asserts `bridge_markdown_telemetry` drift/eligibility fields

Validation:

- `python3 -m py_compile scripts/build_backfill_warning_bridge.py scripts/summarize_backfill_warning_audits.py tests/test_backfill_audit_scripts.py`
- `python3 -m unittest tests.test_backfill_audit_scripts -v`
- `./.venv-voice/bin/python -m pytest -q tests/test_backfill_audit_scripts.py tests/test_cli_backfill_project_signals.py tests/test_project_signal_adapters.py`

Result:

- `22/22` unittest cases passed (`tests.test_backfill_audit_scripts`).
- `64 passed` targeted pytest suite.
