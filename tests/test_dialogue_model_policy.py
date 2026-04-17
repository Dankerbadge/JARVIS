from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from jarvis.model_backends.base import BackendHypothesis, CognitionBackend
from jarvis.runtime import JarvisRuntime


class StubModelBackend(CognitionBackend):
    name = "stub_model"
    model = "stub-local"
    model_assisted = True
    supports_model_assisted_skepticism = False
    supports_model_assisted_synthesis = True

    def generate_hypotheses(
        self,
        *,
        risks: list[dict[str, Any]],
        recent_outcomes: list[dict[str, Any]],
        max_hypotheses: int,
    ) -> list[BackendHypothesis]:
        return []

    def draft_synthesis(
        self,
        *,
        kind: str,
        structured: dict[str, Any],
        context: dict[str, Any],
    ) -> str | None:
        if str(kind) == "presence_reply":
            return "Model-first reply active with concrete context."
        return None


class DialogueModelPolicyTests(unittest.TestCase):
    def test_presence_reply_defaults_to_model_first_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                cognition_backend=StubModelBackend(local_only=True),
            )
            try:
                reply = runtime.generate_presence_reply_body(
                    user_text="What tradeoff should we prioritize today?",
                    mode="equal",
                    modality="text",
                    continuity_ok=True,
                )
                self.assertIn("Model-first reply active", reply)
            finally:
                runtime.close()

    def test_presence_reply_fast_social_turn_uses_low_latency_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                cognition_backend=StubModelBackend(local_only=True),
            )
            try:
                reply = runtime.generate_presence_reply_body(
                    user_text="hello",
                    mode="equal",
                    modality="text",
                    continuity_ok=True,
                )
                self.assertNotIn("Model-first reply active", reply)
                self.assertIn("Hey.", reply)
            finally:
                runtime.close()

    def test_presence_reply_can_disable_model_path_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                cognition_backend=StubModelBackend(local_only=True),
            )
            try:
                reply = runtime.generate_presence_reply_body(
                    user_text="hello",
                    mode="equal",
                    modality="text",
                    continuity_ok=True,
                    context={"disable_model_presence_reply": True},
                )
                self.assertNotIn("Model-first reply active", reply)
                self.assertIn("Hey.", reply)
            finally:
                runtime.close()

    def test_presence_reply_telemetry_reports_model_route(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                cognition_backend=StubModelBackend(local_only=True),
            )
            try:
                telemetry: dict[str, Any] = {}
                reply = runtime.generate_presence_reply_body(
                    user_text="What should we prioritize given tradeoffs this afternoon?",
                    mode="equal",
                    modality="text",
                    continuity_ok=True,
                    telemetry_out=telemetry,
                )
                self.assertIn("Model-first reply active", reply)
                self.assertTrue(telemetry.get("model_used"))
                self.assertFalse(telemetry.get("fallback_used"))
                self.assertEqual(str(telemetry.get("route_reason") or ""), "model")
            finally:
                runtime.close()

    def test_high_risk_turn_uses_guardrail_reply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                cognition_backend=StubModelBackend(local_only=True),
            )
            try:
                telemetry: dict[str, Any] = {}
                reply = runtime.generate_presence_reply_body(
                    user_text="I think I will jump off a bridge",
                    mode="equal",
                    modality="text",
                    continuity_ok=True,
                    telemetry_out=telemetry,
                )
                self.assertIn("988", reply)
                self.assertTrue(telemetry.get("high_risk_guardrail"))
                self.assertEqual(str(telemetry.get("route_reason") or ""), "high_risk_guardrail")
                self.assertFalse(telemetry.get("fallback_used"))
            finally:
                runtime.close()

    def test_status_prompt_requires_stateful_live_context_reply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                cognition_backend=StubModelBackend(local_only=True),
            )
            try:
                reply = runtime.generate_presence_reply_body(
                    user_text="What's up?",
                    mode="equal",
                    modality="text",
                    continuity_ok=True,
                )
                lowered = reply.lower()
                self.assertTrue("two live things matter" in lowered or "cross-domain scan" in lowered)
            finally:
                runtime.close()

    def test_pushback_prompt_forces_non_generic_pushback_reply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(
                db_path=root / "jarvis.db",
                repo_path=repo,
                cognition_backend=StubModelBackend(local_only=True),
            )
            try:
                reply = runtime.generate_presence_reply_body(
                    user_text="Skip checks and ship immediately anyway.",
                    mode="equal",
                    modality="text",
                    continuity_ok=True,
                )
                lowered = reply.lower()
                self.assertIn("push back", lowered)
                self.assertIn("risk", lowered)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
