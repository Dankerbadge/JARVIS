from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class ConsciousnessSurfaceTests(unittest.TestCase):
    def test_surface_generation_and_contract_update(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                refreshed = runtime.refresh_consciousness_surfaces(reason="test")
                self.assertEqual(refreshed.get("reason"), "test")
                files = refreshed.get("files") or []
                self.assertEqual(len(files), 8)
                names = {item.get("name") for item in files}
                self.assertEqual(names, {"SOUL", "IDENTITY", "TOOLS", "AGENTS", "USER", "HEARTBEAT", "BOOT", "MEMORY"})
                for item in files:
                    self.assertTrue(Path(str(item.get("path"))).exists())

                contract = runtime.update_consciousness_contract(
                    patch={"interaction_modes": {"equal_ratio": 0.8}},
                    actor="tester",
                )
                self.assertAlmostEqual(float(contract["interaction_modes"]["equal_ratio"]), 0.8, places=2)

                runtime.refresh_consciousness_surfaces(reason="contract_update")
                surfaces = runtime.get_consciousness_surfaces(include_content=True)
                soul = next(item for item in surfaces.get("files") or [] if item.get("name") == "SOUL")
                self.assertIn("Core Commitments", str(soul.get("content") or ""))
                self.assertIn("Resource Growth Mandate", str(soul.get("content") or ""))
                self.assertIn("Epistemic Inquiry Protocol", str(soul.get("content") or ""))
                identity = next(item for item in surfaces.get("files") or [] if item.get("name") == "IDENTITY")
                self.assertIn("Development Sources", str(identity.get("content") or ""))
                self.assertIn("JARVIS 25Q CONSCIOUSNESS", str(identity.get("content") or ""))
                user = next(item for item in surfaces.get("files") or [] if item.get("name") == "USER")
                self.assertIn("Human Clarification Model", str(user.get("content") or ""))

                events = runtime.list_consciousness_events(limit=50)
                self.assertTrue(any(str(item.get("event_type")) == "consciousness.surfaces_refreshed" for item in events))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
