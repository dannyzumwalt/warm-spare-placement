from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd


@dataclass(slots=True)
class PathsConfig:
    offices_csv: str
    scenarios_dir: str
    output_root: str
    office_coordinates_csv: str | None = None


@dataclass(slots=True)
class SolverConfig:
    time_limit_seconds: int = 30
    random_seed: int = 42
    num_workers: int = 8
    objective_scale: int = 100


@dataclass(slots=True)
class RecommendationConfig:
    plateau_threshold_pct: float = 5.0
    plateau_consecutive_steps: int = 2
    tier2_guardrail_pct: float = 2.0


@dataclass(slots=True)
class ArtifactConfig:
    persist_preprocessed_csv: bool = True
    generate_plots: bool = True


@dataclass(slots=True)
class RetryPolicyConfig:
    max_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    jitter_seconds: float = 0.5


@dataclass(slots=True)
class BatchLimitsConfig:
    max_origins_per_request: int = 25
    max_destinations_per_request: int = 25
    max_elements_per_request: int = 100


@dataclass(slots=True)
class AnomalyConfig:
    pair_abs_minutes: float = 30.0
    pair_pct_of_static: float = 0.5
    pair_zscore_threshold: float = 3.0
    scenario_pair_fraction: float = 0.05
    scenario_tier12_pair_fraction: float = 0.02
    quarantine_by_default: bool = True


@dataclass(slots=True)
class ScenarioDefinition:
    id: str
    departure_policy: Literal["none", "now"] = "none"
    traffic_model: str | None = None
    mode: str = "driving"


@dataclass(slots=True)
class MatrixBuilderConfig:
    provider: str = "google_distance_matrix"
    api_key_env_var: str = "GOOGLE_MAPS_API_KEY"
    cache_db_path: str = "outputs/matrix_cache.sqlite"
    geocode_cache_db_path: str = "outputs/geocode_cache.sqlite"
    eligible_spare_tiers: list[int] = field(default_factory=lambda: [1, 2, 3])
    accepted_anomaly_scenarios: list[str] = field(default_factory=list)
    retry_policy: RetryPolicyConfig = field(default_factory=RetryPolicyConfig)
    batch_limits: BatchLimitsConfig = field(default_factory=BatchLimitsConfig)
    anomaly: AnomalyConfig = field(default_factory=AnomalyConfig)
    scenarios: list[ScenarioDefinition] = field(default_factory=list)


@dataclass(slots=True)
class AppConfig:
    paths: PathsConfig
    scenario_names: list[str]
    k_values: list[int]
    sla_minutes: float
    scenario_weight_profiles: dict[str, dict[str, float]]
    active_scenario_profile: str
    tier_weights: dict[int, float]
    round_trip_sla_minutes: float | None = None
    candidate_tiers: list[int] = field(default_factory=lambda: [1, 2, 3])
    solver: SolverConfig = field(default_factory=SolverConfig)
    recommendation: RecommendationConfig = field(default_factory=RecommendationConfig)
    artifacts: ArtifactConfig = field(default_factory=ArtifactConfig)
    matrix_builder: MatrixBuilderConfig | None = None

    def active_weights(self) -> dict[str, float]:
        return dict(self.scenario_weight_profiles[self.active_scenario_profile])

    def effective_round_trip_sla_minutes(self) -> float:
        if self.round_trip_sla_minutes is not None:
            return float(self.round_trip_sla_minutes)
        return float(self.sla_minutes) * 2.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MarketConfig:
    market_id: str
    label: str
    offices_csv: str
    output_root: str
    eligible_spare_tiers: list[int] = field(default_factory=lambda: [1, 2, 3])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ValidationWarning:
    category: str
    message: str


@dataclass(slots=True)
class ScenarioStats:
    scenario_name: str
    minimum: float
    median: float
    p95: float
    maximum: float
    mean_abs_directional_gap: float
    corrected_diagonal_entries: int


@dataclass(slots=True)
class ValidationResult:
    offices: pd.DataFrame
    scenario_matrices: dict[str, pd.DataFrame]
    directional_matrices: dict[str, dict[str, pd.DataFrame]]
    canonical_order: list[str]
    candidate_order: list[str]
    normalized_weights: dict[str, float]
    original_weights: dict[str, float]
    warnings: list[ValidationWarning]
    scenario_stats: list[ScenarioStats]


@dataclass(slots=True)
class PreprocessResult:
    offices: pd.DataFrame
    canonical_order: list[str]
    candidate_order: list[str]
    directional_matrices: dict[str, dict[str, pd.DataFrame]]
    d_avg: pd.DataFrame
    d_max: pd.DataFrame
    one_way_dmax: pd.DataFrame | None
    feasibility_mask: pd.DataFrame
    office_feasibility: pd.DataFrame
    normalized_weights: dict[str, float]
    original_weights: dict[str, float]
    warnings: list[ValidationWarning]
    scenario_stats: list[ScenarioStats]


@dataclass(slots=True)
class OptimizationResult:
    k: int
    solver_status: str
    solve_time_seconds: float
    objective: float | None
    selected_sites: list[str]
    assignments: pd.DataFrame | None
    assignment_map: dict[str, str]
    raw_solver_status: str
    had_incumbent: bool


@dataclass(slots=True)
class RecommendationResult:
    recommended_k: int | None
    chosen_rule: str
    alternatives: list[int]
    notes: list[str]


@dataclass(slots=True)
class SpareSiteMapDetail:
    spare_site: str
    address: str | None
    site_name: str | None
    total_offices: int
    tier_counts: dict[int, int]
    map_path: str


@dataclass(slots=True)
class RunMetadata:
    timestamp_utc: str
    python_version: str
    package_versions: dict[str, str]
    active_scenario_profile: str
    input_hashes: dict[str, str]
    git_commit_hash: str | None = None

    @classmethod
    def create(
        cls,
        *,
        python_version: str,
        package_versions: dict[str, str],
        active_scenario_profile: str,
        input_hashes: dict[str, str],
        git_commit_hash: str | None,
    ) -> "RunMetadata":
        return cls(
            timestamp_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            python_version=python_version,
            package_versions=package_versions,
            active_scenario_profile=active_scenario_profile,
            input_hashes=input_hashes,
            git_commit_hash=git_commit_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PipelineArtifacts:
    config: AppConfig
    validation: ValidationResult
    preprocess: PreprocessResult
    optimization_results: list[OptimizationResult]
    metrics: pd.DataFrame
    recommendation: RecommendationResult
    output_dir: Path


@dataclass(slots=True)
class DriveTimeElement:
    origin_id: str
    destination_id: str
    duration_minutes: float
    status: str
    raw_duration_text: str | None
    normalized_origin: str | None
    normalized_destination: str | None


@dataclass(slots=True)
class MatrixBuildResult:
    output_dir: Path
    analysis_config_path: Path
    office_coordinates_path: Path | None
    unresolved_pairs_path: Path | None
    quarantined_pairs_path: Path | None
    quarantine_manifest_path: Path | None
    build_report_path: Path
    build_manifest_path: Path
    quarantined_scenarios: list[str]
    unresolved_pair_count: int
    success: bool
