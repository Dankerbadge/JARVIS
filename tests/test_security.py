from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.security import ActionClass, SecurityManager


class SecurityTests(unittest.TestCase):
    def test_permission_classes_and_approval_flow(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sec = SecurityManager(Path(td) / "jarvis.db")
            try:
                sec.enforce(ActionClass.P0)
                sec.enforce(ActionClass.P1)

                with self.assertRaises(PermissionError):
                    sec.enforce(ActionClass.P4)

                with self.assertRaises(PermissionError):
                    sec.enforce(ActionClass.P3)

                approval_id = sec.request_approval(
                    plan_id="pln_1",
                    step_id="stp_1",
                    action_class=ActionClass.P3,
                    action_desc="dangerous_action",
                )
                sec.approve(approval_id)
                sec.enforce(ActionClass.P3, approval_id=approval_id)
            finally:
                sec.close()

    def test_kill_switch_blocks_actions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sec = SecurityManager(Path(td) / "jarvis.db")
            try:
                sec.set_kill_switch(True)
                with self.assertRaises(PermissionError):
                    sec.enforce(ActionClass.P0)
            finally:
                sec.close()


if __name__ == "__main__":
    unittest.main()

