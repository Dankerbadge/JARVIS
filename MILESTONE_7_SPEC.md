# Milestone 7: Provider-native Review Creation and Status Sync

## Goal
Turn a pushed JARVIS review branch into a hosted provider review artifact and sync its evolving state back into runtime memory/state.

## Scope
- Provider abstraction for hosted code review backends.
- GitHub implementation first.
- Persisted provider review artifact tied to approval + plan + step.
- Status sync that updates runtime state with review state and CI/check rollup.
- CLI support for open/show/sync review artifacts.

## Contracts
### Review artifact
A provider review artifact stores:
- provider name
- target repo slug
- provider external id / review number
- title/body/base/head/head sha
- draft/open/merged state
- labels/reviewers
- last synced status snapshot

### Status snapshot
A synced status snapshot stores:
- review state (`open`, `closed`, `merged`)
- checks rollup (`success`, `pending`, `failure`, or `None`)
- mergeability when available
- blocking contexts
- provider update time
- sync time

## Runtime behavior
1. `publish-approved` still prepares/commits/pushes and builds the durable PR payload.
2. `open-review` (or `publish-approved --open-review`) uses that payload to create a provider-native review artifact.
3. The artifact is persisted in security storage and indexed in the world-state graph.
4. `sync-review` refreshes provider state and check rollups, then writes the latest status back into state + episodic memory.

## State indexing
- `latest_review_artifact:<repo_id>:<branch>`
- `latest_review_status:<repo_id>:<branch>`

## Guardrails
- No provider review can be opened before a publication receipt exists.
- Provider sync is read-only.
- Protected code changes remain gated by the same approval path established in M5/M6.

## Test targets
- request formation and response parsing for the GitHub provider
- review-service orchestration
- runtime persistence + state sync using a fake provider
