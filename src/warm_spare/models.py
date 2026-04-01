from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(slots=True)
class PathsConfig:
    offices_csv: str
    scenarios_dir: str
    output_root: str


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
class AppConfig:
    paths: PathsConfig
    scenario_names: list[str]
    k_values: list[int]
    sla_minutes: float
    scenario_weight_profiles: dict[str, dict[str, float]]
    active_scenario_profile: str
    tier_weights: dict[int, float]
    solver: SolverConfig = field(default_factory=SolverConfig)
    recommendation: RecommendationConfig = field(default_factory=RecommendationConfig)
    artifacts: ArtifactConfig = field(default_factory=ArtifactConfig)

    def active_weights(self) -> dict[str, float]:
        return dict(self.scenario_weight_profiles[self.active_scenario_profile])

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
    mean_abs_symmetry_deviation: float
    corrected_diagonal_entries: int


@dataclass(slots=True)
class ValidationResult:
    offices: pd.DataFrame
    scenario_matrices: dict[str, pd.DataFrame]
    canonical_order: list[str]
    normalized_weights: dict[str, float]
    original_weights: dict[str, float]
    warnings: list[ValidationWarning]
    scenario_stats: list[ScenarioStats]


@dataclass(slots=True)
class PreprocessResult:
    offices: pd.DataFrame
    canonical_order: list[str]
    symmetrized_matrices: dict[str, pd.DataFrame]
    d_avg: pd.DataFrame
    d_max: pd.DataFrame
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
