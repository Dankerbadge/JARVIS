from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jarvis.model_backends.heuristic import HeuristicCognitionBackend
from jarvis.runtime import JarvisRuntime


class ReplayAssistedBackend(HeuristicCognitionBackend):
    name = "replay_assisted"
    model = "mock-local-cognition"
    model_assisted = True

    def draft_synthesis(self, *, kind: str, structured: dict, context: dict) -> str | None:
        if kind == "morning":
            return (
                "Tradeoff: prioritize academics exam risk over non-critical zenith cleanup, "
                "while monitoring zenith for only high-impact regressions."
            )
        if kind == "evening":
            return (
                "Tradeoff review: accepted high-impact interrupts and suppressed low-value churn; "
                "carry unresolved approvals with bounded scope tomorrow."
            )
        return super().draft_synthesis(kind=kind, structured=structured, context=context)


class CognitionReplayTests(unittest.TestCase):
    def _fixture(self) -> dict:
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "replays"
            / "zenith_academics_tradeoff.json"
        )
        return json.loads(fixture_path.read_text(encoding="utf-8"))

    def _run_replay(self, backend: HeuristicCognitionBackend) -> dict[str, float]:
        fixture = self._fixture()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "ui").mkdir(parents=True, exist_ok=True)
            (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
            (repo / "service.py").write_text("def run():\n    return 'TODO_ZENITH'\n", encoding="utf-8")

            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                cognition_backend=backend,
                cognition_enabled=True,
            )
            try:
                for outcome in fixture.get("outcomes", []):
                    runtime.plan_repo.record_outcome(
                        plan_id=str(outcome["plan_id"]),
                        repo_id=str(outcome["repo_id"]),
                        branch=str(outcome["branch"]),
                        status=str(outcome["status"]),
                        touched_paths=list(outcome.get("touched_paths", [])),
                        summary=str(outcome.get("summary") or ""),
                    )

                runtime.ingest_event(
                    source="zenith",
                    source_type="ci",
                    payload={"project": "zenith", "status": "failed", "deadline_hours": 24},
                )
                runtime.ingest_event(
                    source="academics",
                    source_type="academic.risk_signal",
                    payload={
                        "project": "academics",
                        "domain": "academics",
                        "course_id": "CS101",
                        "term_id": "2026-spring",
                        "severity": "high",
                        "reason": "exam_in_36h",
                    },
                )

                runtime.run_cognition_cycle()
                thought = runtime.list_recent_thoughts(limit=1)[0]
                morning = runtime.generate_morning_synthesis()
                interrupts = runtime.list_interrupts(status="all", limit=50)

                hyps = list(thought.get("hypotheses", []))
                skepticism_quality = sum(len(item.get("skepticism_flags", [])) for item in hyps) / max(1, len(hyps))
                hypothesis_usefulness = sum(float(item.get("expected_value", 0.0)) for item in hyps) / max(1, len(hyps))

                delivered = [item for item in interrupts if item.get("status") == "delivered"]
                delivered_high = [item for item in delivered if float(item.get("urgency_score", 0.0)) >= 0.7]
                interruption_precision = len(delivered_high) / max(1, len(delivered))

                narrative = str(morning.get("narrative") or "")
                synthesis_coherence = 1.0 if len(narrative.split()) >= 10 else 0.25
                cross_domain_tradeoff_quality = (
                    1.0
                    if (
                        "tradeoff" in narrative.lower()
                        and "academics" in narrative.lower()
                        and "zenith" in narrative.lower()
                    )
                    else 0.0
                )

                return {
                    "hypothesis_usefulness": hypothesis_usefulness,
                    "skepticism_quality": skepticism_quality,
                    "interruption_precision": interruption_precision,
                    "synthesis_coherence": synthesis_coherence,
                    "cross_domain_tradeoff_quality": cross_domain_tradeoff_quality,
                }
            finally:
                runtime.close()

    def test_model_assisted_replay_improves_tradeoff_or_synthesis(self) -> None:
        heuristic_scores = self._run_replay(HeuristicCognitionBackend(local_only=True))
        assisted_scores = self._run_replay(ReplayAssistedBackend(local_only=True))

        improved_dimensions = [
            name
            for name in (
                "synthesis_coherence",
                "cross_domain_tradeoff_quality",
                "skepticism_quality",
                "interruption_precision",
                "hypothesis_usefulness",
            )
            if assisted_scores[name] > heuristic_scores[name]
        ]
        self.assertTrue(
            improved_dimensions,
            msg=f"Expected at least one improved dimension, baseline={heuristic_scores}, assisted={assisted_scores}",
        )


if __name__ == "__main__":
    unittest.main()
