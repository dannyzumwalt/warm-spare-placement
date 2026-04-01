from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

SCENARIOS = [
    "weekday_midnight",
    "weekday_6am",
    "weekday_noon",
    "weekday_6pm",
    "weekend_midnight",
    "weekend_6am",
    "weekend_noon",
    "weekend_6pm",
]


def write_fixture_dataset(base_dir: Path, *, globally_infeasible: bool = False) -> Path:
    data_dir = base_dir / "data" / "input"
    scenarios_dir = data_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    offices = pd.DataFrame(
        [
            {"office_id": "A", "name": "Alpha", "latitude": 33.75, "longitude": -84.39, "tier": 1},
            {"office_id": "B", "name": "Beta", "latitude": 33.76, "longitude": -84.38, "tier": 2},
            {"office_id": "C", "name": "Gamma", "latitude": 33.90, "longitude": -84.20, "tier": 3},
            {"office_id": "D", "name": "Delta", "latitude": 33.91, "longitude": -84.19, "tier": 4},
        ]
    )
    offices.to_csv(data_dir / "offices.csv", index=False)

    canonical = pd.DataFrame(
        [
            [0, 10, 90, 95],
            [12, 0, 88, 92],
            [90, 89, 0, 11],
            [94, 91, 10, 0],
        ],
        index=["A", "B", "C", "D"],
        columns=["A", "B", "C", "D"],
    )
    base = canonical.loc[["D", "B", "A", "C"], ["D", "B", "A", "C"]].copy()
    if globally_infeasible:
        base.loc["A", "D"] = 140
        base.loc["D", "A"] = 145
        base.loc["A", "C"] = 130
        base.loc["C", "A"] = 132
        base.loc["A", "B"] = 125
        base.loc["B", "A"] = 126

    for idx, scenario in enumerate(SCENARIOS):
        matrix = base.copy().astype(float)
        matrix.iloc[0, 1] += idx
        matrix.iloc[1, 0] += idx / 2
        matrix.iloc[2, 3] += idx
        matrix.iloc[3, 2] += idx / 2
        matrix.to_csv(scenarios_dir / f"{scenario}.csv")

    config = {
        "paths": {
            "offices_csv": str(data_dir / "offices.csv"),
            "scenarios_dir": str(scenarios_dir),
            "output_root": str(base_dir / "outputs"),
        },
        "scenario_names": SCENARIOS,
        "k_values": [1, 2, 3],
        "sla_minutes": 120,
        "scenario_weight_profiles": {
            "balanced_default": {scenario: 1 / len(SCENARIOS) for scenario in SCENARIOS},
            "tier1_heavy": {scenario: 1 / len(SCENARIOS) for scenario in SCENARIOS},
            "weekday_peak_heavy": {scenario: 1 / len(SCENARIOS) for scenario in SCENARIOS},
        },
        "active_scenario_profile": "balanced_default",
        "tier_weights": {1: 10, 2: 6, 3: 3, 4: 1},
        "solver": {
            "time_limit_seconds": 10,
            "random_seed": 7,
            "num_workers": 1,
            "objective_scale": 100,
        },
        "recommendation": {
            "plateau_threshold_pct": 5.0,
            "plateau_consecutive_steps": 2,
            "tier2_guardrail_pct": 2.0,
        },
        "artifacts": {
            "persist_preprocessed_csv": True,
            "generate_plots": False,
        },
    }
    config_path = base_dir / "config.yaml"
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
    return config_path
