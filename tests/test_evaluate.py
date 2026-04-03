from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from warm_spare.config import load_config
from warm_spare.evaluate import evaluate_results
from warm_spare.io import load_and_validate_inputs
from warm_spare.optimize import solve_all_k
from warm_spare.preprocess import preprocess_inputs
from tests.test_support import write_analysis_fixture


class EvaluateTests(unittest.TestCase):
    def test_metrics_include_round_trip_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path, _ = write_analysis_fixture(Path(tmp))
            config = load_config(config_path)
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            results = solve_all_k(config, preprocess)
            metrics = evaluate_results(preprocess, results, config.sla_minutes)
            expected = {"overall_worst_case_drive", "tier1_avg_drive", "site_overlap_with_prev_k"}
            self.assertTrue(expected.issubset(set(metrics.columns)))


if __name__ == "__main__":
    unittest.main()
