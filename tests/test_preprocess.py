from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from warm_spare.config import load_config
from warm_spare.io import load_and_validate_inputs
from warm_spare.preprocess import preprocess_inputs
from warm_spare.reporting import create_output_dir, write_preprocess_artifacts
from tests.test_support import write_fixture_dataset


class PreprocessTests(unittest.TestCase):
    def test_symmetrization_and_weighted_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(write_fixture_dataset(Path(tmp)))
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            self.assertAlmostEqual(preprocess.symmetrized_matrices[config.scenario_names[0]].loc["A", "B"], 11.0)
            self.assertEqual(preprocess.d_avg.index.tolist(), ["A", "B", "C", "D"])
            self.assertEqual(preprocess.d_max.index.tolist(), ["A", "B", "C", "D"])
            self.assertTrue(((preprocess.feasibility_mask == 0) | (preprocess.feasibility_mask == 1)).all().all())

    def test_persists_preprocess_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = load_config(write_fixture_dataset(tmp_path))
            validation = load_and_validate_inputs(config)
            preprocess = preprocess_inputs(config, validation)
            output_dir = create_output_dir(str(tmp_path / "outputs"), "preprocess_test")
            write_preprocess_artifacts(output_dir, preprocess)
            self.assertTrue((output_dir / "d_avg.csv").exists())
            self.assertTrue((output_dir / "d_max.csv").exists())
            self.assertTrue((output_dir / "feasibility_mask.csv").exists())


if __name__ == "__main__":
    unittest.main()
