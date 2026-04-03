from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from warm_spare.config import load_config
from warm_spare.io import load_and_validate_inputs
from warm_spare.optimize import solve_all_k
from warm_spare.preprocess import preprocess_inputs
from tests.test_support import write_analysis_fixture


class OptimizeTests(unittest.TestCase):
    def test_optimizer_never_selects_tier4_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path, _ = write_analysis_fixture(Path(tmp))
            config = load_config(config_path)
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            results = solve_all_k(config, preprocess)
            for result in results:
                self.assertTrue(set(result.selected_sites).issubset({"A", "B", "C"}))


if __name__ == "__main__":
    unittest.main()
