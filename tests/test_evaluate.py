from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from warm_spare.config import load_config
from warm_spare.evaluate import evaluate_results
from warm_spare.io import load_and_validate_inputs
from warm_spare.models import OptimizationResult
from warm_spare.optimize import solve_all_k
from warm_spare.preprocess import preprocess_inputs
from tests.test_support import write_fixture_dataset


class EvaluateTests(unittest.TestCase):
    def test_metrics_include_expected_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(write_fixture_dataset(Path(tmp)))
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            results = solve_all_k(config, preprocess)
            metrics = evaluate_results(preprocess, results, config.sla_minutes)
            expected = {
                "tier1_worst_avg_drive",
                "overall_worst_case_drive",
                "site_overlap_with_prev_k",
                "offices_reassigned_from_prev_k",
                "monotonicity_anomaly_flag",
            }
            self.assertTrue(expected.issubset(set(metrics.columns)))

    def test_monotonicity_anomaly_flag_when_larger_k_has_no_solution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(write_fixture_dataset(Path(tmp)))
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            results = solve_all_k(config, preprocess)
            broken = OptimizationResult(
                k=3,
                solver_status="TIME_LIMIT_NO_INCUMBENT",
                solve_time_seconds=1.0,
                objective=None,
                selected_sites=[],
                assignments=None,
                assignment_map={},
                raw_solver_status="UNKNOWN",
                had_incumbent=False,
            )
            metrics = evaluate_results(preprocess, [results[0], results[1], broken], config.sla_minutes)
            flag = metrics.loc[metrics["k"] == 3, "monotonicity_anomaly_flag"].iloc[0]
            self.assertTrue(bool(flag))


if __name__ == "__main__":
    unittest.main()
