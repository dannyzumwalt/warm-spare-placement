from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from warm_spare.models import (
    AppConfig,
    ArtifactConfig,
    PathsConfig,
    RecommendationConfig,
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
    paths = PathsConfig(**paths_raw)

    solver = SolverConfig(**raw.get("solver", {}))
    recommendation = RecommendationConfig(**raw.get("recommendation", {}))
    artifacts = ArtifactConfig(**raw.get("artifacts", {}))

    tier_weights = {int(key): float(value) for key, value in raw["tier_weights"].items()}

    config = AppConfig(
        paths=paths,
        scenario_names=list(raw["scenario_names"]),
        k_values=[int(value) for value in raw["k_values"]],
        sla_minutes=float(raw["sla_minutes"]),
        scenario_weight_profiles={
            str(name): {str(k): float(v) for k, v in weights.items()}
            for name, weights in raw["scenario_weight_profiles"].items()
        },
        active_scenario_profile=str(raw["active_scenario_profile"]),
        tier_weights=tier_weights,
        solver=solver,
        recommendation=recommendation,
        artifacts=artifacts,
    )
    validate_config(config)
    return config


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
    if sorted(config.k_values) != config.k_values:
        raise ConfigError("k_values must be sorted ascending")
    if any(tier not in {1, 2, 3, 4} for tier in config.tier_weights):
        raise ConfigError("tier_weights keys must be 1, 2, 3, or 4")

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
