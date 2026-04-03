from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from warm_spare.models import (
    AnomalyConfig,
    AppConfig,
    ArtifactConfig,
    BatchLimitsConfig,
    MarketConfig,
    MatrixBuilderConfig,
    PathsConfig,
    RecommendationConfig,
    RetryPolicyConfig,
    ScenarioDefinition,
    SolverConfig,
)


class ConfigError(ValueError):
    pass


def _require_keys(payload: dict[str, Any], keys: list[str], context: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ConfigError(f"Missing config keys in {context}: {', '.join(missing)}")


def load_config(config_path: str | Path) -> AppConfig:
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    _require_keys(
        raw,
        [
            "paths",
            "scenario_names",
            "k_values",
            "sla_minutes",
            "scenario_weight_profiles",
            "active_scenario_profile",
            "tier_weights",
        ],
        "root",
    )

    paths_raw = raw["paths"]
    _require_keys(paths_raw, ["offices_csv", "scenarios_dir", "output_root"], "paths")
    paths = PathsConfig(
        offices_csv=str(paths_raw["offices_csv"]),
        scenarios_dir=str(paths_raw["scenarios_dir"]),
        output_root=str(paths_raw["output_root"]),
        office_coordinates_csv=(
            str(paths_raw["office_coordinates_csv"])
            if paths_raw.get("office_coordinates_csv") is not None
            else None
        ),
    )

    solver = SolverConfig(**raw.get("solver", {}))
    recommendation = RecommendationConfig(**raw.get("recommendation", {}))
    artifacts = ArtifactConfig(**raw.get("artifacts", {}))
    candidate_tiers = [int(value) for value in raw.get("candidate_tiers", [1, 2, 3])]
    tier_weights = {int(key): float(value) for key, value in raw["tier_weights"].items()}

    matrix_builder = _load_matrix_builder(raw.get("matrix_builder"))
    if matrix_builder is not None and "candidate_tiers" not in raw:
        candidate_tiers = list(matrix_builder.eligible_spare_tiers)

    config = AppConfig(
        paths=paths,
        scenario_names=[str(value) for value in raw["scenario_names"]],
        k_values=[int(value) for value in raw["k_values"]],
        sla_minutes=float(raw["sla_minutes"]),
        round_trip_sla_minutes=(
            float(raw["round_trip_sla_minutes"])
            if "round_trip_sla_minutes" in raw and raw["round_trip_sla_minutes"] is not None
            else None
        ),
        scenario_weight_profiles={
            str(name): {str(k): float(v) for k, v in weights.items()}
            for name, weights in raw["scenario_weight_profiles"].items()
        },
        active_scenario_profile=str(raw["active_scenario_profile"]),
        tier_weights=tier_weights,
        candidate_tiers=candidate_tiers,
        solver=solver,
        recommendation=recommendation,
        artifacts=artifacts,
        matrix_builder=matrix_builder,
        market_id=str(raw["market_id"]) if raw.get("market_id") is not None else None,
        market_label=str(raw["market_label"]) if raw.get("market_label") is not None else None,
    )
    validate_config(config)
    return config


def load_market_config(market_path: str | Path) -> MarketConfig:
    path = Path(market_path)
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ConfigError("Market config root must be a mapping")
    _require_keys(raw, ["market_id", "label", "offices_csv", "output_root"], "market")
    eligible_spare_tiers = [int(value) for value in raw.get("eligible_spare_tiers", [1, 2, 3])]
    market = MarketConfig(
        market_id=str(raw["market_id"]),
        label=str(raw["label"]),
        offices_csv=str(raw["offices_csv"]),
        output_root=str(raw["output_root"]),
        eligible_spare_tiers=eligible_spare_tiers,
    )
    validate_market_config(market)
    return market


def resolve_market_config(market: str | None, market_file: str | None) -> MarketConfig:
    if bool(market) == bool(market_file):
        raise ConfigError("Specify exactly one of --market or --market-file for build-matrix")
    if market_file:
        return load_market_config(market_file)
    market_path = Path("config") / "markets" / f"{market}.yaml"
    if not market_path.exists():
        raise ConfigError(f"Market alias '{market}' did not resolve to {market_path}")
    return load_market_config(market_path)


def validate_config(config: AppConfig) -> None:
    if config.active_scenario_profile not in config.scenario_weight_profiles:
        raise ConfigError(
            f"Active scenario profile '{config.active_scenario_profile}' is not defined"
        )
    if not config.scenario_names:
        raise ConfigError("scenario_names cannot be empty")
    if not config.k_values:
        raise ConfigError("k_values cannot be empty")
    if any(k <= 0 for k in config.k_values):
        raise ConfigError("k_values must be positive integers")
    if config.sla_minutes <= 0:
        raise ConfigError("sla_minutes must be positive")
    if config.round_trip_sla_minutes is not None and config.round_trip_sla_minutes <= 0:
        raise ConfigError("round_trip_sla_minutes must be positive when provided")
    if sorted(config.k_values) != config.k_values:
        raise ConfigError("k_values must be sorted ascending")
    if any(tier not in {1, 2, 3, 4} for tier in config.tier_weights):
        raise ConfigError("tier_weights keys must be 1, 2, 3, or 4")
    if any(tier not in {1, 2, 3, 4} for tier in config.candidate_tiers):
        raise ConfigError("candidate_tiers values must be 1, 2, 3, or 4")
    if config.recommendation.max_defensible_worst_case_round_trip_minutes <= 0:
        raise ConfigError("max_defensible_worst_case_round_trip_minutes must be positive")
    if not 0 < config.recommendation.max_defensible_load_share <= 1:
        raise ConfigError("max_defensible_load_share must be in (0, 1]")
    if config.recommendation.max_defensible_load_imbalance_ratio <= 0:
        raise ConfigError("max_defensible_load_imbalance_ratio must be positive")
    if config.recommendation.min_signals_to_add_site <= 0:
        raise ConfigError("min_signals_to_add_site must be positive")

    active_weights = config.active_weights()
    missing = [name for name in config.scenario_names if name not in active_weights]
    extra = [name for name in active_weights if name not in config.scenario_names]
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing weights for {missing}")
        if extra:
            details.append(f"unexpected weights for {extra}")
        raise ConfigError("Active scenario profile mismatch: " + "; ".join(details))

    if config.matrix_builder is not None:
        scenario_ids = [scenario.id for scenario in config.matrix_builder.scenarios]
        if not scenario_ids:
            raise ConfigError("matrix_builder.scenarios cannot be empty")
        if scenario_ids and sorted(scenario_ids) != sorted(config.scenario_names):
            raise ConfigError("matrix_builder.scenarios ids must match scenario_names")


def validate_market_config(market: MarketConfig) -> None:
    if any(tier not in {1, 2, 3, 4} for tier in market.eligible_spare_tiers):
        raise ConfigError("market eligible_spare_tiers values must be 1, 2, 3, or 4")


def _load_matrix_builder(raw: dict[str, Any] | None) -> MatrixBuilderConfig | None:
    if raw is None:
        return None
    retry_policy = RetryPolicyConfig(**raw.get("retry_policy", {}))
    batch_limits = BatchLimitsConfig(**raw.get("batch_limits", {}))
    anomaly = AnomalyConfig(**raw.get("anomaly", {}))
    scenarios = [ScenarioDefinition(**item) for item in raw.get("scenarios", [])]
    return MatrixBuilderConfig(
        provider=str(raw.get("provider", "google_distance_matrix")),
        api_key_env_var=str(raw.get("api_key_env_var", "GOOGLE_MAPS_API_KEY")),
        cache_db_path=str(raw.get("cache_db_path", "outputs/matrix_cache.sqlite")),
        geocode_cache_db_path=str(raw.get("geocode_cache_db_path", "outputs/geocode_cache.sqlite")),
        eligible_spare_tiers=[int(value) for value in raw.get("eligible_spare_tiers", [1, 2, 3])],
        accepted_anomaly_scenarios=[str(value) for value in raw.get("accepted_anomaly_scenarios", [])],
        retry_policy=retry_policy,
        batch_limits=batch_limits,
        anomaly=anomaly,
        scenarios=scenarios,
    )
