from __future__ import annotations

import unittest

import pandas as pd

from warm_spare.models import RecommendationConfig
from warm_spare.recommend import recommend_k


class RecommendTests(unittest.TestCase):
    def test_first_defensible_k_is_selected_when_next_step_is_not_justified(self) -> None:
        metrics = pd.DataFrame(
            [
                {"k": 1, "solver_status": "OPTIMAL", "objective": 25300.0, "objective_improvement_pct_from_prev_k": None, "tier1_avg_drive": 30.0, "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 88.0, "overall_worst_case_drive": 227.0, "max_load_share": 1.0, "load_imbalance_ratio": 1.0},
                {"k": 2, "solver_status": "OPTIMAL", "objective": 22300.0, "objective_improvement_pct_from_prev_k": 11.8, "tier1_avg_drive": 30.0, "tier1_improvement_pct_from_prev_k": 0.0, "tier2_avg_drive": 88.0, "overall_worst_case_drive": 226.95, "max_load_share": 0.91, "load_imbalance_ratio": 1.82},
                {"k": 3, "solver_status": "OPTIMAL", "objective": 20020.0, "objective_improvement_pct_from_prev_k": 10.2, "tier1_avg_drive": 30.0, "tier1_improvement_pct_from_prev_k": 0.0, "tier2_avg_drive": 88.0, "overall_worst_case_drive": 151.0, "max_load_share": 0.64, "load_imbalance_ratio": 1.92},
                {"k": 4, "solver_status": "OPTIMAL", "objective": 18320.0, "objective_improvement_pct_from_prev_k": 8.5, "tier1_avg_drive": 30.0, "tier1_improvement_pct_from_prev_k": 0.0, "tier2_avg_drive": 87.0, "overall_worst_case_drive": 143.0, "max_load_share": 0.60, "load_imbalance_ratio": 1.85},
            ]
        )
        config = RecommendationConfig(
            min_objective_step_improvement_pct=9.0,
            min_worst_case_step_improvement_pct=10.0,
            min_load_share_step_improvement_pct=10.0,
            min_load_imbalance_step_improvement_pct=10.0,
            min_signals_to_add_site=2,
        )
        result = recommend_k(metrics, config)
        self.assertEqual(result.recommended_k, 3)
        self.assertEqual(result.chosen_rule, "operational_floor_plus_stepwise_signals")
        self.assertTrue(any("first site count that passes" in note for note in result.notes))

    def test_recommendation_advances_when_next_site_improves_two_signals(self) -> None:
        metrics = pd.DataFrame(
            [
                {"k": 1, "solver_status": "OPTIMAL", "objective": 25300.0, "objective_improvement_pct_from_prev_k": None, "tier1_avg_drive": 30.0, "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 88.0, "overall_worst_case_drive": 227.0, "max_load_share": 1.0, "load_imbalance_ratio": 1.0},
                {"k": 2, "solver_status": "OPTIMAL", "objective": 22300.0, "objective_improvement_pct_from_prev_k": 11.8, "tier1_avg_drive": 30.0, "tier1_improvement_pct_from_prev_k": 0.0, "tier2_avg_drive": 88.0, "overall_worst_case_drive": 226.95, "max_load_share": 0.91, "load_imbalance_ratio": 1.82},
                {"k": 3, "solver_status": "OPTIMAL", "objective": 20020.0, "objective_improvement_pct_from_prev_k": 10.2, "tier1_avg_drive": 30.0, "tier1_improvement_pct_from_prev_k": 0.0, "tier2_avg_drive": 88.0, "overall_worst_case_drive": 151.0, "max_load_share": 0.68, "load_imbalance_ratio": 2.10},
                {"k": 4, "solver_status": "OPTIMAL", "objective": 18320.0, "objective_improvement_pct_from_prev_k": 8.5, "tier1_avg_drive": 30.0, "tier1_improvement_pct_from_prev_k": 0.0, "tier2_avg_drive": 87.0, "overall_worst_case_drive": 143.0, "max_load_share": 0.55, "load_imbalance_ratio": 1.55},
                {"k": 5, "solver_status": "OPTIMAL", "objective": 16950.0, "objective_improvement_pct_from_prev_k": 7.5, "tier1_avg_drive": 29.0, "tier1_improvement_pct_from_prev_k": 3.0, "tier2_avg_drive": 86.5, "overall_worst_case_drive": 142.0, "max_load_share": 0.52, "load_imbalance_ratio": 1.45},
            ]
        )
        result = recommend_k(metrics, RecommendationConfig())
        self.assertEqual(result.recommended_k, 4)
        self.assertTrue(any("Advanced from k=3 to k=4" in note for note in result.notes))

    def test_local_tier2_spike_is_still_blocked(self) -> None:
        metrics = pd.DataFrame(
            [
                {"k": 3, "solver_status": "OPTIMAL", "objective": 100.0, "objective_improvement_pct_from_prev_k": None, "tier1_avg_drive": 50.0, "tier1_improvement_pct_from_prev_k": None, "tier2_avg_drive": 20.0, "overall_worst_case_drive": 170.0, "max_load_share": 0.60, "load_imbalance_ratio": 1.50},
                {"k": 4, "solver_status": "OPTIMAL", "objective": 90.0, "objective_improvement_pct_from_prev_k": 10.0, "tier1_avg_drive": 47.0, "tier1_improvement_pct_from_prev_k": 6.0, "tier2_avg_drive": 30.0, "overall_worst_case_drive": 160.0, "max_load_share": 0.52, "load_imbalance_ratio": 1.40},
                {"k": 5, "solver_status": "OPTIMAL", "objective": 82.0, "objective_improvement_pct_from_prev_k": 8.0, "tier1_avg_drive": 44.0, "tier1_improvement_pct_from_prev_k": 6.0, "tier2_avg_drive": 20.0, "overall_worst_case_drive": 150.0, "max_load_share": 0.45, "load_imbalance_ratio": 1.20},
                {"k": 6, "solver_status": "OPTIMAL", "objective": 75.0, "objective_improvement_pct_from_prev_k": 8.5, "tier1_avg_drive": 41.0, "tier1_improvement_pct_from_prev_k": 6.8, "tier2_avg_drive": 19.5, "overall_worst_case_drive": 149.0, "max_load_share": 0.44, "load_imbalance_ratio": 1.18},
            ]
        )
        result = recommend_k(metrics, RecommendationConfig())
        self.assertTrue(any("Tier 2 local-anomaly guardrail" in note for note in result.notes))
        self.assertNotEqual(result.recommended_k, 4)


if __name__ == "__main__":
    unittest.main()
