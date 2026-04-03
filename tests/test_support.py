from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from warm_spare.models import DriveTimeElement, ScenarioDefinition

SCENARIOS = ["static_baseline", "realtime_now"]


def write_analysis_fixture(
    base_dir: Path,
    *,
    anomalous_realtime: bool = False,
    cache_db_path: str | None = None,
    accepted_anomaly_scenarios: list[str] | None = None,
) -> tuple[Path, Path]:
    data_dir = base_dir / "data" / "input"
    scenarios_dir = data_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    offices = pd.DataFrame(
        [
            {"office_id": "A", "address": "1 Main St, Alpha, ST", "tier": 1},
            {"office_id": "B", "address": "2 Main St, Beta, ST", "tier": 2},
            {"office_id": "C", "address": "3 Main St, Gamma, ST", "tier": 3},
            {"office_id": "D", "address": "4 Main St, Delta, ST", "tier": 4},
        ]
    )
    offices.to_csv(data_dir / "offices.csv", index=False)

    office_to_candidate_static = pd.DataFrame(
        [
            [0, 10, 90],
            [12, 0, 85],
            [95, 88, 0],
            [100, 95, 12],
        ],
        index=["A", "B", "C", "D"],
        columns=["A", "B", "C"],
    )
    candidate_to_office_static = pd.DataFrame(
        [
            [0, 12, 95],
            [10, 0, 88],
            [90, 85, 0],
            [105, 96, 13],
        ],
        index=["A", "B", "C", "D"],
        columns=["A", "B", "C"],
    )
    round_trip_static = office_to_candidate_static + candidate_to_office_static

    if anomalous_realtime:
        office_to_candidate_realtime = office_to_candidate_static + 90
        candidate_to_office_realtime = candidate_to_office_static + 95
    else:
        office_to_candidate_realtime = office_to_candidate_static + 3
        candidate_to_office_realtime = candidate_to_office_static + 4
    round_trip_realtime = office_to_candidate_realtime + candidate_to_office_realtime

    _write_scenario_files(scenarios_dir, "static_baseline", office_to_candidate_static, candidate_to_office_static, round_trip_static)
    _write_scenario_files(scenarios_dir, "realtime_now", office_to_candidate_realtime, candidate_to_office_realtime, round_trip_realtime)

    config = {
        "paths": {
            "offices_csv": str(data_dir / "offices.csv"),
            "scenarios_dir": str(scenarios_dir),
            "output_root": str(base_dir / "outputs"),
        },
        "scenario_names": SCENARIOS,
        "k_values": [1, 2, 3],
        "sla_minutes": 240,
        "candidate_tiers": [1, 2, 3],
        "scenario_weight_profiles": {
            "balanced_default": {"static_baseline": 0.5, "realtime_now": 0.5},
            "static_only": {"static_baseline": 1.0, "realtime_now": 0.0},
        },
        "active_scenario_profile": "balanced_default",
        "tier_weights": {1: 10, 2: 6, 3: 3, 4: 1},
        "solver": {"time_limit_seconds": 10, "random_seed": 7, "num_workers": 1, "objective_scale": 100},
        "recommendation": {"plateau_threshold_pct": 5.0, "plateau_consecutive_steps": 2, "tier2_guardrail_pct": 2.0},
        "artifacts": {"persist_preprocessed_csv": True, "generate_plots": False},
        "matrix_builder": {
            "provider": "google_distance_matrix",
            "api_key_env_var": "GOOGLE_MAPS_API_KEY",
            "cache_db_path": cache_db_path or str(base_dir / "matrix_cache.sqlite"),
            "eligible_spare_tiers": [1, 2, 3],
            "accepted_anomaly_scenarios": accepted_anomaly_scenarios or [],
            "retry_policy": {"max_attempts": 2, "initial_backoff_seconds": 0.0, "max_backoff_seconds": 0.0, "jitter_seconds": 0.0},
            "batch_limits": {"max_origins_per_request": 25, "max_destinations_per_request": 2, "max_elements_per_request": 100},
            "anomaly": {
                "pair_abs_minutes": 30.0,
                "pair_pct_of_static": 0.5,
                "pair_zscore_threshold": 3.0,
                "scenario_pair_fraction": 0.05,
                "scenario_tier12_pair_fraction": 0.02,
                "quarantine_by_default": True,
            },
            "scenarios": [
                {"id": "static_baseline", "departure_policy": "none", "traffic_model": None},
                {"id": "realtime_now", "departure_policy": "now", "traffic_model": "best_guess"},
            ],
        },
    }
    config_path = base_dir / "config.yaml"
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)

    market_path = base_dir / "market.yaml"
    with market_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {
                "market_id": "test-market",
                "label": "Test Market",
                "offices_csv": str(data_dir / "offices.csv"),
                "output_root": str(base_dir / "outputs"),
                "eligible_spare_tiers": [1, 2, 3],
            },
            handle,
            sort_keys=False,
        )
    return config_path, market_path


