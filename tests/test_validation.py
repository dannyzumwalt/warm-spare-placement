from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from warm_spare.config import load_config
from warm_spare.io import ValidationError, load_and_validate_inputs
from tests.test_support import write_fixture_dataset


class ValidationTests(unittest.TestCase):
    def test_reorders_to_canonical_office_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(write_fixture_dataset(Path(tmp)))
            result = load_and_validate_inputs(config)
            self.assertEqual(result.canonical_order, ["A", "B", "C", "D"])
            first = result.scenario_matrices[config.scenario_names[0]]
            self.assertEqual(first.index.tolist(), ["A", "B", "C", "D"])
            self.assertEqual(first.columns.tolist(), ["A", "B", "C", "D"])

    def test_duplicate_office_id_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = write_fixture_dataset(Path(tmp))
            offices_path = Path(tmp) / "data" / "input" / "offices.csv"
            offices = pd.read_csv(offices_path)
            offices.loc[1, "office_id"] = "A"
            offices.to_csv(offices_path, index=False)
            config = load_config(config_path)
            with self.assertRaises(ValidationError):
                load_and_validate_inputs(config)

    def test_negative_weight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = write_fixture_dataset(Path(tmp))
            raw = Path(config_path).read_text(encoding="utf-8")
            raw = raw.replace("weekday_midnight: 0.125", "weekday_midnight: -0.125", 1)
            Path(config_path).write_text(raw, encoding="utf-8")
            config = load_config(config_path)
            with self.assertRaises(ValidationError):
                load_and_validate_inputs(config)

    def test_nonzero_diagonal_is_corrected_and_warned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(write_fixture_dataset(Path(tmp)))
            matrix_path = Path(tmp) / "data" / "input" / "scenarios" / f"{config.scenario_names[0]}.csv"
            matrix = pd.read_csv(matrix_path, index_col=0)
            matrix.iloc[0, 0] = 5
            matrix.to_csv(matrix_path)
            result = load_and_validate_inputs(config)
            warning_messages = [warning.message for warning in result.warnings]
            self.assertTrue(any("non-zero diagonal" in message for message in warning_messages))
            self.assertEqual(result.scenario_matrices[config.scenario_names[0]].iloc[0, 0], 0)


if __name__ == "__main__":
    unittest.main()
