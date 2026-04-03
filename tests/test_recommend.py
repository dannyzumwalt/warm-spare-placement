from __future__ import annotations

import unittest

import pandas as pd

from warm_spare.models import RecommendationConfig
from warm_spare.recommend import recommend_k


class RecommendTests(unittest.TestCase):
    def test_plateau_rule_selects_expected_k(self) -> None:
        metrics = pd.DataFrame(
            [
                {"k": 3, "solver_status": "OPTIMAL", "objective": 100, "objective_improvement_pct_from_prev_k": None, "tier1_avg_drive": 50, "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 45},
                {"k": 4, "solver_status": "OPTIMAL", "objective": 80, "objective_improvement_pct_from_prev_k": 20.0, "tier1_avg_drive": 40, "tier1_improvement_pct_from_prev_k": 20.0, "tier2_avg_drive": 40},
                {"k": 5, "solver_status": "OPTIMAL", "objective": 77, "objective_improvement_pct_from_prev_k": 3.75, "tier1_avg_drive": 38.5, "tier1_improvement_pct_from_prev_k": 3.75, "tier2_avg_drive": 39.5},
                {"k": 6, "solver_status": "OPTIMAL", "objective": 75, "objective_improvement_pct_from_prev_k": 2.60, "tier1_avg_drive": 37.8, "tier1_improvement_pct_from_prev_k": 1.82, "tier2_avg_drive": 39.0},
            ]
        )
        result = recommend_k(metrics, RecommendationConfig())
        self.assertEqual(result.recommended_k, 4)

    def test_knee_rule_does_not_block_when_tier2_is_flat_then_improves(self) -> None:
        metrics = pd.DataFrame(
            [
                {"k": 1, "solver_status": "OPTIMAL", "objective": 23058.21, "objective_improvement_pct_from_prev_k": None, "tier1_avg_drive": float("nan"), "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 29.69},
                {"k": 2, "solver_status": "OPTIMAL", "objective": 20032.42, "objective_improvement_pct_from_prev_k": 13.12, "tier1_avg_drive": float("nan"), "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 29.69},
                {"k": 3, "solver_status": "OPTIMAL", "objective": 17537.12, "objective_improvement_pct_from_prev_k": 12.46, "tier1_avg_drive": float("nan"), "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 29.69},
                {"k": 4, "solver_status": "OPTIMAL", "objective": 15372.10, "objective_improvement_pct_from_prev_k": 12.35, "tier1_avg_drive": float("nan"), "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 29.69},
                {"k": 5, "solver_status": "OPTIMAL", "objective": 14158.07, "objective_improvement_pct_from_prev_k": 7.90, "tier1_avg_drive": float("nan"), "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 17.91},
            ]
        )
        result = recommend_k(metrics, RecommendationConfig())
        self.assertEqual(result.recommended_k, 4)
        self.assertFalse(any("removed these k values" in note for note in result.notes))

    def test_local_tier2_spike_is_still_blocked(self) -> None:
        metrics = pd.DataFrame(
            [
                {"k": 3, "solver_status": "OPTIMAL", "objective": 100.0, "objective_improvement_pct_from_prev_k": None, "tier1_avg_drive": 50.0, "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 20.0},
                {"k": 4, "solver_status": "OPTIMAL", "objective": 90.0, "objective_improvement_pct_from_prev_k": 10.0, "tier1_avg_drive": 47.0, "tier1_improvement_pct_from_prev_k": 6.0, "tier2_avg_drive": 30.0},
                {"k": 5, "solver_status": "OPTIMAL", "objective": 82.0, "objective_improvement_pct_from_prev_k": 8.0, "tier1_avg_drive": 44.0, "tier1_improvement_pct_from_prev_k": 6.0, "tier2_avg_drive": 20.0},
                {"k": 6, "solver_status": "OPTIMAL", "objective": 75.0, "objective_improvement_pct_from_prev_k": 8.5, "tier1_avg_drive": 41.0, "tier1_improvement_pct_from_prev_k": 6.8, "tier2_avg_drive": 19.5},
            ]
        )
        result = recommend_k(metrics, RecommendationConfig())
        self.assertIn("Tier 2 local-anomaly guardrail removed these k values from consideration: 4", result.notes)
        self.assertNotEqual(result.recommended_k, 4)


if __name__ == "__main__":
    unittest.main()