def _write_scenario_files(scenarios_dir: Path, scenario_id: str, office_to_candidate: pd.DataFrame, candidate_to_office: pd.DataFrame, round_trip: pd.DataFrame) -> None:
    office_to_candidate.to_csv(scenarios_dir / f"{scenario_id}__office_to_candidate.csv")
    candidate_to_office.to_csv(scenarios_dir / f"{scenario_id}__candidate_to_office.csv")
    round_trip.to_csv(scenarios_dir / f"{scenario_id}__round_trip.csv")


class FakeProvider:
    def __init__(self, responses: dict[tuple[str, str, str], float], *, fail_once: set[tuple[str, str, str]] | None = None) -> None:
        self.responses = responses
        self.fail_once = fail_once or set()
        self.calls: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
        self._failed: set[tuple[str, str, str]] = set()

    def fetch_matrix(
        self,
        origins: list[tuple[str, str]],
        destinations: list[tuple[str, str]],
        scenario: ScenarioDefinition,
    ) -> list[DriveTimeElement]:
        self.calls.append((scenario.id, scenario.departure_policy, tuple(origin_id for origin_id, _ in origins), tuple(destination_id for destination_id, _ in destinations)))
        results: list[DriveTimeElement] = []
        for origin_id, origin_address in origins:
            for destination_id, destination_address in destinations:
                key = (scenario.id, origin_id, destination_id)
                if key in self.fail_once and key not in self._failed:
                    self._failed.add(key)
                    raise RuntimeError(f"temporary failure for {key}")
                duration = self.responses.get(key)
                if duration is None:
                    results.append(
                        DriveTimeElement(
                            origin_id=origin_id,
                            destination_id=destination_id,
                            duration_minutes=float("nan"),
                            status="ZERO_RESULTS",
                            raw_duration_text=None,
                            normalized_origin=origin_address,
                            normalized_destination=destination_address,
                        )
                    )
                else:
                    results.append(
                        DriveTimeElement(
                            origin_id=origin_id,
                            destination_id=destination_id,
                            duration_minutes=float(duration),
                            status="OK",
                            raw_duration_text=f"{duration} min",
                            normalized_origin=origin_address,
                            normalized_destination=destination_address,
                        )
                    )
        return results


def build_provider_responses(*, anomalous_realtime: bool = False) -> dict[tuple[str, str, str], float]:
    responses: dict[tuple[str, str, str], float] = {}
    static_otc = {
        ("A", "A"): 0, ("A", "B"): 10, ("A", "C"): 90,
        ("B", "A"): 12, ("B", "B"): 0, ("B", "C"): 85,
        ("C", "A"): 95, ("C", "B"): 88, ("C", "C"): 0,
        ("D", "A"): 100, ("D", "B"): 95, ("D", "C"): 12,
    }
    static_cto = {
        ("A", "A"): 0, ("B", "A"): 12, ("C", "A"): 95,
        ("A", "B"): 10, ("B", "B"): 0, ("C", "B"): 88,
        ("A", "C"): 90, ("B", "C"): 85, ("C", "C"): 0,
        ("A", "D"): 105, ("B", "D"): 96, ("C", "D"): 13,
    }
    for origin_id in ["A", "B", "C", "D"]:
        for candidate_id in ["A", "B", "C"]:
            responses[("static_baseline", origin_id, candidate_id)] = static_otc[(origin_id, candidate_id)]
    for candidate_id in ["A", "B", "C"]:
        for office_id in ["A", "B", "C", "D"]:
            responses[("static_baseline", candidate_id, office_id)] = static_cto[(candidate_id, office_id)]

    realtime_delta = 180 if anomalous_realtime else 7
    for key, value in list(responses.items()):
        if key[0] == "static_baseline":
            responses[("realtime_now", key[1], key[2])] = value + realtime_delta
    return responses
