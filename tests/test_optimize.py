from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ortools.sat.python import cp_model

from warm_spare.config import load_config
from warm_spare.evaluate import evaluate_results
from warm_spare.io import load_and_validate_inputs
from warm_spare.optimize import _map_solver_status, solve_all_k
from warm_spare.preprocess import enforce_global_feasibility, preprocess_inputs
from tests.test_support import write_fixture_dataset


class OptimizeTests(unittest.TestCase):
    def test_global_infeasibility_detected_before_solver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(write_fixture_dataset(Path(tmp)))
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            preprocess.feasibility_mask.loc["A"] = 0
            preprocess.office_feasibility.loc[
                preprocess.office_feasibility["office_id"] == "A", "feasible_candidate_count"
            ] = 0
            infeasible = enforce_global_feasibility(preprocess)
            self.assertIn("A", infeasible)

    def test_solver_finds_known_two_cluster_solution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(write_fixture_dataset(Path(tmp)))
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            results = solve_all_k(config, preprocess)
            by_k = {result.k: result for result in results}
            self.assertEqual(by_k[2].solver_status, "OPTIMAL")
            self.assertEqual(set(by_k[2].selected_sites), {"A", "C"})

    def test_objective_is_nonincreasing_across_feasible_k(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(write_fixture_dataset(Path(tmp)))
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            results = solve_all_k(config, preprocess)
            metrics = evaluate_results(preprocess, results, config.sla_minutes)
            objectives = metrics["objective"].dropna().tolist()
            self.assertEqual(objectives, sorted(objectives, reverse=True))

    def test_status_mapping_distinguishes_unknown_cases(self) -> None:
        self.assertEqual(_map_solver_status(cp_model.OPTIMAL, True), "OPTIMAL")
        self.assertEqual(_map_solver_status(cp_model.FEASIBLE, True), "FEASIBLE")
        self.assertEqual(_map_solver_status(cp_model.UNKNOWN, True), "TIME_LIMIT_WITH_INCUMBENT")
        self.assertEqual(_map_solver_status(cp_model.UNKNOWN, False), "TIME_LIMIT_NO_INCUMBENT")


if __name__ == "__main__":
    unittest.main()
