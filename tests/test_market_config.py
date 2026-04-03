from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from warm_spare.config import resolve_market_config
from tests.test_support import write_analysis_fixture


class MarketConfigTests(unittest.TestCase):
    def test_market_file_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, market_path = write_analysis_fixture(Path(tmp))
            market = resolve_market_config(None, str(market_path))
            self.assertEqual(market.market_id, "test-market")
            self.assertEqual(market.eligible_spare_tiers, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
