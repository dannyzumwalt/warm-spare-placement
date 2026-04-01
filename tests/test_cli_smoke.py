from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_support import write_fixture_dataset


class CliSmokeTests(unittest.TestCase):
    def test_run_command_produces_expected_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = write_fixture_dataset(tmp_path)
            env = os.environ.copy()
            src_path = Path(__file__).resolve().parents[1] / "src"
            env["PYTHONPATH"] = str(src_path) + os.pathsep + env.get("PYTHONPATH", "")
            completed = subprocess.run(
                [sys.executable, "-m", "warm_spare.cli", "run", "--config", str(config_path)],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, msg=completed.stderr)
            output_dir = Path(completed.stdout.strip().splitlines()[-1])
            expected = [
                "resolved_config.yaml",
                "run_metadata.json",
                "validation_report.md",
                "office_feasibility.csv",
                "d_avg.csv",
                "d_max.csv",
                "feasibility_mask.csv",
                "metrics_by_k.csv",
                "selected_sites_by_k.csv",
                "recommendation.md",
            ]
            for name in expected:
                self.assertTrue((output_dir / name).exists(), msg=name)


if __name__ == "__main__":
    unittest.main()
