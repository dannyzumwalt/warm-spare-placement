from __future__ import annotations

import math

import pandas as pd

from warm_spare.models import RecommendationConfig, RecommendationResult, SpareInventoryConfig


FEASIBLE_STATUSES = {"OPTIMAL", "FEASIBLE", "TIME_LIMIT_WITH_INCUMBENT"}


def recommend_k(
    metrics: pd.DataFrame,
    config: RecommendationConfig,
    spare_inventory: SpareInventoryConfig | None = None,
) -> RecommendationResult:
    feasible = metrics.loc[metrics["solver_status"].isin(FEASIBLE_STATUSES)].sort_values("k").reset_index(drop=True)
    notes: list[str] = []
    if feasible.empty:
        return RecommendationResult(
            recommended_k=None,
            chosen_rule="no_feasible_solution",
            alternatives=[],
            notes=["No feasible solutions were available for recommendation."],
        )

    spare_inventory = spare_inventory or SpareInventoryConfig()
    if spare_inventory.candidate_site_counts:
        feasible = feasible.loc[feasible["k"].isin(spare_inventory.candidate_site_counts)].reset_index(drop=True)
        notes.append(
            "Recommendation review was limited to configured candidate site counts: "
            + ", ".join(f"k={value}" for value in spare_inventory.candidate_site_counts)
            + "."
        )
        if feasible.empty:
            return RecommendationResult(
                recommended_k=None,
                chosen_rule="no_feasible_solution_in_candidate_site_counts",
                alternatives=[],
                notes=notes + ["No feasible solutions were available inside the configured candidate site-count window."],
            )

    blocked_ks = _blocked_by_tier2_guardrail(feasible, float(config.tier2_guardrail_pct))
    screened = feasible.loc[~feasible["k"].isin(blocked_ks)].reset_index(drop=True)
    if screened.empty:
        screened = feasible.copy()

    admissible, inadmissible_reasons = _admissible_candidates(screened, config)
    for k in sorted(blocked_ks):
        notes.append(f"k={k} was removed from recommendation consideration by the Tier 2 local-anomaly guardrail.")
    for k, reasons in inadmissible_reasons.items():
        notes.append(f"k={k} is not defensible for recommendation because {', '.join(reasons)}.")

    if admissible.empty:
        fallback_k = _find_knee_candidate(screened)
        notes.append(
            "No tested site count passed the operational admissibility gates; falling back to the objective knee among feasible solutions."
        )
        return RecommendationResult(
            recommended_k=int(fallback_k),
            chosen_rule="objective_knee_fallback_no_admissible_solution",
            alternatives=_top_alternatives(screened, fallback_k),
            notes=notes,
        )

    current_row = admissible.iloc[0]
    current_k = int(current_row["k"])
    notes.append(f"k={current_k} is the first site count that passes the worst-case and load-concentration admissibility gates.")

    while True:
        next_row = _next_admissible_row(admissible, current_k)
        if next_row is None:
            break
        signals = _stepwise_signals(current_row, next_row, config)
        if len(signals) < int(config.min_signals_to_add_site):
            notes.append(
                f"Stopped at k={current_k} because moving to k={int(next_row['k'])} only produced {len(signals)} material signal(s): {', '.join(signals) if signals else 'none'}."
            )
            break
        notes.append(
            f"Advanced from k={current_k} to k={int(next_row['k'])} because the next site materially improved {', '.join(signals)}."
        )
        current_row = next_row
        current_k = int(current_row["k"])

    return RecommendationResult(
        recommended_k=current_k,
        chosen_rule="operational_floor_plus_stepwise_signals",
        alternatives=_top_alternatives(screened, current_k),
        notes=notes,
    )


def _blocked_by_tier2_guardrail(feasible: pd.DataFrame, threshold_pct: float) -> set[int]:
    blocked: set[int] = set()
    for idx in range(1, len(feasible) - 1):
        current = feasible.iloc[idx]
        previous = feasible.iloc[idx - 1]
        nxt = feasible.iloc[idx + 1]
        if _tier2_degrades_materially(
            current["tier2_avg_drive"], previous["tier2_avg_drive"], threshold_pct
        ) and _tier2_degrades_materially(current["tier2_avg_drive"], nxt["tier2_avg_drive"], threshold_pct):
            blocked.add(int(current["k"]))
    return blocked


