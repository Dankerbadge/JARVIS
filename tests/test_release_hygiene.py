from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.release_hygiene import scan_release_root, verify_release_clean


class ReleaseHygieneTests(unittest.TestCase):
    def test_verify_clean_release_root_passes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("ok\n", encoding="utf-8")
            (root / "jarvis").mkdir(parents=True, exist_ok=True)
            (root / "jarvis" / "runtime.py").write_text("print('ok')\n", encoding="utf-8")

            manifest, report = verify_release_clean(root, strict=True)
            self.assertTrue(report["ok"])
            self.assertGreaterEqual(manifest["file_count"], 2)

    def test_verify_clean_release_root_blocks_forbidden_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".jarvis").mkdir(parents=True, exist_ok=True)
            (root / ".jarvis" / "jarvis.db").write_text("sqlite bytes\n", encoding="utf-8")

            report = scan_release_root(root)
            self.assertFalse(report["ok"])
            self.assertTrue(report["forbidden_paths"])
            with self.assertRaises(ValueError):
                verify_release_clean(root, strict=True)

    def test_verify_clean_release_root_blocks_secret_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config.txt").write_text(
                "Authorization: Bearer github_pat_exampletokenvalue123456\n",
                encoding="utf-8",
            )

            report = scan_release_root(root)
            self.assertFalse(report["ok"])
            self.assertTrue(report["secret_hits"])
            with self.assertRaises(ValueError):
                verify_release_clean(root, strict=True)


if __name__ == "__main__":
    unittest.main()
