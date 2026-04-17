# Milestone 6: Remote Branch + PR Orchestration

Milestone 6 closes the gap between an approved sandbox and an actual reviewable software delivery artifact.

## Goals

1. Turn a prepared, approved sandbox into a committed remote branch.
2. Keep `main`/protected branches untouched by JARVIS.
3. Generate a durable PR payload containing the reasoning and evidence needed for human review.
4. Persist publication receipts so later steps can reference exactly what was pushed.

## New runtime components

- `jarvis/executors/git_remote.py`
  - commit staged sandbox changes
  - push sandbox branch to named remote
  - capture commit and push receipts
- `jarvis/pr_payload.py`
  - build PR title/body/labels from plan + approval packet + commit/push facts
- `jarvis/publication_service.py`
  - orchestrate commit -> push -> PR payload generation
- `jarvis/security.py`
  - store/retrieve `publication_receipts`
- `jarvis/runtime.py`
  - expose `publish_approved_step()` and `get_pr_payload()`
- `jarvis/cli.py`
  - add `plans publish-approved`
  - add `plans pr-payload`

## Safety model

- publication only runs for an already approved step
- publication uses the existing prepared sandbox path from the evidence packet
- publication never merges to the base branch
- PR payload includes rollback and audit references

## Stored receipt contract

A publication receipt records:

- `approval_id`
- `plan_id`
- `step_id`
- `repo_id`
- `remote_name`
- `remote_url`
- `base_branch`
- `head_branch`
- `commit`
- `push`
- `pr_payload`
- `published_at`
- `sandbox_path`

## CLI flow

1. `plans preflight <plan_id>`
2. `approvals approve <approval_id>`
3. `plans publish-approved <plan_id> <step_id> --remote-name origin --base-branch main`
4. `plans pr-payload <plan_id> <step_id>`

## Test coverage

- remote commit + push against a local bare `origin`
- PR payload rendering from approval evidence
- runtime end-to-end publication + stored PR payload retrieval
