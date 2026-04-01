from __future__ import annotations

import math

import pandas as pd

from warm_spare.models import RecommendationConfig, RecommendationResult


FEASIBLE_STATUSES = {"OPTIMAL", "FEASIBLE", "TIME_LIMIT_WITH_INCUMBENT"}


def recommend_k(metrics: pd.DataFrame, config: RecommendationConfig) -> RecommendationResult:
    feasible = metrics.loc[metrics["solver_status"].isin(FEASIBLE_STATUSES)].sort_values("k").reset_index(drop=True)
    notes: list[str] = []
    if feasible.empty:
        return RecommendationResult(
            recommended_k=None,
            chosen_rule="no_feasible_solution",
            alternatives=[],
            notes=["No feasible solutions were available for recommendation."],
        )

    blocked_ks = _blocked_by_tier2_guardrail(feasible, float(config.tier2_guardrail_pct))
    plateau_candidate = _find_plateau_candidate(feasible, config, blocked_ks)
    if plateau_candidate is not None:
        alternatives = _top_alternatives(feasible, plateau_candidate)
        return RecommendationResult(
            recommended_k=int(plateau_candidate),
            chosen_rule="plateau_with_tier2_guardrail",
            alternatives=alternatives,
            notes=notes,
        )

    knee_candidate = _find_knee_candidate(feasible, blocked_ks)
    alternatives = _top_alternatives(feasible, knee_candidate)
    if blocked_ks:
        notes.append(
            "Tier 2 guardrail removed these k values from consideration: "
            + ", ".join(str(value) for value in sorted(blocked_ks))
        )
    notes.append("Plateau rule was not satisfied; selected the strongest knee in the objective curve.")
    return RecommendationResult(
        recommended_k=int(knee_candidate),
        chosen_rule="objective_knee_fallback",
        alternatives=alternatives,
        notes=notes,
    )


def _find_plateau_candidate(
    feasible: pd.DataFrame,
    config: RecommendationConfig,
    blocked_ks: set[int],
) -> int | None:
    threshold = float(config.plateau_threshold_pct)
    span = int(config.plateau_consecutive_steps)
    guardrail = float(config.tier2_guardrail_pct)
    for idx in range(len(feasible)):
        if idx + span >= len(feasible):
            break
        window = feasible.iloc[idx + 1 : idx + span + 1]
        if window["objective_improvement_pct_from_prev_k"].isna().any():
            continue
        if window["tier1_improvement_pct_from_prev_k"].isna().any():
            continue
        if (window["objective_improvement_pct_from_prev_k"] < threshold).all() and (
            window["tier1_improvement_pct_from_prev_k"] < threshold
        ).all():
            current = feasible.iloc[idx]
            nxt = feasible.iloc[idx + 1]
            if int(current["k"]) in blocked_ks:
                continue
            return int(current["k"])
    return None


def _tier2_degrades_materially(current: float, next_value: float, threshold_pct: float) -> bool:
    if any(math.isnan(value) for value in [current, next_value]):
        return False
    if next_value >= current:
        return False
    reduction_pct = (current - next_value) / current * 100.0
    return reduction_pct > threshold_pct


def _blocked_by_tier2_guardrail(feasible: pd.DataFrame, threshold_pct: float) -> set[int]:
    blocked: set[int] = set()
    for idx in range(len(feasible) - 1):
        current = feasible.iloc[idx]
        nxt = feasible.iloc[idx + 1]
        if _tier2_degrades_materially(current["tier2_avg_drive"], nxt["tier2_avg_drive"], threshold_pct):
            blocked.add(int(current["k"]))
    return blocked


def _find_knee_candidate(feasible: pd.DataFrame, blocked_ks: set[int]) -> int:
    allowed = feasible.loc[~feasible["k"].isin(blocked_ks)].reset_index(drop=True)
    if allowed.empty:
        allowed = feasible
    improvements = allowed["objective_improvement_pct_from_prev_k"].fillna(0.0).tolist()
    if len(allowed) <= 2:
        return int(allowed.iloc[0]["k"])
    best_idx = 0
    best_drop = float("-inf")
    for idx in range(1, len(improvements) - 1):
        drop = improvements[idx] - improvements[idx + 1]
        if drop > best_drop:
            best_drop = drop
            best_idx = idx
    return int(allowed.iloc[best_idx]["k"])


def _top_alternatives(feasible: pd.DataFrame, selected_k: int) -> list[int]:
    ks = [int(value) for value in feasible["k"].tolist() if int(value) != int(selected_k)]
    return ks[:2]