def _tier2_degrades_materially(current: float, comparison_value: float, threshold_pct: float) -> bool:
    if any(math.isnan(float(value)) for value in [current, comparison_value]):
        return False
    if comparison_value >= current:
        return False
    reduction_pct = (float(current) - float(comparison_value)) / float(current) * 100.0
    return reduction_pct > threshold_pct


def _admissible_candidates(
    feasible: pd.DataFrame,
    config: RecommendationConfig,
) -> tuple[pd.DataFrame, dict[int, list[str]]]:
    admissible_rows: list[pd.Series] = []
    reasons_by_k: dict[int, list[str]] = {}
    for _, row in feasible.iterrows():
        reasons = _inadmissibility_reasons(row, config)
        if reasons:
            reasons_by_k[int(row["k"])] = reasons
        else:
            admissible_rows.append(row)
    if not admissible_rows:
        return feasible.iloc[0:0].copy(), reasons_by_k
    return pd.DataFrame(admissible_rows).reset_index(drop=True), reasons_by_k


def _inadmissibility_reasons(row: pd.Series, config: RecommendationConfig) -> list[str]:
    reasons: list[str] = []
    if _is_gt(row.get("overall_worst_case_drive"), config.max_defensible_worst_case_round_trip_minutes):
        reasons.append(
            "overall worst-case round trip exceeds "
            f"{config.max_defensible_worst_case_round_trip_minutes:.0f} minutes"
        )
    if _is_gt(row.get("max_load_share"), config.max_defensible_load_share):
        reasons.append(
            "maximum assigned load share exceeds "
            f"{config.max_defensible_load_share:.0%}"
        )
    if _is_gt(row.get("load_imbalance_ratio"), config.max_defensible_load_imbalance_ratio):
        reasons.append(
            "load imbalance ratio exceeds "
            f"{config.max_defensible_load_imbalance_ratio:.2f}x average load"
        )
    return reasons


def _next_admissible_row(admissible: pd.DataFrame, current_k: int) -> pd.Series | None:
    higher = admissible.loc[admissible["k"] > current_k].sort_values("k")
    if higher.empty:
        return None
    return higher.iloc[0]


def _stepwise_signals(
    current_row: pd.Series,
    next_row: pd.Series,
    config: RecommendationConfig,
) -> list[str]:
    signals: list[str] = []
    if _pct_drop(current_row.get("objective"), next_row.get("objective")) >= float(
        config.min_objective_step_improvement_pct
    ):
        signals.append("objective")
    if _pct_drop(
        current_row.get("overall_worst_case_drive"),
        next_row.get("overall_worst_case_drive"),
    ) >= float(config.min_worst_case_step_improvement_pct):
        signals.append("worst-case round trip")
    if _pct_drop(current_row.get("max_load_share"), next_row.get("max_load_share")) >= float(
        config.min_load_share_step_improvement_pct
    ):
        signals.append("load share")
    if _pct_drop(
        current_row.get("load_imbalance_ratio"),
        next_row.get("load_imbalance_ratio"),
    ) >= float(config.min_load_imbalance_step_improvement_pct):
        signals.append("load imbalance")
    return signals


def _find_knee_candidate(feasible: pd.DataFrame) -> int:
    improvements = feasible["objective_improvement_pct_from_prev_k"].fillna(0.0).tolist()
    if len(feasible) <= 2:
        return int(feasible.iloc[0]["k"])
    best_idx = 0
    best_drop = float("-inf")
    for idx in range(1, len(improvements) - 1):
        drop = improvements[idx] - improvements[idx + 1]
        if drop > best_drop:
            best_drop = drop
            best_idx = idx
    return int(feasible.iloc[best_idx]["k"])


def _top_alternatives(feasible: pd.DataFrame, selected_k: int) -> list[int]:
    ks = [int(value) for value in feasible["k"].tolist() if int(value) != int(selected_k)]
    return ks[:2]


def _pct_drop(current: object, next_value: object) -> float:
    try:
        current_float = float(current)
        next_float = float(next_value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(current_float) or math.isnan(next_float) or current_float <= 0:
        return 0.0
    return (current_float - next_float) / current_float * 100.0


def _is_gt(value: object, threshold: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if math.isnan(number):
        return False
    return number > float(threshold)
