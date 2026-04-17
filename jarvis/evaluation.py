from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from .model_backends import build_backend
from .runtime import JarvisRuntime


def _score_bundle(*, thought: dict[str, Any], morning: dict[str, Any], interrupts: list[dict[str, Any]]) -> dict[str, float]:
    hyps = list(thought.get("hypotheses", []))
    skepticism_quality = sum(len(item.get("skepticism_flags", [])) for item in hyps) / max(1, len(hyps))
    hypothesis_usefulness = sum(float(item.get("expected_value", 0.0)) for item in hyps) / max(1, len(hyps))

    delivered = [item for item in interrupts if item.get("status") == "delivered"]
    delivered_high = [item for item in delivered if float(item.get("urgency_score", 0.0)) >= 0.7]
    interruption_precision = len(delivered_high) / max(1, len(delivered))

    narrative = str(morning.get("narrative") or "")
    synthesis_coherence = 1.0 if len(narrative.split()) >= 10 else 0.25
    lower = narrative.lower()
    cross_domain_tradeoff_quality = 1.0 if (
        "academics" in lower and "zenith" in lower and ("tradeoff" in lower or "depriorit" in lower)
    ) else 0.0

    return {
        "hypothesis_usefulness": hypothesis_usefulness,
        "skepticism_quality": skepticism_quality,
        "interruption_precision": interruption_precision,
        "synthesis_coherence": synthesis_coherence,
        "cross_domain_tradeoff_quality": cross_domain_tradeoff_quality,
    }


def _run_backend_once(
    *,
    db_snapshot_path: Path,
    repo_path: Path,
    backend_name: str,
    model_name: str,
    local_only: bool,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        run_db = temp_root / "eval.db"
        shutil.copy2(db_snapshot_path, run_db)
        backend = build_backend(
            backend_name=backend_name,
            model_name=model_name,
            local_only=local_only,
        )
        runtime = JarvisRuntime(
            db_path=run_db,
            repo_path=repo_path,
            cognition_backend=backend,
            cognition_enabled=True,
        )
        try:
            # Force one cycle on this isolated copy regardless of recent cycle timing.
            runtime.cognition.min_cycle_interval_seconds = 0
            cycle = runtime.run_cognition_cycle()
            thoughts = runtime.list_recent_thoughts(limit=1)
            thought = thoughts[0] if thoughts else {}
            morning = runtime.generate_morning_synthesis()
            interrupts = runtime.list_interrupts(status="all", limit=80)
            scores = _score_bundle(thought=thought, morning=morning, interrupts=interrupts)
            return {
                "backend": backend_name,
                "model": model_name or backend.model,
                "cycle": cycle,
                "thought": thought,
                "morning_synthesis": morning,
                "interrupt_count": len(interrupts),
                "scores": scores,
            }
        finally:
            runtime.close()


def compare_backends_on_snapshot(
    *,
    db_snapshot_path: str | Path,
    repo_path: str | Path,
    primary_backend: str,
    primary_model: str = "",
    secondary_backend: str,
    secondary_model: str = "",
    local_only: bool = True,
) -> dict[str, Any]:
    snapshot = Path(db_snapshot_path).resolve()
    if not snapshot.exists():
        raise FileNotFoundError(f"Snapshot DB not found: {snapshot}")
    repo = Path(repo_path).resolve()
    first = _run_backend_once(
        db_snapshot_path=snapshot,
        repo_path=repo,
        backend_name=primary_backend,
        model_name=primary_model,
        local_only=local_only,
    )
    second = _run_backend_once(
        db_snapshot_path=snapshot,
        repo_path=repo,
        backend_name=secondary_backend,
        model_name=secondary_model,
        local_only=local_only,
    )
    improved_dimensions = [
        name
        for name in (
            "hypothesis_usefulness",
            "skepticism_quality",
            "interruption_precision",
            "synthesis_coherence",
            "cross_domain_tradeoff_quality",
        )
        if float(second["scores"].get(name, 0.0)) > float(first["scores"].get(name, 0.0))
    ]
    return {
        "snapshot_db_path": str(snapshot),
        "repo_path": str(repo),
        "primary": first,
        "secondary": second,
        "improved_dimensions": improved_dimensions,
    }
