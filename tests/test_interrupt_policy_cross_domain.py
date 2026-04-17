from __future__ import annotations

import unittest

from jarvis.interrupts import InterruptCandidate, InterruptPolicy


class InterruptPolicyCrossDomainTests(unittest.TestCase):
    def test_academic_deadline_suppresses_minor_zenith_noise(self) -> None:
        policy = InterruptPolicy(base_threshold=0.72)
        minor = InterruptCandidate(
            candidate_id="cand_minor",
            domain="zenith",
            reason="minor_ui_cleanup",
            urgency_score=0.66,
            confidence=0.72,
            state_refs=("risk:zenith:ui",),
        )
        decision = policy.evaluate(
            minor,
            suppression_windows=["academic_deadline_focus"],
            active_focus_domain="academics",
        )
        self.assertFalse(decision.delivered)
        self.assertEqual(decision.status, "suppressed")

    def test_academic_deadline_allows_high_impact_zenith_regressions(self) -> None:
        policy = InterruptPolicy(base_threshold=0.72)
        high_impact = InterruptCandidate(
            candidate_id="cand_high",
            domain="zenith",
            reason="ci_failed_regression_on_release",
            urgency_score=0.94,
            confidence=0.9,
            state_refs=("risk:zenith:ci",),
        )
        decision = policy.evaluate(
            high_impact,
            suppression_windows=["academic_deadline_focus", "academic:class_session"],
            active_focus_domain="academics",
        )
        self.assertTrue(decision.delivered)
        self.assertEqual(decision.status, "delivered")

    def test_low_confidence_market_signal_is_suppressed_during_academics_focus(self) -> None:
        policy = InterruptPolicy(base_threshold=0.72)
        market_noise = InterruptCandidate(
            candidate_id="cand_market_noise",
            domain="markets",
            reason="short_lived_momentum_ping",
            urgency_score=0.78,
            confidence=0.74,
            state_refs=("risk:markets:opportunity:sig_1",),
        )
        decision = policy.evaluate(
            market_noise,
            suppression_windows=["academic_deadline_focus"],
            active_focus_domain="academics",
            goal_domain_weight=1.0,
            personal_context={"stress_level": 0.8, "energy_level": 0.45},
        )
        self.assertFalse(decision.delivered)
        self.assertEqual(decision.status, "suppressed")

    def test_high_confidence_market_signal_can_deliver_when_no_focus_lock(self) -> None:
        policy = InterruptPolicy(base_threshold=0.72)
        market_high = InterruptCandidate(
            candidate_id="cand_market_high",
            domain="markets",
            reason="high_confidence_low_downside_opportunity",
            urgency_score=0.95,
            confidence=0.93,
            state_refs=("risk:markets:opportunity:sig_2",),
        )
        decision = policy.evaluate(
            market_high,
            suppression_windows=[],
            active_focus_domain="off",
            goal_domain_weight=1.2,
            personal_context={"stress_level": 0.42, "energy_level": 0.71},
        )
        self.assertTrue(decision.delivered)
        self.assertEqual(decision.status, "delivered")


if __name__ == "__main__":
    unittest.main()
