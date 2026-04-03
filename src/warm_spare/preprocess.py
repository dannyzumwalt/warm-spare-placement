from __future__ import annotations

import pandas as pd

from warm_spare.models import AppConfig, PreprocessResult, ValidationResult


def preprocess_inputs(config: AppConfig, validation: ValidationResult) -> PreprocessResult:
    d_avg = _weighted_average(
        validation.scenario_matrices,
        validation.normalized_weights,
        validation.canonical_order,
        validation.candidate_order,
    )
    d_max = _elementwise_max(
        validation.scenario_matrices,
        validation.canonical_order,
        validation.candidate_order,
    )
    feasibility_mask = (d_max <= config.sla_minutes).astype(int)

    office_feasibility = _build_office_feasibility(
        offices=validation.offices,
        d_avg=d_avg,
        d_max=d_max,
        feasibility_mask=feasibility_mask,
    )

    return PreprocessResult(
        offices=validation.offices.copy(),
        canonical_order=list(validation.canonical_order),
        candidate_order=list(validation.candidate_order),
        directional_matrices=dict(validation.directional_matrices),
        d_avg=d_avg,
        d_max=d_max,
        feasibility_mask=feasibility_mask,
        office_feasibility=office_feasibility,
        normalized_weights=dict(validation.normalized_weights),
        original_weights=dict(validation.original_weights),
        warnings=list(validation.warnings),
        scenario_stats=list(validation.scenario_stats),
    )


def enforce_global_feasibility(preprocess: PreprocessResult) -> list[str]:
    infeasible = preprocess.office_feasibility.loc[
        preprocess.office_feasibility["feasible_candidate_count"] == 0, "office_id"
    ].tolist()
    return infeasible


def _weighted_average(
    matrices: dict[str, pd.DataFrame],
    weights: dict[str, float],
    canonical_order: list[str],
    candidate_order: list[str],
) -> pd.DataFrame:
    result = pd.DataFrame(0.0, index=canonical_order, columns=candidate_order)
    for scenario_name, matrix in matrices.items():
        result = result + matrix * float(weights[scenario_name])
    return result


def _elementwise_max(
    matrices: dict[str, pd.DataFrame],
    canonical_order: list[str],
    candidate_order: list[str],
) -> pd.DataFrame:
    stacked = pd.concat(
        [matrix.stack().rename(name) for name, matrix in matrices.items()], axis=1
    )
    max_series = stacked.max(axis=1)
    return max_series.unstack().loc[canonical_order, candidate_order]


def _build_office_feasibility(
    *,
    offices: pd.DataFrame,
    d_avg: pd.DataFrame,
    d_max: pd.DataFrame,
    feasibility_mask: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    tier_lookup = offices.set_index("office_id")["tier"].to_dict()
    for office_id in d_avg.index:
        feasible_targets = feasibility_mask.columns[feasibility_mask.loc[office_id] == 1].tolist()
        rows.append(
            {
                "office_id": office_id,
                "tier": int(tier_lookup[office_id]),
                "feasible_candidate_count": int(len(feasible_targets)),
                "min_dmax": float(d_max.loc[office_id].min()),
                "min_davg": float(d_avg.loc[office_id].min()),
            }
        )
    return pd.DataFrame(rows)
