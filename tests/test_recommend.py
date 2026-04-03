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


if __name__ == "__main__":
    unittest.main()
