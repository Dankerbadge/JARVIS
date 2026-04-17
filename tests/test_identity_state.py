from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jarvis.identity_state import IdentityStateStore


class IdentityStateStoreTests(unittest.TestCase):
    def test_user_model_and_personal_context_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "jarvis.db"
            store = IdentityStateStore(db_path)
            try:
                model = store.get_user_model()
                self.assertIn("domain_weights", model)
                self.assertIn("goals", model)
                self.assertIn("consciousness_contract", model)
                goal_map = {
                    str(item.get("goal_id") or ""): item
                    for item in list(model.get("goals") or [])
                    if isinstance(item, dict)
                }
                make_money = goal_map.get("make_money") or {}
                self.assertEqual(make_money.get("strategy"), "legal_ethical_profit_optimization")
                self.assertIn("market_ml", list(make_money.get("focus_projects") or []))
                self.assertIn("gpu_upgrades", list(make_money.get("reinvestment_targets") or []))
                contract = model.get("consciousness_contract") if isinstance(model.get("consciousness_contract"), dict) else {}
                growth = contract.get("resource_growth_policy") if isinstance(contract.get("resource_growth_policy"), dict) else {}
                self.assertIn("jarvis", list(growth.get("focus_projects") or []))
                self.assertIn("trusted_data_required", list(growth.get("constraints") or []))
                sources = list(contract.get("development_sources") or [])
                source_ids = {str(item.get("source_id") or "") for item in sources if isinstance(item, dict)}
                self.assertIn("jarvis_3_doc", source_ids)
                self.assertIn("jarvis_25q_consciousness_doc", source_ids)
                inquiry = contract.get("epistemic_inquiry_protocol") if isinstance(contract.get("epistemic_inquiry_protocol"), dict) else {}
                self.assertTrue(bool(inquiry.get("enabled")))
                self.assertTrue(bool(inquiry.get("ask_before_concluding_when_conceptual_uncertainty")))
                constraints = model.get("constraints") if isinstance(model.get("constraints"), dict) else {}
                self.assertTrue(bool(constraints.get("profit_generation_requires_legal_and_ethical_compliance")))
                self.assertTrue(bool(constraints.get("trusted_data_required_for_growth_decisions")))
                self.assertTrue(bool(constraints.get("conceptual_clarification_with_human_enabled")))

                updated_model = store.set_domain_weight(domain="academics", weight=1.33, actor="tester")
                self.assertAlmostEqual(float(updated_model["domain_weights"]["academics"]), 1.33, places=3)

                updated_model = store.upsert_goal(
                    goal_id="ship_release",
                    label="Ship Release",
                    priority=1,
                    weight=1.4,
                    domains=["zenith"],
                    actor="tester",
                )
                goal_ids = {item.get("goal_id") for item in updated_model.get("goals", [])}
                self.assertIn("ship_release", goal_ids)

                context = store.update_personal_context(
                    stress_level=0.81,
                    energy_level=0.44,
                    sleep_hours=5.7,
                    available_focus_minutes=50,
                    mode="deep_work",
                    note="long day",
                    actor="tester",
                )
                self.assertAlmostEqual(float(context["stress_level"]), 0.81, places=2)
                self.assertEqual(int(context["available_focus_minutes"]), 50)

                contract = store.update_consciousness_contract(
                    patch={"interaction_modes": {"equal_ratio": 0.8}},
                    actor="tester",
                    replace=False,
                )
                self.assertAlmostEqual(float(contract["interaction_modes"]["equal_ratio"]), 0.8, places=2)

                events = store.list_events(limit=10)
                self.assertGreaterEqual(len(events), 4)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
