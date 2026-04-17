# MILESTONE 18: OPENCLAW PRESENCE BRIDGE + CONSCIOUSNESS PROJECTION

## Goal

Project one stable JARVIS consciousness across OpenClaw surfaces while keeping JARVIS as the
authoritative mind and OpenClaw as embodiment/control transport.

## Architecture Principle

- JARVIS core remains source of truth for:
  - identity and relationship continuity
  - goal hierarchy and domain weighting
  - hypothesis generation and uncertainty policy
  - pushback/override/refusal behavior
  - interruption judgment and finite-time framing
- OpenClaw acts as:
  - channel ingress/egress
  - device presence and node embodiment
  - operator surface/control transport
  - guarded tool transport layer

## Scope

### 1. Consciousness projection expansion (workspace contract)

Finalize projection surfaces consumed by OpenClaw workspace loading:

- `SOUL.md`
- `IDENTITY.md`
- `USER.md`
- `TOOLS.md`
- `AGENTS.md`
- `HEARTBEAT.md`
- `BOOT.md`
- `MEMORY.md`

Acceptance criteria:

- surfaces generated from authoritative JARVIS runtime state only
- surfaces refreshed on contract/context/priority change
- no secret material rendered into workspace artifacts

### 2. WebSocket presence bridge (OpenClaw Gateway -> JARVIS)

Implement `jarvis/openclaw_ws_bridge.py` with:

- gateway challenge/handshake lifecycle support
- durable connection + reconnect strategy
- event subscription for session/presence/channel streams
- inbound normalization to `jarvis.signal.v1`

Acceptance criteria:

- every inbound event converted to canonical `SignalEnvelope` before cognition
- replay-safe ingest and provenance preserved
- bridge state observable in operator home/debug endpoints

### 3. SecretRef node contract for bridge identity

Add bridge credential contract for:

- gateway bearer/auth tokens
- device token persistence
- node/device identity material

Requirements:

- fail-closed secret resolution
- no partial apply on invalid plans
- explicit path and target validation
- no secret values in logs, surfaces, or memory events

### 4. One consciousness, many surfaces (mode governance)

Codify relationship-mode policy in core runtime:

- `equal`
- `strategist`
- `butler`

Requirements:

- no multi-agent personality split for relationship modes
- mode selected by core cognition context, then projected outward
- OpenClaw routing renders chosen mode; does not define it

### 5. Pushback and override calibration loop

Introduce first-class artifacts:

- `PushbackRecord`
- `OverrideRecord`
- `OutcomeReview`
- `CalibrationUpdate`

Acceptance criteria:

- every significant override captures prior pushback rationale
- post-outcome calibration updates future pushback intensity
- operator can inspect calibration trajectory from API/UI

### 6. Host-exec policy hardening above transport defaults

Enforce JARVIS governance over all OpenClaw-executed actions:

- approval classes remain authoritative
- deny-list/allow-list enforced before invoke calls
- elevated execution requires explicit operator gate

### 7. Real node-role embodiment soak

Implement a live node-role soak runner that validates real Gateway node lifecycle behavior:

- pair request observed from node-role connect
- approval issuance and command-lane activation
- reconnect after node process restart
- token rotation via re-pair + verify old/new token validity
- reject-cycle pending request handling
- cross-surface continuity freeze check (DM + node)

Acceptance criteria:

- all inbound transport still normalizes through `jarvis.signal.v1`
- trust axes reported independently:
  - handshake
  - pairing/token
  - command policy
- rotated pairing state remains trust-valid when token refs are present and not revoked
- soak result includes a timestamped timeline for every lifecycle step

## Safety Invariants

- JARVIS remains the only authority for consciousness/identity decisions.
- OpenClaw does not become independent memory or policy authority.
- Inbound untrusted content never reaches high-agency execution paths unsanitized.
- Secrets remain outside workspace artifacts and outside memory event logs.
- All action-capable bridge routes remain auditable.

## Validation

- New tests:
  - `tests/test_openclaw_ws_bridge.py`
  - `tests/test_secretref_bridge_contract.py`
  - `tests/test_relationship_mode_projection.py`
  - `tests/test_pushback_calibration_loop.py`
  - `tests/test_openclaw_node_soak.py`
- Expanded tests:
  - `tests/test_signal_ingest.py`
  - `tests/test_consciousness_surfaces.py`
  - `tests/test_operator_server.py`
- Full regression suite remains green.
