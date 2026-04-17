from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis.runtime import JarvisRuntime


class OpenClawNodeSoakTests(unittest.TestCase):
    def test_node_soak_rejects_invalid_ws_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                result = runtime.run_openclaw_node_embodiment_soak(ws_url="not-a-ws-url")
                self.assertFalse(result.get("ok"))
                self.assertEqual(result.get("error"), "invalid_gateway_ws_url")
            finally:
                runtime.close()

    def test_node_soak_fails_closed_when_token_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                with patch.object(runtime, "_resolve_openclaw_gateway_token_for_soak", return_value=None):
                    result = runtime.run_openclaw_node_embodiment_soak(ws_url="ws://127.0.0.1:18789")
                self.assertFalse(result.get("ok"))
                self.assertEqual(result.get("error"), "gateway_token_not_available")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
