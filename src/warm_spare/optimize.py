from __future__ import annotations

import time

import pandas as pd
from ortools.sat.python import cp_model

from warm_spare.models import AppConfig, OptimizationResult, PreprocessResult


SOLVED_WITH_ASSIGNMENTS = {"OPTIMAL", "FEASIBLE", "TIME_LIMIT_WITH_INCUMBENT"}


class _IncumbentTracker(cp_model.CpSolverSolutionCallback):
    def __init__(self) -> None:
        super().__init__()
        self.seen_solution = False

    def on_solution_callback(self) -> None:
        self.seen_solution = True


def solve_all_k(config: AppConfig, preprocess: PreprocessResult) -> list[OptimizationResult]:
    return [solve_for_k(config, preprocess, k) for k in config.k_values]


def solve_for_k(config: AppConfig, preprocess: PreprocessResult, k: int) -> OptimizationResult:
    office_ids = preprocess.canonical_order
    candidate_ids = preprocess.candidate_order
    office_count = len(office_ids)
    candidate_count = len(candidate_ids)
    tier_lookup = preprocess.offices.set_index("office_id")["tier"].to_dict()
    tier_weights = {office_id: config.tier_weights[int(tier_lookup[office_id])] for office_id in office_ids}

    model = cp_model.CpModel()
    x = {j: model.NewBoolVar(f"x_{j}") for j in range(candidate_count)}
    y = {
        (i, j): model.NewBoolVar(f"y_{i}_{j}")
        for i in range(office_count)
        for j in range(candidate_count)
    }

    scale = int(config.solver.objective_scale)
    objective_terms = []
    for i, office_id in enumerate(office_ids):
        for j, spare_id in enumerate(candidate_ids):
            feasible = int(preprocess.feasibility_mask.loc[office_id, spare_id])
            if feasible == 0:
                model.Add(y[(i, j)] == 0)
                continue
            cost = int(round(preprocess.d_avg.loc[office_id, spare_id] * tier_weights[office_id] * scale))
            objective_terms.append(cost * y[(i, j)])
            model.Add(y[(i, j)] <= x[j])
        model.Add(sum(y[(i, j)] for j in range(candidate_count)) == 1)

    model.Add(sum(x.values()) == k)
    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(config.solver.time_limit_seconds)
    solver.parameters.random_seed = int(config.solver.random_seed)
    solver.parameters.num_search_workers = int(config.solver.num_workers)

    tracker = _IncumbentTracker()
    start = time.perf_counter()
    try:
        status = solver.Solve(model, tracker)
        elapsed = time.perf_counter() - start
    except Exception:
        elapsed = time.perf_counter() - start
        return OptimizationResult(
            k=k,
            solver_status="SOLVER_ERROR",
            solve_time_seconds=elapsed,
            objective=None,
            selected_sites=[],
            assignments=None,
            assignment_map={},
            raw_solver_status="EXCEPTION",
            had_incumbent=False,
        )

    solver_status = _map_solver_status(status, tracker.seen_solution)
    if solver_status not in SOLVED_WITH_ASSIGNMENTS:
        return OptimizationResult(
            k=k,
            solver_status=solver_status,
            solve_time_seconds=elapsed,
            objective=None,
            selected_sites=[],
            assignments=None,
            assignment_map={},
            raw_solver_status=solver.StatusName(status),
            had_incumbent=tracker.seen_solution,
        )

    selected_indices = [j for j in range(candidate_count) if solver.Value(x[j]) == 1]
    selected_sites = [candidate_ids[j] for j in selected_indices]
    records = []
    assignment_map: dict[str, str] = {}
    for i, office_id in enumerate(office_ids):
        assigned_j = next(j for j in range(candidate_count) if solver.Value(y[(i, j)]) == 1)
        spare_id = candidate_ids[assigned_j]
        assignment_map[office_id] = spare_id
        records.append(
            {
                "office_id": office_id,
                "assigned_spare": spare_id,
                "tier": int(tier_lookup[office_id]),
                "avg_drive_minutes": float(preprocess.d_avg.loc[office_id, spare_id]),
                "worst_case_drive_minutes": float(preprocess.d_max.loc[office_id, spare_id]),
                "worst_case_one_way_drive_minutes": (
                    float(preprocess.one_way_dmax.loc[office_id, spare_id])
                    if preprocess.one_way_dmax is not None
                    else float("nan")
                ),
            }
        )

    objective = float(solver.ObjectiveValue()) / scale
    assignments = pd.DataFrame(records)
    return OptimizationResult(
        k=k,
        solver_status=solver_status,
        solve_time_seconds=elapsed,
        objective=objective,
        selected_sites=selected_sites,
        assignments=assignments,
        assignment_map=assignment_map,
        raw_solver_status=solver.StatusName(status),
        had_incumbent=tracker.seen_solution,
    )


def _map_solver_status(status: int, had_incumbent: bool) -> str:
    if status == cp_model.OPTIMAL:
        return "OPTIMAL"
    if status == cp_model.FEASIBLE:
        return "FEASIBLE"
    if status == cp_model.INFEASIBLE:
        return "INFEASIBLE"
    if status == cp_model.UNKNOWN:
        if had_incumbent:
            return "TIME_LIMIT_WITH_INCUMBENT"
        return "TIME_LIMIT_NO_INCUMBENT"
    return "SOLVER_ERROR"
