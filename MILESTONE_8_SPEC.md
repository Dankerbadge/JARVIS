# Milestone 8: Hosted Review Feedback Ingestion + Promotion Policy

## Goal
Move provider reviews from "artifact created" into a closed-loop runtime signal stream that can influence policy and learning.

## Scope
- Reviewer and label orchestration on hosted reviews.
- Sync of requested reviewers, reviews, issue comments, review comments, and timeline events.
- Stateful indexing of hosted review feedback into the world-state graph.
- Draft-to-ready promotion policy evaluation with explicit gates.
- Outcome-learning hook from hosted review outcomes into `plan_outcomes`.

## Runtime Additions
### Review orchestration
- `configure_provider_review(plan_id, step_id, reviewers, labels)`
- Normalizes reviewers and labels via provider APIs.

### Review sync
- `sync_provider_review(plan_id, step_id)`
- `sync_review_feedback(repo_id, pr_number, branch)`
- Persists review artifact + status + feedback snapshots.
- Persists dedicated security snapshots:
  - `review_artifacts`
  - `review_feedback`
  - `review_timeline_cursor`
  - `merge_outcomes`
- Writes enriched state artifacts:
  - `latest_review_artifact:<repo_id>:<branch>`
  - `latest_review_status:<repo_id>:<branch>`
  - `latest_requested_reviewers:<repo_id>:<branch>`
  - `latest_review_summary:<repo_id>:<branch>`
  - `latest_review_comments:<repo_id>:<branch>`
  - `latest_timeline_cursor:<repo_id>:<branch>`
  - `latest_merge_outcome:<repo_id>:<branch>`

### Promotion policy
- `evaluate_review_promotion_policy(...)`
- `promote_provider_review_ready(...)`

Default policy gates:
- approved action record exists
- approval packet exists
- preflight passed
- requested reviewers present
- labels normalized to required set
- checks gate:
  - required checks configured: checks must be `success`
- no required checks configured: blocked by default unless `allow_no_required_checks=True`
- single-maintainer repos with no valid reviewer targets: blocked unless
    `single_maintainer_override=True` with auditable policy object:
    `actor`, `repo_id`, `pr_number`, `reason`, `applied_at`, `sunset_condition`

### Outcome hook
Hosted review outcomes are mapped into `plan_outcomes`:
- `approved` -> `success`
- `changes_requested` -> `regression`
- `merged` -> `success`
- `closed_unmerged` -> `failure`

## CLI Additions
- `plans configure-review`
- `plans request-reviewers`
- `plans set-labels`
- `plans evaluate-promotion`
- `plans promote-ready`
- `plans sync-review-feedback`
- `plans review-summary`
- `plans review-comments`

## Provider Notes
GitHub provider now supports:
- reviewer/label orchestration
- full review feedback sync (reviews/comments/timeline)
- required-check discovery
- GraphQL ready-for-review promotion (with REST fallback)
