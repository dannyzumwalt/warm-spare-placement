from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from warm_spare.models import AppConfig, ScenarioStats, ValidationResult, ValidationWarning


class ValidationError(ValueError):
    pass


REQUIRED_OFFICE_COLUMNS = ["office_id", "tier"]
OPTIONAL_OFFICE_COLUMNS = ["address", "name", "latitude", "longitude", "market"]


def load_and_validate_inputs(config: AppConfig) -> ValidationResult:
    offices = load_offices_frame(Path(config.paths.offices_csv), require_address=False)
    canonical_order = offices["office_id"].astype(str).tolist()
    candidate_order = offices.loc[offices["tier"].isin(config.candidate_tiers), "office_id"].astype(str).tolist()
    if not candidate_order:
        raise ValidationError("No candidate offices remain after applying candidate_tiers filter")

    original_weights = config.active_weights()
    normalized_weights, weight_warnings = _normalize_weights(original_weights)

    scenario_matrices: dict[str, pd.DataFrame] = {}
    directional_matrices: dict[str, dict[str, pd.DataFrame]] = {}
    scenario_stats: list[ScenarioStats] = []
    warnings = list(weight_warnings)

    scenarios_dir = Path(config.paths.scenarios_dir)
    for scenario_name in config.scenario_names:
        round_trip_path, otc_path, cto_path = _resolve_scenario_paths(scenarios_dir, scenario_name)
        matrix = _load_rectangular_matrix(
            matrix_path=round_trip_path,
            scenario_name=scenario_name,
            row_order=canonical_order,
            column_order=candidate_order,
        )
        office_to_candidate = _load_rectangular_matrix(
            matrix_path=otc_path,
            scenario_name=f"{scenario_name} office_to_candidate",
            row_order=canonical_order,
            column_order=candidate_order,
            required=False,
        )
        candidate_to_office = _load_rectangular_matrix(
            matrix_path=cto_path,
            scenario_name=f"{scenario_name} candidate_to_office",
            row_order=canonical_order,
            column_order=candidate_order,
            required=False,
        )
        scenario_matrices[scenario_name] = matrix
        if office_to_candidate is not None and candidate_to_office is not None:
            directional_matrices[scenario_name] = {
                "office_to_candidate": office_to_candidate,
                "candidate_to_office": candidate_to_office,
            }
            directional_gap = abs(office_to_candidate - candidate_to_office)
            mean_abs_directional_gap = float(directional_gap.to_numpy(dtype=float).mean())
        else:
            mean_abs_directional_gap = 0.0

        scenario_stats.append(
            ScenarioStats(
                scenario_name=scenario_name,
                minimum=float(matrix.to_numpy(dtype=float).min()),
                median=float(pd.Series(matrix.to_numpy(dtype=float).flatten()).median()),
                p95=float(pd.Series(matrix.to_numpy(dtype=float).flatten()).quantile(0.95)),
                maximum=float(matrix.to_numpy(dtype=float).max()),
                mean_abs_directional_gap=mean_abs_directional_gap,
                corrected_diagonal_entries=0,
            )
        )

    _validate_scenario_inventory(scenarios_dir=scenarios_dir, scenario_names=config.scenario_names)

    return ValidationResult(
        offices=offices,
        scenario_matrices=scenario_matrices,
        directional_matrices=directional_matrices,
        canonical_order=canonical_order,
        candidate_order=candidate_order,
        normalized_weights=normalized_weights,
        original_weights=original_weights,
        warnings=warnings,
        scenario_stats=scenario_stats,
    )


def load_offices_frame(path: Path, *, require_address: bool) -> pd.DataFrame:
    if not path.exists():
        raise ValidationError(f"Missing offices file: {path}")
    offices = pd.read_csv(path)
    missing = [column for column in REQUIRED_OFFICE_COLUMNS if column not in offices.columns]
    if missing:
        raise ValidationError(f"offices.csv missing required columns: {missing}")
    if require_address and "address" not in offices.columns:
        raise ValidationError("offices.csv must include address for matrix building")

    selected_columns = REQUIRED_OFFICE_COLUMNS + [
        column for column in OPTIONAL_OFFICE_COLUMNS if column in offices.columns
    ]
    offices = offices[selected_columns].copy()
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
    if require_address and offices["address"].isna().any():
        raise ValidationError("address column contains null values")
    return offices


