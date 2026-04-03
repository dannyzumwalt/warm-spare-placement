from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from warm_spare.config import load_config
from warm_spare.io import load_and_validate_inputs, load_offices_frame
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

    def test_load_offices_frame_normalizes_whitespace_padded_headers_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "offices.csv"
            csv_path.write_text(
                "office_id, address, tier\n"
                " ATLNGANW , 3450 RIVERWOOD PKWY SE ATLANTA GA , 1 \n"
                " ATLNGATL , 51 PEACHTREE CENTER AVE NE ATLANTA GA , 2 \n",
                encoding="utf-8",
            )

            offices = load_offices_frame(csv_path, require_address=True)

            self.assertEqual(offices.columns.tolist(), ["office_id", "tier", "address"])
            self.assertEqual(offices["office_id"].tolist(), ["ATLNGANW", "ATLNGATL"])
            self.assertEqual(
                offices["address"].tolist(),
                [
                    "3450 RIVERWOOD PKWY SE ATLANTA GA",
                    "51 PEACHTREE CENTER AVE NE ATLANTA GA",
                ],
            )
            self.assertEqual(offices["tier"].tolist(), [1, 2])


if __name__ == "__main__":
    unittest.main()
