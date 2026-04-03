from __future__ import annotations

import math

import pandas as pd

from warm_spare.models import OptimizationResult, PreprocessResult
from warm_spare.optimize import SOLVED_WITH_ASSIGNMENTS


def evaluate_results(
    preprocess: PreprocessResult,
    optimization_results: list[OptimizationResult],
    one_way_sla_minutes: float,
    round_trip_sla_minutes: float,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    previous_feasible_result: OptimizationResult | None = None
    seen_feasible_k: int | None = None

    for result in optimization_results:
        row = _evaluate_single(
            preprocess,
            result,
            previous_feasible_result,
            seen_feasible_k,
            one_way_sla_minutes,
            round_trip_sla_minutes,
        )
        rows.append(row)
        if result.solver_status in SOLVED_WITH_ASSIGNMENTS and result.assignments is not None:
            previous_feasible_result = result
            if seen_feasible_k is None:
                seen_feasible_k = result.k
    metrics = pd.DataFrame(rows).sort_values("k").reset_index(drop=True)
    metrics["objective_improvement_pct_from_prev_k"] = _improvement_pct(metrics["objective"])
    metrics["tier1_improvement_pct_from_prev_k"] = _improvement_pct(metrics["tier1_avg_drive"])
    metrics["tier2_improvement_pct_from_prev_k"] = _improvement_pct(metrics["tier2_avg_drive"])
    metrics["overall_worst_case_improvement_pct_from_prev_k"] = _improvement_pct(
        metrics["overall_worst_case_drive"]
    )
    metrics["max_load_share_improvement_pct_from_prev_k"] = _improvement_pct(metrics["max_load_share"])
    metrics["load_imbalance_improvement_pct_from_prev_k"] = _improvement_pct(
        metrics["load_imbalance_ratio"]
    )
    return metrics


def _evaluate_single(
    preprocess: PreprocessResult,
    result: OptimizationResult,
    previous_feasible_result: OptimizationResult | None,
    seen_feasible_k: int | None,
    one_way_sla_minutes: float,
    round_trip_sla_minutes: float,
) -> dict[str, object]:
    if result.assignments is None or result.solver_status not in SOLVED_WITH_ASSIGNMENTS:
        return {
            "k": result.k,
            "solver_status": result.solver_status,
            "solve_time_seconds": result.solve_time_seconds,
            "objective": math.nan,
            "tier1_avg_drive": math.nan,
            "tier2_avg_drive": math.nan,
            "tier3_avg_drive": math.nan,
            "tier4_avg_drive": math.nan,
            "tier1_worst_avg_drive": math.nan,
            "tier2_worst_avg_drive": math.nan,
            "overall_worst_avg_drive": math.nan,
            "overall_worst_case_drive": math.nan,
            "max_assigned_dmax": math.nan,
            "sla_violations": math.nan,
            "selected_site_count": 0,
            "avg_load_per_spare": math.nan,
            "max_load_per_spare": math.nan,
            "max_load_share": math.nan,
            "load_imbalance_ratio": math.nan,
            "site_overlap_with_prev_k": math.nan,
            "offices_reassigned_from_prev_k": math.nan,
            "monotonicity_anomaly_flag": bool(seen_feasible_k is not None),
        }

    assignments = result.assignments.copy()
    loads = assignments.groupby("assigned_spare").size()
    total_offices = float(len(assignments))
    avg_load = float(loads.mean())
    max_load = float(loads.max())
    avg_by_tier = assignments.groupby("tier")["avg_drive_minutes"].mean().to_dict()
    worst_avg_by_tier = assignments.groupby("tier")["avg_drive_minutes"].max().to_dict()
    round_trip_violations = assignments["worst_case_drive_minutes"] > float(round_trip_sla_minutes)
    if "worst_case_one_way_drive_minutes" in assignments.columns:
        one_way_violations = assignments["worst_case_one_way_drive_minutes"] > float(one_way_sla_minutes)
    else:
        one_way_violations = False
    sla_violations = int((round_trip_violations | one_way_violations).sum())

    site_overlap = math.nan
    office_reassigned = math.nan
    if previous_feasible_result is not None and previous_feasible_result.assignments is not None:
        site_overlap = float(len(set(result.selected_sites) & set(previous_feasible_result.selected_sites)))
        previous_map = previous_feasible_result.assignment_map
        office_reassigned = float(
            sum(
                1
                for office_id, spare_id in result.assignment_map.items()
                if previous_map.get(office_id) != spare_id
            )
        )

    return {
        "k": result.k,
        "solver_status": result.solver_status,
        "solve_time_seconds": result.solve_time_seconds,
        "objective": float(result.objective if result.objective is not None else math.nan),
        "tier1_avg_drive": float(avg_by_tier.get(1, math.nan)),
        "tier2_avg_drive": float(avg_by_tier.get(2, math.nan)),
        "tier3_avg_drive": float(avg_by_tier.get(3, math.nan)),
        "tier4_avg_drive": float(avg_by_tier.get(4, math.nan)),
        "tier1_worst_avg_drive": float(worst_avg_by_tier.get(1, math.nan)),
        "tier2_worst_avg_drive": float(worst_avg_by_tier.get(2, math.nan)),
        "overall_worst_avg_drive": float(assignments["avg_drive_minutes"].max()),
        "overall_worst_case_drive": float(assignments["worst_case_drive_minutes"].max()),
        "max_assigned_dmax": float(assignments["worst_case_drive_minutes"].max()),
        "sla_violations": sla_violations,
        "selected_site_count": int(len(result.selected_sites)),
        "avg_load_per_spare": avg_load,
        "max_load_per_spare": max_load,
        "max_load_share": (max_load / total_offices) if total_offices > 0 else math.nan,
        "load_imbalance_ratio": (max_load / avg_load) if avg_load > 0 else math.nan,
        "site_overlap_with_prev_k": site_overlap,
        "offices_reassigned_from_prev_k": office_reassigned,
        "monotonicity_anomaly_flag": False,
    }


def _improvement_pct(series: pd.Series) -> pd.Series:
    prev = series.shift(1)
    improvement = (prev - series) / prev * 100.0
    improvement[(prev <= 0) | prev.isna() | series.isna()] = math.nan
    return improvement
