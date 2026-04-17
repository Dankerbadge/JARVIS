from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.evaluation import compare_backends_on_snapshot
from jarvis.model_backends.heuristic import HeuristicCognitionBackend
from jarvis.runtime import JarvisRuntime


class _AssistedBackend(HeuristicCognitionBackend):
    name = "assisted_eval"
    model = "mock-local"
    model_assisted = True

    def draft_synthesis(self, *, kind: str, structured: dict, context: dict) -> str | None:
        if kind == "morning":
            return (
                "Tradeoff: prioritize academics exam risk over non-critical zenith cleanup, "
                "while watching zenith for high-impact regressions only."
            )
        return super().draft_synthesis(kind=kind, structured=structured, context=context)


class CognitionEvaluationTests(unittest.TestCase):
    def test_compare_backends_reports_improved_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "ui").mkdir(parents=True, exist_ok=True)
            (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
            (repo / "service.py").write_text("def run():\n    return 'TODO_ZENITH'\n", encoding="utf-8")

            snapshot = root / "snapshot.db"
            runtime = JarvisRuntime(db_path=snapshot, repo_path=repo)
            try:
                runtime.ingest_event(
                    source="zenith",
                    source_type="ci",
                    payload={"project": "zenith", "status": "failed", "deadline_hours": 18},
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
            finally:
                runtime.close()

            def _backend_factory(*, backend_name: str, model_name: str, local_only: bool, **_: object):
                if backend_name == "secondary":
                    return _AssistedBackend(local_only=local_only)
                return HeuristicCognitionBackend(local_only=local_only)

            with patch("jarvis.evaluation.build_backend", side_effect=_backend_factory):
                result = compare_backends_on_snapshot(
                    db_snapshot_path=snapshot,
                    repo_path=repo,
                    primary_backend="primary",
                    secondary_backend="secondary",
                )

            self.assertIn("primary", result)
            self.assertIn("secondary", result)
            self.assertTrue(result.get("improved_dimensions"))


if __name__ == "__main__":
    unittest.main()
