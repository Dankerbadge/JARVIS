from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime
from jarvis.workflows import Executor, PlanRepository, Planner


class WorkflowExtractionTests(unittest.TestCase):
    def test_runtime_uses_extracted_workflow_components(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            db = root / "jarvis.db"
            repo.mkdir(parents=True)

            runtime = JarvisRuntime(db_path=db, repo_path=repo)
            try:
                self.assertIsInstance(runtime.plan_repo, PlanRepository)
                self.assertIsInstance(runtime.planner, Planner)
                self.assertIsInstance(runtime.executor, Executor)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()

