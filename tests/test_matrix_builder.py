from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from warm_spare.config import load_config, resolve_market_config
from warm_spare.matrix_builder import build_matrix_dataset
from tests.test_support import FakeProvider, build_provider_responses, write_analysis_fixture


class MatrixBuilderTests(unittest.TestCase):
    def test_build_matrix_writes_directional_and_round_trip_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path, market_path = write_analysis_fixture(tmp_path)
            config = load_config(config_path)
            market = resolve_market_config(None, str(market_path))
            provider = FakeProvider(build_provider_responses())
            result = build_matrix_dataset(config, market, provider=provider)
            self.assertTrue(result.success)
            self.assertTrue((result.output_dir / "scenarios" / "static_baseline__office_to_candidate.csv").exists())
            self.assertTrue((result.output_dir / "scenarios" / "static_baseline__candidate_to_office.csv").exists())
            self.assertTrue((result.output_dir / "scenarios" / "static_baseline__round_trip.csv").exists())
            analysis_config = Path(result.analysis_config_path).read_text(encoding="utf-8")
            self.assertIn("scenario_names", analysis_config)
            self.assertTrue(any(call[1] == "now" for call in provider.calls))
            self.assertTrue(any(call[1] == "none" for call in provider.calls))

    def test_build_matrix_quarantines_anomalous_realtime_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path, market_path = write_analysis_fixture(tmp_path, anomalous_realtime=True)
            config = load_config(config_path)
            market = resolve_market_config(None, str(market_path))
            provider = FakeProvider(build_provider_responses(anomalous_realtime=True))
            result = build_matrix_dataset(config, market, provider=provider)
            self.assertIn("realtime_now", result.quarantined_scenarios)
            self.assertIsNotNone(result.quarantined_pairs_path)
            self.assertTrue(Path(result.quarantined_pairs_path).exists())
            analysis_config = Path(result.analysis_config_path).read_text(encoding="utf-8")
            self.assertNotIn("realtime_now: 0.5", analysis_config)

    def test_build_matrix_resumes_after_transient_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path, market_path = write_analysis_fixture(tmp_path)
            config = load_config(config_path)
            market = resolve_market_config(None, str(market_path))
            provider = FakeProvider(build_provider_responses(), fail_once={("static_baseline", "A", "B")})
            result = build_matrix_dataset(config, market, provider=provider)
            self.assertTrue(result.success)
            round_trip = pd.read_csv(result.output_dir / "scenarios" / "static_baseline__round_trip.csv", index_col=0)
            self.assertGreater(round_trip.loc["A", "B"], 0)

    def test_build_matrix_reuses_shared_cache_across_separate_build_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path, market_path = write_analysis_fixture(tmp_path, cache_db_path="matrix_cache.sqlite")
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                config = load_config(config_path)
                market = resolve_market_config(None, str(market_path))
                first_provider = FakeProvider(build_provider_responses())
                first_result = build_matrix_dataset(config, market, provider=first_provider)
                self.assertTrue(first_result.success)

                second_provider = FakeProvider({})
                second_result = build_matrix_dataset(config, market, provider=second_provider)
                self.assertTrue(second_result.success)
                self.assertNotEqual(first_result.output_dir, second_result.output_dir)
                self.assertEqual(second_provider.calls, [])
            finally:
                os.chdir(previous_cwd)

    def test_build_matrix_can_accept_quarantined_scenario_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path, market_path = write_analysis_fixture(tmp_path, anomalous_realtime=True)
            config = load_config(config_path)
            market = resolve_market_config(None, str(market_path))
            provider = FakeProvider(build_provider_responses(anomalous_realtime=True))
            result = build_matrix_dataset(
                config,
                market,
                provider=provider,
                accept_quarantined_scenarios={"realtime_now"},
            )
            self.assertEqual(result.quarantined_scenarios, [])
            analysis_config = Path(result.analysis_config_path).read_text(encoding="utf-8")
            self.assertIn("realtime_now: 0.5", analysis_config)
            build_report = Path(result.build_report_path).read_text(encoding="utf-8")
            self.assertIn("Accepted anomaly", build_report)

    def test_build_matrix_resolution_run_refreshes_only_quarantined_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path, market_path = write_analysis_fixture(
                tmp_path,
                anomalous_realtime=True,
                cache_db_path="matrix_cache.sqlite",
            )
            previous_cwd = Path.cwd()
            os.chdir(tmp_path)
            try:
                config = load_config(config_path)
                market = resolve_market_config(None, str(market_path))
                first_provider = FakeProvider(build_provider_responses(anomalous_realtime=True))
                first_result = build_matrix_dataset(config, market, provider=first_provider)
                self.assertIn("realtime_now", first_result.quarantined_scenarios)

                second_provider = FakeProvider(build_provider_responses(anomalous_realtime=False))
                second_result = build_matrix_dataset(
                    config,
                    market,
                    provider=second_provider,
                    resolve_quarantine_from=first_result.output_dir,
                )
                self.assertTrue(second_result.success)
                self.assertEqual(second_result.quarantined_scenarios, [])
                self.assertTrue(second_provider.calls)
                self.assertTrue(all(call[0] == "realtime_now" for call in second_provider.calls))
                round_trip = pd.read_csv(
                    second_result.output_dir / "scenarios" / "realtime_now__round_trip.csv",
                    index_col=0,
                )
                self.assertEqual(round_trip.loc["A", "B"], 36.0)
            finally:
                os.chdir(previous_cwd)

    def test_build_report_distinguishes_anomaly_classes_and_request_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path, market_path = write_analysis_fixture(tmp_path, anomalous_realtime=True)
            config = load_config(config_path)
            market = resolve_market_config(None, str(market_path))
            responses = build_provider_responses(anomalous_realtime=True)
            responses.pop(("static_baseline", "A", "B"))
            provider = FakeProvider(responses)
            result = build_matrix_dataset(config, market, provider=provider)
            self.assertFalse(result.success)
            build_report = Path(result.build_report_path).read_text(encoding="utf-8")
            self.assertIn("Broad scenario anomaly", build_report)
            self.assertIn("Request/API failure", build_report)
            self.assertIn("Quarantine Targets", build_report)


if __name__ == "__main__":
    unittest.main()
