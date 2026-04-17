# MILESTONE 19: VOICE + LATENCY + RELATIONSHIP POLISH

## Goal

Make one stable JARVIS consciousness feel consistent and responsive across text, voice, and node surfaces.

## Core Principle

- OpenClaw remains embodiment and transport.
- JARVIS remains identity, reasoning, memory, mode selection, and approval authority.

## Scope

### 1. Voice surface binding

- Route voice/talk interactions through the same reply orchestration path used by DM/node.
- Preserve continuity envelope checks before response emission.

Acceptance criteria:

- voice responses include continuity state and relationship mode decision metadata
- voice does not bypass pushback, time framing, or approval boundary behavior

### 2. Latency ladder

Reply preparation should emit three stages:

- `phase_a_presence`: immediate acknowledgement
- `phase_b_first_useful`: short useful answer
- `phase_c_deep_followup`: deeper reasoning/tradeoff follow-up

Acceptance criteria:

- target latencies are generated per modality/profile
- profile supports at least `standard` and faster `talk` behavior

### 3. Speech-mode rendering

Keep one mind, multiple render modes:

- `equal`
- `strategist`
- `butler`

Acceptance criteria:

- mode framing appears in prepared replies
- mode choice remains governed by core relationship-mode engine

### 4. Tone balance controller

Add explicit tone balancing and calibration:

- profile dimensions: calmness, warmth, challenge, deference, compression, humor
- imbalance detection
- calibration hints
- persistent snapshots for trend analysis

Acceptance criteria:

- tone profile is returned with each prepared reply
- snapshots are queryable by API

### 5. Cross-surface continuity polish

- classify talk/voice traffic as `voice` channel type in session routing
- maintain continuity across voice + text surfaces with same contract/user/pushback anchors

## APIs

- `POST /api/presence/voice/reply/prepare`
- `GET /api/presence/tone-balance`
- `POST /api/presence/voice/soak/start`
- `POST /api/presence/voice/soak/turn`
- `GET /api/presence/voice/soak/report`

## Soak Evaluation Axes

- continuity drift (`contract_hash`, `relationship_mode`, `user_model_revision`, `pushback_calibration_revision`)
- mode accuracy (expected vs selected mode)
- latency ladder quality (phase A/B/C observed vs target deltas)
- interruption recovery (interrupted turns vs recovered turns)
- tone-balance drift (dimension deltas + dominant drift dimensions)

## Validation

- `tests/test_presence_orchestration.py`
- `tests/test_openclaw_event_router.py`
- `tests/test_operator_server.py`
- existing gateway/presence regression tests remain green
