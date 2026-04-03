from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from warm_spare.config import load_config, resolve_market_config
from tests.test_support import write_analysis_fixture


class MarketConfigTests(unittest.TestCase):
    def test_market_file_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, market_path = write_analysis_fixture(Path(tmp))
            market = resolve_market_config(None, str(market_path))
            self.assertEqual(market.market_id, "test-market")
            self.assertEqual(market.eligible_spare_tiers, [1, 2, 3])

    def test_load_config_supports_spare_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path, _ = write_analysis_fixture(Path(tmp))
            config_payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
            config_payload["k_values"] = [1, 2, 3, 4, 5]
            config_payload["spare_inventory"] = {
                "total_cabinets": 8,
                "candidate_site_counts": [3, 4, 5],
                "preferred_cabinet_distribution": {
                    3: [3, 3, 2],
                    4: [2, 2, 2, 2],
                    5: [2, 2, 2, 1, 1],
                },
                "min_cabinets_per_site": 1,
                "max_cabinets_per_site": 3,
                "preferred_tier2_site_count": 2,
            }
            Path(config_path).write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

            config = load_config(config_path)

            self.assertEqual(config.spare_inventory.total_cabinets, 8)
            self.assertEqual(config.spare_inventory.candidate_site_counts, [3, 4, 5])
            self.assertEqual(config.spare_inventory.preferred_cabinet_distribution[4], [2, 2, 2, 2])
            self.assertEqual(config.spare_inventory.preferred_tier2_site_count, 2)


if __name__ == "__main__":
    unittest.main()
