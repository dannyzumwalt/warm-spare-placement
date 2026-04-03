from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from warm_spare.config import load_config
from warm_spare.io import load_and_validate_inputs
from warm_spare.preprocess import preprocess_inputs
from tests.test_support import write_analysis_fixture


class PreprocessTests(unittest.TestCase):
    def test_weighted_round_trip_outputs_and_feasibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path, _ = write_analysis_fixture(Path(tmp))
            config = load_config(config_path)
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            self.assertEqual(preprocess.d_avg.shape, (4, 3))
            self.assertEqual(preprocess.feasibility_mask.loc["D", "C"], 1)
            self.assertIsNotNone(preprocess.one_way_dmax)
            self.assertIn("min_one_way_dmax", preprocess.office_feasibility.columns)
            self.assertEqual(preprocess.candidate_order, ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
