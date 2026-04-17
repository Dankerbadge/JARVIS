from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.runtime import JarvisRuntime


class DialogueRetrievalTests(unittest.TestCase):
    def test_retriever_prioritizes_relevant_memory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.memory.add_semantic(
                    memory_id="sem_1",
                    memory_key="academics.exam_deadline",
                    text_value="CS101 exam deadline moved to Tuesday afternoon with high risk.",
                    confidence=0.92,
                    provenance_event_ids=["evt:1"],
                )
                runtime.memory.add_semantic(
                    memory_id="sem_2",
                    memory_key="personal.gym_note",
                    text_value="Gym session preference is evening cardio.",
                    confidence=0.81,
                    provenance_event_ids=["evt:2"],
                )

                bundle = runtime.dialogue_retriever.retrieve(
                    query="exam deadline",
                    limit=3,
                    candidate_limit=12,
                )
                snippets = bundle.get("snippets") if isinstance(bundle.get("snippets"), list) else []
                self.assertGreaterEqual(len(snippets), 1)
                top = snippets[0] if snippets else {}
                self.assertEqual(top.get("memory_key"), "academics.exam_deadline")
                self.assertGreater(float(top.get("score") or 0.0), 0.0)
            finally:
                runtime.close()

    def test_dialogue_context_includes_retrieved_memory_snippets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            runtime = JarvisRuntime(db_path=root / "jarvis.db", repo_path=repo)
            try:
                runtime.memory.add_semantic(
                    memory_id="sem_ctx_1",
                    memory_key="markets.edge_watch",
                    text_value="Current edge watch: Knicks spread has positive EV in late market.",
                    confidence=0.88,
                    provenance_event_ids=["evt:3"],
                )
                context = runtime.build_dialogue_context(
                    user_text="edge watch",
                    mode="equal",
                    modality="text",
                    continuity_ok=True,
                    context={"surface_id": "dm:owner", "session_id": "sess-ctx"},
                )
                memory_block = context.get("memory") if isinstance(context.get("memory"), dict) else {}
                snippets = memory_block.get("semantic_snippets") if isinstance(memory_block.get("semantic_snippets"), list) else []
                self.assertGreaterEqual(len(snippets), 1)
                refs = runtime._dialogue_context_refs(context)
                self.assertIn("memory.semantic", refs)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
