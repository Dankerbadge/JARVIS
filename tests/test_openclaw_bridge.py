from __future__ import annotations

import unittest

from jarvis.openclaw_bridge import OpenClawBridgeError, OpenClawToolsInvokeClient


class OpenClawBridgeTests(unittest.TestCase):
    def test_rejects_non_private_host_by_default(self) -> None:
        with self.assertRaises(ValueError):
            OpenClawToolsInvokeClient(
                base_url="https://example.com",
                bearer_token="token",
            )

    def test_deny_list_blocks_high_risk_tools(self) -> None:
        client = OpenClawToolsInvokeClient(
            base_url="http://127.0.0.1:3000",
            bearer_token="token",
        )
        with self.assertRaises(OpenClawBridgeError):
            client.invoke(tool="apply_patch", args={})


if __name__ == "__main__":
    unittest.main()