def hash_inputs(config: AppConfig) -> dict[str, str]:
    hashes = {"offices_csv": file_sha256(Path(config.paths.offices_csv))}
    if config.paths.office_coordinates_csv:
        coordinates_path = Path(config.paths.office_coordinates_csv)
        if coordinates_path.exists():
            hashes["office_coordinates_csv"] = file_sha256(coordinates_path)
    scenarios_dir = Path(config.paths.scenarios_dir)
    for scenario_name in config.scenario_names:
        round_trip_path, otc_path, cto_path = _resolve_scenario_paths(scenarios_dir, scenario_name)
        for label, path in [
            (f"scenario::{scenario_name}::round_trip", round_trip_path),
            (f"scenario::{scenario_name}::office_to_candidate", otc_path),
            (f"scenario::{scenario_name}::candidate_to_office", cto_path),
        ]:
            if path.exists():
                hashes[label] = file_sha256(path)
    return hashes


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_scenario_paths(scenarios_dir: Path, scenario_name: str) -> tuple[Path, Path, Path]:
    default_round_trip = scenarios_dir / f"{scenario_name}__round_trip.csv"
    legacy_round_trip = scenarios_dir / f"{scenario_name}.csv"
    round_trip_path = default_round_trip if default_round_trip.exists() else legacy_round_trip
    office_to_candidate_path = scenarios_dir / f"{scenario_name}__office_to_candidate.csv"
    candidate_to_office_path = scenarios_dir / f"{scenario_name}__candidate_to_office.csv"
    return round_trip_path, office_to_candidate_path, candidate_to_office_path


def _load_rectangular_matrix(
    *,
    matrix_path: Path,
    scenario_name: str,
    row_order: list[str],
    column_order: list[str],
    required: bool = True,
) -> pd.DataFrame | None:
    if not matrix_path.exists():
        if required:
            raise ValidationError(f"Missing scenario file for '{scenario_name}': {matrix_path}")
        return None

    matrix = pd.read_csv(matrix_path, index_col=0)
    matrix.index = matrix.index.map(str)
    matrix.columns = matrix.columns.map(str)

    if matrix.empty:
        raise ValidationError(f"Scenario matrix '{scenario_name}' is empty")
    if matrix.index.duplicated().any():
        dupes = matrix.index[matrix.index.duplicated()].tolist()
        raise ValidationError(f"Scenario '{scenario_name}' has duplicate row labels: {dupes}")
    if matrix.columns.duplicated().any():
        dupes = matrix.columns[matrix.columns.duplicated()].tolist()
        raise ValidationError(f"Scenario '{scenario_name}' has duplicate column labels: {dupes}")

    row_missing = sorted(set(row_order) - set(matrix.index))
    row_extra = sorted(set(matrix.index) - set(row_order))
    col_missing = sorted(set(column_order) - set(matrix.columns))
    col_extra = sorted(set(matrix.columns) - set(column_order))
    if row_missing or row_extra or col_missing or col_extra:
        parts = []
        if row_missing:
            parts.append(f"missing row office IDs {row_missing}")
        if row_extra:
            parts.append(f"unexpected row office IDs {row_extra}")
        if col_missing:
            parts.append(f"missing candidate office IDs {col_missing}")
        if col_extra:
            parts.append(f"unexpected candidate office IDs {col_extra}")
        raise ValidationError(f"Scenario '{scenario_name}' label mismatch: {'; '.join(parts)}")

    matrix = matrix.loc[row_order, column_order]
    matrix = matrix.apply(pd.to_numeric, errors="coerce")
    if matrix.isna().any().any():
        raise ValidationError(f"Scenario '{scenario_name}' contains NaN or non-numeric values")
    if (matrix < 0).any().any():
        raise ValidationError(f"Scenario '{scenario_name}' contains negative travel times")
    return matrix


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
    missing = []
    for name in scenario_names:
        default_round_trip = scenarios_dir / f"{name}__round_trip.csv"
        legacy_round_trip = scenarios_dir / f"{name}.csv"
        if not default_round_trip.exists() and not legacy_round_trip.exists():
            missing.append(name)
    if missing:
        raise ValidationError(f"Missing configured scenario files: {missing}")
