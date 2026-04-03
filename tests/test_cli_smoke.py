from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_support import write_analysis_fixture


class CliSmokeTests(unittest.TestCase):
    def test_run_command_works_with_rectangular_round_trip_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path, _ = write_analysis_fixture(tmp_path)
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
            self.assertTrue((output_dir / "metrics_by_k.csv").exists())
            self.assertTrue((output_dir / "recommendation.md").exists())


if __name__ == "__main__":
    unittest.main()
