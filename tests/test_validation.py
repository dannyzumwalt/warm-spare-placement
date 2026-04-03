from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from warm_spare.config import load_config
from warm_spare.io import load_and_validate_inputs
from tests.test_support import write_analysis_fixture


class ValidationTests(unittest.TestCase):
    def test_rectangular_round_trip_matrices_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path, _ = write_analysis_fixture(Path(tmp))
            config = load_config(config_path)
            result = load_and_validate_inputs(config)
            self.assertEqual(result.canonical_order, ["A", "B", "C", "D"])
            self.assertEqual(result.candidate_order, ["A", "B", "C"])
            self.assertEqual(result.scenario_matrices["static_baseline"].shape, (4, 3))
            self.assertIn("office_to_candidate", result.directional_matrices["static_baseline"])


if __name__ == "__main__":
    unittest.main()
