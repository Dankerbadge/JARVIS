# M20: DIALOGUE INTELLIGENCE REALIGNMENT

## Goal

Replace the shallow live reply path with a context-grounded dialogue engine so JARVIS feels like one stable partner, not a formatter around heuristics.

## Problem Statement

The system currently has strong governance and continuity scaffolding, but the user-facing conversation path is under-contextualized:

- short/social turns are frequently resolved by canned heuristics
- model reply context is too thin for cross-domain reasoning
- relationship mode appears in text banners instead of being behaviorally rendered
- continuity is tracked mechanically (hashes/revisions) more than conversationally (unresolved meaning)

## Design Principles

1. One mind, many surfaces: core reasoning remains in JARVIS runtime.
2. Model-first dialogue: heuristics are fallback, not default.
3. Context before response: each turn is built from active state, not just the latest utterance.
4. Invisible mode rendering: mode affects style/tactics, not explicit mode labels.
5. Critique gate: reject low-quality/parrot replies before user delivery.

## Scope

### P0 (this milestone)

1. Add a structured dialogue context builder for presence replies.
2. Remove hard short-social heuristic routing as primary path.
3. Add reply-quality guardrails to block parroting/internal-label leakage.
4. Remove explicit mode banners from user-facing reply text.
5. Log dialogue quality signals for audit.

### P1

1. Add dedicated `dialogue_threads` + `dialogue_turns` stores.
2. Persist unresolved questions, hypotheses, and follow-up commitments.
3. Add context retrieval strategy (recent turns + salient memory + domain state).

### P2

1. Add second-pass reply critique (model-assisted and deterministic fallback).
2. Add eval harness for high-friction prompts (greetings, sarcasm, ambiguity, contradiction, pushback).
3. Add objective scoring: relevance, continuity, specificity, initiative, tone stability.

## Runtime Contract (Presence Reply)

Every live reply must run:

1. ingest user turn
2. classify intent + effort route
3. build dialogue context packet
4. generate candidate reply (model-first)
5. critique/quality gate
6. apply relationship/tone/pushback policy
7. emit final response + trace metadata

## Dialogue Context Packet (minimum fields)

- conversation: `surface_id`, `session_id`, `mode`, `modality`, uncertainty/high-stakes flags
- identity: top goals, personal context, focus mode
- thinking: latest hypotheses and active thread cues
- operations: pending interrupts, recent event window, recent pushback records
- domains:
  - academics risks
  - market risks/opportunities/risk posture
- session continuity: last relationship mode + session key

## Acceptance Criteria

1. Greeting/status prompts are no longer hard-routed to canned templates when model path is available.
2. User-facing replies contain no explicit `Mode:` banners.
3. Parrot/echo responses are blocked by quality gate.
4. Status requests return stateful answers grounded in live context.
5. Presence reply events include dialogue quality metadata.
6. Existing presence/codex/governance tests remain green; dialogue tests added.

## Deliverables

- Runtime/context-builder changes in `jarvis/runtime.py`
- Reply renderer cleanup in `jarvis/openclaw_reply_orchestrator.py`
- Presence prompt hardening in `jarvis/model_backends/ollama_backend.py`
- New and updated tests in `tests/test_presence_orchestration.py` and related suites

