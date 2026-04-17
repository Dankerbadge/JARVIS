from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.secref_nodes import (
    SecretRefError,
    parse_secret_ref,
    resolve_secret_ref,
    validate_node_secret_plan,
    validate_secret_ref,
)


class SecretRefNodeTests(unittest.TestCase):
    def test_env_refs_validate_and_plan_normalizes(self) -> None:
        ref = parse_secret_ref("env:OPENCLAW_GATEWAY_TOKEN")
        validate_secret_ref(ref)
        plan = validate_node_secret_plan(
            {
                "node_id": "node-1",
                "device_id": "phone-1",
                "owner_id": "owner-1",
                "gateway_token_ref": "env:OPENCLAW_GATEWAY_TOKEN",
                "node_token_ref": "env:OPENCLAW_NODE_TOKEN_1",
                "pairing_status": "PAIRED",
            }
        )
        self.assertEqual(plan["pairing_status"], "paired")
        self.assertEqual(plan["gateway_token_ref"], "env:OPENCLAW_GATEWAY_TOKEN")

    def test_file_refs_require_strict_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            secret_file = root / "token.txt"
            secret_file.write_text("token-value\n", encoding="utf-8")
            secret_file.chmod(0o600)

            ref = parse_secret_ref(f"file:{secret_file}")
            validate_secret_ref(ref, allowed_file_roots=[root])
            resolved = resolve_secret_ref(ref)
            self.assertEqual(resolved, "token-value")

            secret_file.chmod(0o644)
            with self.assertRaises(SecretRefError):
                validate_secret_ref(ref, allowed_file_roots=[root])

    def test_invalid_env_name_rejected(self) -> None:
        ref = parse_secret_ref("env:not-valid")
        with self.assertRaises(SecretRefError):
            validate_secret_ref(ref)


if __name__ == "__main__":
    unittest.main()
