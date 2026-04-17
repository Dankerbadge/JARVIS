# Milestone 10: Local Cognition Backend + Release Hygiene Gate

Milestone 10 introduces a configurable local cognition backend boundary behind the M9 cognition loop while preserving planner/executor governance, and adds a mandatory pre-release artifact hygiene gate for packaging.

## Scope

### A. Local model adapter boundary

New package:

- `jarvis/model_backends/base.py`
- `jarvis/model_backends/heuristic.py`
- `jarvis/model_backends/ollama_backend.py`
- `jarvis/model_backends/llama_cpp_backend.py`
- `jarvis/model_backends/__init__.py`

Runtime configuration:

- `JARVIS_COGNITION_BACKEND=heuristic|ollama|llama_cpp`
- `JARVIS_COGNITION_MODEL=<model-name>`
- `JARVIS_COGNITION_ENABLED=true|false`
- `JARVIS_COGNITION_LOCAL_ONLY=true|false`

Operator surface:

- `python3 -m jarvis.cli thoughts config`

Backend responsibilities:

- hypothesis generation
- skepticism pass / counter-signals
- dead-end diagnosis
- synthesis drafting
- interrupt rationale drafting

Guardrails retained:

- no backend authority to execute actions
- no bypass of planner/executor split
- no bypass of permission classes / approvals

### B. Model-assisted cognition integration

The cognition loop now records backend metadata in thought artifacts and can run with deterministic heuristic fallback or local-model-assisted mode.

Thought artifacts include `backend_mode` for per-cycle provenance:

- `heuristic`
- `ollama_assisted`
- `llama_cpp_assisted`
- `heuristic_fallback`

Morning/evening synthesis artifacts support backend-drafted narrative text while preserving structured fields used by policy and tooling.

### C. Replay/evaluation harness

Added replay and backend contract tests to compare heuristic-only vs model-assisted cognition behavior, including dimensions for:

- hypothesis usefulness
- skepticism quality
- interruption precision
- synthesis coherence
- cross-domain tradeoff quality

### D. Required release hygiene gate

New hygiene scanner:

- `jarvis/release_hygiene.py`
- `scripts/verify_release_clean.py`

Packaging script now requires hygiene verification before zip generation:

- `scripts/build_release_zip.sh`

Verification outputs:

- release manifest JSON
- release scan report JSON
- zip SHA-256

Forbidden artifacts include `.jarvis`, DB files, key/pem files, private key patterns, nested zips, `.env` files, worktree residue, and secret/token regex hits.

CI enforcement:

- `.github/workflows/release-hygiene.yml`

## Acceptance criteria mapping

1. Heuristic mode remains available and test-covered.
2. Local backend selection is config-driven and test-covered.
3. Thought/synthesis generation works in heuristic and assisted modes.
4. Replay test verifies at least one improved quality dimension in assisted mode.
5. Release packaging fails when forbidden artifacts/secrets are detected.
6. Clean packaging emits reproducible manifest + scan report + archive hash.
