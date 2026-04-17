from __future__ import annotations

import unittest
from unittest.mock import patch

from jarvis.model_backends import build_backend_from_env
from jarvis.model_backends.heuristic import HeuristicCognitionBackend
from jarvis.model_backends.ollama_backend import OllamaCognitionBackend


class ModelBackendContractTests(unittest.TestCase):
    def test_heuristic_backend_generates_bounded_hypotheses(self) -> None:
        backend = HeuristicCognitionBackend(local_only=True)
        risks = [
            {
                "entity_key": "academic_risk:cs101",
                "confidence": 0.84,
                "source_refs": ["evt:1"],
                "value": {"domain": "academics", "reason": "exam_due_soon", "severity": "high"},
            },
            {
                "entity_key": "zenith_risk:release",
                "confidence": 0.73,
                "source_refs": ["evt:2"],
                "value": {"domain": "zenith", "reason": "ci_failure", "severity": "medium"},
            },
        ]
        outcomes = [{"repo_id": "academics", "status": "failure"}]
        hypotheses = backend.generate_hypotheses(
            risks=risks,
            recent_outcomes=outcomes,
            max_hypotheses=4,
        )
        self.assertGreaterEqual(len(hypotheses), 1)
        self.assertLessEqual(len(hypotheses), 4)
        for hypothesis in hypotheses:
            self.assertGreaterEqual(hypothesis.confidence, 0.0)
            self.assertLessEqual(hypothesis.confidence, 1.0)
            self.assertGreaterEqual(hypothesis.expected_value, 0.0)
            self.assertLessEqual(hypothesis.expected_value, 1.0)

    def test_ollama_backend_transport_refines_hypothesis(self) -> None:
        def fake_transport(url: str, payload: dict, timeout: float) -> dict:
            return {
                "response": '{"updates":[{"index":0,"claim":"Refined hypothesis","skepticism_flags":["counter_signal"],"confidence_delta":0.05}]}'
            }

        backend = OllamaCognitionBackend(
            model="test-local",
            endpoint="http://127.0.0.1:11434/api/generate",
            local_only=True,
            transport=fake_transport,
        )
        risks = [
            {
                "entity_key": "zenith_risk:release",
                "confidence": 0.7,
                "value": {"domain": "zenith", "reason": "ci_failure", "severity": "high"},
            }
        ]
        hypotheses = backend.generate_hypotheses(risks=risks, recent_outcomes=[], max_hypotheses=2)
        self.assertEqual(hypotheses[0].claim, "Refined hypothesis")
        self.assertIn("counter_signal", hypotheses[0].skepticism_flags)
        self.assertGreater(hypotheses[0].confidence, 0.7)

    def test_ollama_synthesis_accepts_non_narrative_json_shapes(self) -> None:
        def fake_transport(url: str, payload: dict, timeout: float) -> dict:
            return {
                "response": '{"operator":"Reactive Reframer","description":"Respond calmly and ask for the desired outcome."}'
            }

        backend = OllamaCognitionBackend(
            model="test-local",
            endpoint="http://127.0.0.1:11434/api/generate",
            local_only=True,
            transport=fake_transport,
        )
        narrative = backend.draft_synthesis(
            kind="presence_reply",
            structured={"user_text": "What is your problem?"},
            context={},
        )
        self.assertIsInstance(narrative, str)
        self.assertIn("Respond calmly", str(narrative))

    def test_backend_builder_honors_env(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "JARVIS_COGNITION_BACKEND": "heuristic",
                "JARVIS_COGNITION_LOCAL_ONLY": "true",
            },
            clear=False,
        ):
            backend = build_backend_from_env()
        self.assertEqual(backend.name, "heuristic")
        self.assertTrue(backend.local_only)

    def test_backend_builder_auto_selects_local_ollama(self) -> None:
        with patch("jarvis.model_backends._fetch_ollama_models", return_value=["llama3.2:3b"]):
            with patch.dict(
                "os.environ",
                {
                    "JARVIS_COGNITION_BACKEND": "auto",
                    "JARVIS_COGNITION_MODEL": "",
                },
                clear=False,
            ):
                backend = build_backend_from_env()
        self.assertEqual(backend.name, "ollama")
        self.assertEqual(backend.model, "llama3.2:3b")

    def test_backend_builder_auto_falls_back_to_heuristic_when_ollama_unavailable(self) -> None:
        with patch("jarvis.model_backends._fetch_ollama_models", return_value=[]):
            with patch.dict(
                "os.environ",
                {
                    "JARVIS_COGNITION_BACKEND": "auto",
                    "JARVIS_COGNITION_MODEL": "",
                },
                clear=False,
            ):
                backend = build_backend_from_env()
        self.assertEqual(backend.name, "heuristic")


if __name__ == "__main__":
    unittest.main()
