from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from warm_spare.models import AppConfig, ScenarioStats, ValidationResult, ValidationWarning


class ValidationError(ValueError):
    pass


REQUIRED_OFFICE_COLUMNS = ["office_id", "name", "latitude", "longitude", "tier"]


def load_and_validate_inputs(config: AppConfig) -> ValidationResult:
    offices = _load_offices(Path(config.paths.offices_csv))
    canonical_order = offices["office_id"].astype(str).tolist()

    original_weights = config.active_weights()
    normalized_weights, weight_warnings = _normalize_weights(original_weights)

    scenario_matrices: dict[str, pd.DataFrame] = {}
    scenario_stats: list[ScenarioStats] = []
    warnings = list(weight_warnings)

    scenarios_dir = Path(config.paths.scenarios_dir)
    for scenario_name in config.scenario_names:
        matrix_path = scenarios_dir / f"{scenario_name}.csv"
        matrix, stats, diagonal_warning = _load_scenario_matrix(
            matrix_path=matrix_path,
            scenario_name=scenario_name,
            canonical_order=canonical_order,
        )
        scenario_matrices[scenario_name] = matrix
        scenario_stats.append(stats)
        if diagonal_warning is not None:
            warnings.append(diagonal_warning)

    _validate_scenario_inventory(scenarios_dir=scenarios_dir, scenario_names=config.scenario_names)

    return ValidationResult(
        offices=offices,
        scenario_matrices=scenario_matrices,
        canonical_order=canonical_order,
        normalized_weights=normalized_weights,
        original_weights=original_weights,
        warnings=warnings,
        scenario_stats=scenario_stats,
    )


def hash_inputs(config: AppConfig) -> dict[str, str]:
    hashes = {"offices_csv": file_sha256(Path(config.paths.offices_csv))}
    for scenario_name in config.scenario_names:
        path = Path(config.paths.scenarios_dir) / f"{scenario_name}.csv"
        hashes[f"scenario::{scenario_name}"] = file_sha256(path)
    return hashes


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_offices(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise ValidationError(f"Missing offices file: {path}")
    offices = pd.read_csv(path)
    missing = [column for column in REQUIRED_OFFICE_COLUMNS if column not in offices.columns]
    if missing:
        raise ValidationError(f"offices.csv missing required columns: {missing}")
    offices = offices[REQUIRED_OFFICE_COLUMNS].copy()
    offices["office_id"] = offices["office_id"].astype(str)
    if offices["office_id"].duplicated().any():
        dupes = offices.loc[offices["office_id"].duplicated(), "office_id"].tolist()
        raise ValidationError(f"Duplicate office_id values found: {dupes}")
    if offices["tier"].isna().any():
        raise ValidationError("tier column contains null values")
    offices["tier"] = offices["tier"].astype(int)
    invalid_tiers = sorted(set(offices.loc[~offices["tier"].isin([1, 2, 3, 4]), "tier"].tolist()))
    if invalid_tiers:
        raise ValidationError(f"Invalid tier values found: {invalid_tiers}")
    if offices[["latitude", "longitude"]].isna().any().any():
        raise ValidationError("latitude/longitude columns contain null values")
    return offices


def _load_scenario_matrix(
    *,
    matrix_path: Path,
    scenario_name: str,
    canonical_order: list[str],
) -> tuple[pd.DataFrame, ScenarioStats, ValidationWarning | None]:
    if not matrix_path.exists():
        raise ValidationError(f"Missing scenario file for '{scenario_name}': {matrix_path}")

    matrix = pd.read_csv(matrix_path, index_col=0)
    matrix.index = matrix.index.map(str)
    matrix.columns = matrix.columns.map(str)

    if matrix.empty:
        raise ValidationError(f"Scenario matrix '{scenario_name}' is empty")
    if matrix.shape[0] != matrix.shape[1]:
        raise ValidationError(
            f"Scenario matrix '{scenario_name}' is not square: {matrix.shape}"
        )
    if matrix.index.duplicated().any():
        dupes = matrix.index[matrix.index.duplicated()].tolist()
        raise ValidationError(f"Scenario '{scenario_name}' has duplicate row labels: {dupes}")
    if matrix.columns.duplicated().any():
        dupes = matrix.columns[matrix.columns.duplicated()].tolist()
        raise ValidationError(f"Scenario '{scenario_name}' has duplicate column labels: {dupes}")
    if list(matrix.index) != list(matrix.columns):
        raise ValidationError(
            f"Scenario '{scenario_name}' row labels do not match column labels"
        )

    expected = canonical_order
    actual = list(matrix.index)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing office IDs {missing}")
        if extra:
            parts.append(f"unexpected office IDs {extra}")
        raise ValidationError(f"Scenario '{scenario_name}' label mismatch: {'; '.join(parts)}")

    matrix = matrix.loc[expected, expected]
    matrix = matrix.apply(pd.to_numeric, errors="coerce")
    if matrix.isna().any().any():
        raise ValidationError(f"Scenario '{scenario_name}' contains NaN or non-numeric values")
    if (matrix < 0).any().any():
        raise ValidationError(f"Scenario '{scenario_name}' contains negative travel times")

    diagonal_series = pd.Series(matrix.values.diagonal())
    corrected_diagonal_entries = int((diagonal_series != 0).sum())
    if corrected_diagonal_entries:
        for idx in range(len(matrix)):
            matrix.iat[idx, idx] = 0.0
        diagonal_warning = ValidationWarning(
            category="diagonal_correction",
            message=(
                f"Scenario '{scenario_name}' had {corrected_diagonal_entries} non-zero diagonal "
                "entries corrected to 0"
            ),
        )
    else:
        diagonal_warning = None

    raw_values = matrix.to_numpy(dtype=float)
    asym = abs(raw_values - raw_values.T)
    stats = ScenarioStats(
        scenario_name=scenario_name,
        minimum=float(raw_values.min()),
        median=float(pd.Series(raw_values.flatten()).median()),
        p95=float(pd.Series(raw_values.flatten()).quantile(0.95)),
        maximum=float(raw_values.max()),
        mean_abs_symmetry_deviation=float(asym.mean()),
        corrected_diagonal_entries=corrected_diagonal_entries,
    )
    return matrix, stats, diagonal_warning


def _normalize_weights(weights: dict[str, float]) -> tuple[dict[str, float], list[ValidationWarning]]:
    warnings: list[ValidationWarning] = []
    if any(value < 0 for value in weights.values()):
        raise ValidationError("Scenario weights cannot be negative")
    total = float(sum(weights.values()))
    if abs(total - 1.0) <= 1e-9:
        return dict(weights), warnings
    if total <= 0:
        raise ValidationError("Scenario weights must sum to a positive value")
    normalized = {name: value / total for name, value in weights.items()}
    warnings.append(
        ValidationWarning(
            category="weight_normalization",
            message=(
                f"Scenario weights summed to {total:.6f}; normalized automatically to sum to 1.0"
            ),
        )
    )
    return normalized, warnings


def _validate_scenario_inventory(*, scenarios_dir: Path, scenario_names: list[str]) -> None:
    expected_files = {f"{name}.csv" for name in scenario_names}
    actual_files = {path.name for path in scenarios_dir.glob("*.csv")}
    missing = sorted(expected_files - actual_files)
    extra = sorted(actual_files - expected_files)
    if missing:
        raise ValidationError(f"Missing configured scenario files: {missing}")
    if extra:
        raise ValidationError(f"Unexpected scenario files present: {extra}")
