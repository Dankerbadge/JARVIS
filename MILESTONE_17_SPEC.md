# MILESTONE 17: CONSCIOUSNESS-FIRST LIFT (OPENCLAW CONTRACTS)

## Goal

Lift consciousness-critical contracts from the new directives into core JARVIS runtime:
canonical signal ingest, file-backed mind surfaces, and auditable memory telemetry.

## Scope

### 1. Canonical cross-boundary ingestion (`SignalEnvelope`)

- Add `jarvis/signals.py` with:
  - `Provenance` and `SignalEnvelope` schema (`jarvis.signal.v1`)
  - payload sanitization/redaction defaults
  - replay-safe dedupe hash and dedupe key generation
  - persistent ingest ledger (`SignalIngestStore`)
- Add runtime ingest API:
  - `JarvisRuntime.ingest_signal(...)`
  - dedupe-safe acceptance and replay handling
  - normalized provenance + trust metadata propagation into event payloads

### 2. Operator ingest surface + security boundary

- Add HTTP endpoints:
  - `POST /api/ingest`
  - `POST /api/ingest/signal` (alias)
  - `GET /api/ingest/signals`
- Token gate behavior:
  - optional `JARVIS_INGEST_TOKEN` enforcement
  - returns 401 when token is required and invalid

### 3. Consciousness surfaces (file-backed mind artifacts)

- Add `jarvis/consciousness.py`:
  - `SOUL.md`
  - `IDENTITY.md`
  - `TOOLS.md`
  - `AGENTS.md`
  - `MEMORY.md`
- Surfaces are generated from authoritative runtime state:
  - identity model + personal context
  - operator governance
  - recent thought artifacts and digest anchors
- Add endpoints:
  - `GET /api/consciousness/surfaces`
  - `POST /api/consciousness/refresh`
  - `GET /api/consciousness/events`

### 4. JSONL memory telemetry

- Extend `MemoryStore` with append-only JSONL event log:
  - `memory/.dreams/events.jsonl`
- Emit events for:
  - episode adds
  - semantic adds
  - procedure upserts
  - semantic recalls
  - runtime ingest and cognition/digest lifecycle milestones

### 5. 25Q contract persistence in identity state

- Extend `IdentityStateStore` with durable `consciousness_contract_json`
- Include default commitments aligned to 25Q directives:
  - epistemic humility
  - pushback + override semantics
  - refusal conditions
  - degradation signals
  - mode ratios (equal/butler/strategist)
- Add endpoints:
  - `GET /api/identity/consciousness-contract`
  - `POST /api/identity/consciousness-contract`

### 6. OpenClaw bridge client (guarded)

- Add `jarvis/openclaw_bridge.py`:
  - private/loopback-first host guard
  - deny-list enforcement for high-risk tools
  - allow-list option
  - `/tools/invoke` client wrapper

## Safety Invariants

- Suggestion-first planning/execution boundaries remain intact.
- Untrusted inbound content is sanitized/redacted before high-agency use.
- Replay safety is enforced for ingest via dedupe keys.
- New tooling surfaces are read-only by default and auditable.

## Validation

- Add tests for:
  - signal ingest normalization + dedupe behavior
  - consciousness surface generation
  - memory telemetry JSONL events
  - identity consciousness contract persistence
  - ingest/consciousness API routes on operator server
