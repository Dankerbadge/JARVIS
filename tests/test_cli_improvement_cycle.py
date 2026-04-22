from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jarvis.cli import cmd_improvement_cycle_from_file


class CliImprovementCycleTests(unittest.TestCase):
    def _make_repo(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        db = root / "jarvis.db"
        (repo / "ui").mkdir(parents=True)
        (repo / "ui" / "zenith_ui.txt").write_text("TODO_UI\n", encoding="utf-8")
        (repo / "service.py").write_text(
            "def x():\n    return 'TODO_ZENITH'\n",
            encoding="utf-8",
        )
        return repo, db

    def _base_args(self, *, repo: Path, db: Path, input_path: Path, report_path: Path | None) -> argparse.Namespace:
        return argparse.Namespace(
            domain="fitness_apps",
            source="app_store_reviews",
            input_path=input_path,
            input_format="jsonl",
            default_segment="general",
            default_severity=3.0,
            default_frustration_score=None,
            status="open",
            min_cluster_count=1,
            proposal_limit=5,
            owner="operator",
            auto_register=True,
            report_cluster_limit=10,
            report_hypothesis_limit=30,
            report_experiment_limit=50,
            report_queue_limit=20,
            report_path=report_path,
            json_compact=False,
            repo_path=repo,
            db_path=db,
        )

    def test_cycle_from_file_writes_report_and_dedupes_on_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            feedback_path = root / "fitness_feedback.jsonl"
            feedback_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "x1",
                                "title": "Paywall too early",
                                "review": "Paywall appears before I can try any workout.",
                                "rating": 2,
                            }
                        ),
                        json.dumps(
                            {
                                "id": "x2",
                                "summary": "Paywall before trying core features",
                                "rating": 2,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            report_path = root / "reports" / "fitness_inbox.json"

            args = self._base_args(repo=repo, db=db, input_path=feedback_path, report_path=report_path)
            first_out = io.StringIO()
            with redirect_stdout(first_out):
                cmd_improvement_cycle_from_file(args)
            first_payload = json.loads(first_out.getvalue())
            first_cycle = dict((first_payload.get("cycle") or {}).get("cycle") or {})
            first_ingest = dict((first_payload.get("cycle") or {}).get("ingest") or {})

            self.assertEqual(int(first_ingest.get("loaded_record_count") or 0), 2)
            self.assertEqual(int(first_ingest.get("ingested_count") or 0), 2)
            self.assertGreaterEqual(int(first_cycle.get("proposal_count") or 0), 1)
            self.assertGreaterEqual(int(first_cycle.get("created_count") or 0), 1)
            self.assertTrue(report_path.exists())

            report_payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(str(report_payload.get("domain") or ""), "fitness_apps")
            self.assertGreaterEqual(int(report_payload.get("ranked_queue_count") or 0), 1)

            second_out = io.StringIO()
            with redirect_stdout(second_out):
                cmd_improvement_cycle_from_file(args)
            second_payload = json.loads(second_out.getvalue())
            second_cycle = dict((second_payload.get("cycle") or {}).get("cycle") or {})

            self.assertEqual(int(second_cycle.get("created_count") or 0), 0)
            self.assertGreaterEqual(int(second_cycle.get("skipped_existing_count") or 0), 1)

    def test_cycle_from_file_no_auto_register_keeps_proposals_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, db = self._make_repo(root)
            feedback_path = root / "fitness_feedback.jsonl"
            feedback_path.write_text(
                json.dumps(
                    {
                        "id": "x3",
                        "summary": "App crashes after sync",
                        "rating": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            args = self._base_args(repo=repo, db=db, input_path=feedback_path, report_path=None)
            args.auto_register = False

            out = io.StringIO()
            with redirect_stdout(out):
                cmd_improvement_cycle_from_file(args)
            payload = json.loads(out.getvalue())
            cycle = dict((payload.get("cycle") or {}).get("cycle") or {})

            self.assertGreaterEqual(int(cycle.get("proposal_count") or 0), 1)
            self.assertEqual(int(cycle.get("created_count") or 0), 0)


if __name__ == "__main__":
    unittest.main()
