# M20: Dialogue Model Stack (Model-First + Retrieval + Rerank)

## Goal

Upgrade live dialogue quality so the conversational layer matches the architecture:

- model-first generation for normal turns
- retrieved context grounded in memory and recent thread state
- reranked memory snippets before generation
- heuristics reserved for fallback only

## Contract

1. Normal non-empty turns use model path first when model-assisted backend is available.
2. Heuristic generation is fallback-only:
   - model unavailable
   - model timeout/error
   - explicit `disable_model_presence_reply`
3. Dialogue context must include ranked memory snippets and retrieval metadata.

## Retrieval Pipeline

1. Candidate recall:
   - semantic memory recall (`memory.retrieve_semantic`)
   - extra recall probes from objective/unresolved thread questions
2. Base ranking:
   - lexical overlap
   - thread-term overlap
   - confidence
   - freshness decay
3. Optional rerank stages:
   - Ollama embedding rerank (off by default)
   - BGE reranker (`BAAI/bge-reranker-v2-m3`) (off by default)

## Environment Controls

- `JARVIS_PRESENCE_MODEL_FIRST` (default `true`)
- `JARVIS_DIALOGUE_EMBED_RERANK_ENABLED` (default `true`)
- `JARVIS_DIALOGUE_EMBED_MODEL` (default `mxbai-embed-large`)
- `JARVIS_DIALOGUE_FLAG_RERANK_ENABLED` (default `false`; enable only when `FlagEmbedding` + reranker weights are verified)
- `JARVIS_DIALOGUE_FLAG_RERANK_MODEL` (default `BAAI/bge-reranker-v2-m3`)
- `JARVIS_PRESENCE_MODEL_TIMEOUT_SECONDS` (default `20`) for model-backed presence replies
- `JARVIS_OLLAMA_EMBED_ENDPOINT` (optional override)
- `JARVIS_DIALOGUE_RETRIEVE_LIMIT` (default `8`)
- `JARVIS_DIALOGUE_RETRIEVE_CANDIDATE_LIMIT` (default `32`)
- `JARVIS_DIALOGUE_EMBED_BLEND_WEIGHT` (default `0.30`)
- `JARVIS_DIALOGUE_FLAG_BLEND_WEIGHT` (default `0.40`)
- `JARVIS_DIALOGUE_MIN_SCORE` (default `0.00`)
- `JARVIS_M20B_TURN_HARD_TIMEOUT_SECONDS` (harness-only hard wall-clock cap; default `30`)

## Recommended Local Model Stack

- Primary dialogue model (promoted): `qwen3:14b`
- Candidate deep-followup model: `qwen3:30b` (do not globally promote on current hardware)
- Embedding model: `mxbai-embed-large` (or `nomic-embed-text` on lower hardware)
- Reranker: `BAAI/bge-reranker-v2-m3`

## M20B Trial Snapshot (2026-04-13)

- Full promotion trial report: `analysis/m20b_dialogue_model_trial_2026-04-13_04-05-59.json`
- Production-profile pinned copy: `analysis/m20b_dialogue_model_trial_2026-04-13_production-profile.json`
- Daily freeze manifest: `analysis/production_profile_freeze_2026-04-13.json`
- Verdict: `keep_14b`
- Why: `qwen3:30b` improved composite quality only slightly (~`+0.0045`) but missed latency guardrails by a wide margin (phase B/C medians ~`40s` vs `3.5s/10s` limits).
- Timeout-hardcap smoke report (harness validation): `analysis/m20b_dialogue_model_trial_2026-04-13_timeout-smoke.json`

## Frozen Runtime Entry Points

- Daily launcher: `scripts/start_jarvis_daily_production.sh`
- Runtime drift/status view: `scripts/jarvis_runtime_status.py`
- M21 soak harness: `scripts/run_m21_relationship_soak.py`

## M21 Smoke Snapshot (2026-04-13)

- Smoke output: `analysis/m21_relationship_soak_2026-04-13_10-06-11.json`
- Notes:
  - per-turn timeout enforcement is active in the soak harness (`--turn-timeout-seconds`)
  - `--max-turns` is available for bounded smoke checks before full multi-day soak

## M21A First-Turn Hardening (2026-04-13)

- Spec artifact: `M21A_FIRST_TURN_RELIABILITY_SPEC.md`
- Full loop soak artifact: `analysis/m21_relationship_soak_2026-04-13_11-52-06.json`
- Key result:
  - `first_turn_presence_ok=true`
  - `first_turn_elapsed_ms=16.984`
  - `continuity_failure_rate=0.0`
- Runtime changes:
  - fast social-turn path (skip heavy context)
  - low-latency status/priority path (skip retrieval rerank on those turns)
  - presence model budget hardening (single-attempt fail-fast fallback)
  - `presence_ack` contract on reply preparation

## Acceptance Criteria

1. Greeting/status turns no longer default to canned replies when model backend is healthy.
2. Dialogue context includes `memory.semantic_snippets` and retrieval metadata.
3. Quality guard still blocks parroting and internal-label leakage.
4. Existing presence/governance/codex tests remain green.
