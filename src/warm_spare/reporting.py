from __future__ import annotations

import json
import platform
import subprocess
from importlib import metadata
from pathlib import Path

import yaml

from warm_spare.io import hash_inputs
from warm_spare.models import (
    AppConfig,
    OptimizationResult,
    PreprocessResult,
    RecommendationResult,
    RunMetadata,
    ValidationResult,
)

PACKAGE_NAMES = ["numpy", "pandas", "ortools", "PyYAML", "matplotlib"]


def create_output_dir(output_root: str, command_name: str) -> Path:
    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(output_root) / f"{timestamp}_{command_name}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_resolved_config(output_dir: Path, config: AppConfig) -> Path:
    path = output_dir / "resolved_config.yaml"
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config.to_dict(), handle, sort_keys=False)
    return path


def collect_run_metadata(config: AppConfig) -> RunMetadata:
    package_versions: dict[str, str] = {}
    for package_name in PACKAGE_NAMES:
        try:
            package_versions[package_name] = metadata.version(package_name)
        except metadata.PackageNotFoundError:
            package_versions[package_name] = "not-installed"
    return RunMetadata.create(
        python_version=platform.python_version(),
        package_versions=package_versions,
        active_scenario_profile=config.active_scenario_profile,
        input_hashes=hash_inputs(config),
        git_commit_hash=_git_commit_hash(),
    )


def write_run_metadata(output_dir: Path, run_metadata: RunMetadata) -> Path:
    path = output_dir / "run_metadata.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(run_metadata.to_dict(), handle, indent=2, sort_keys=True)
    return path


def write_validation_report(
    output_dir: Path,
    validation: ValidationResult,
    preprocess: PreprocessResult,
    config: AppConfig,
    infeasible_offices: list[str],
) -> Path:
    path = output_dir / "validation_report.md"
    scenario_stats_lines = []
    for stats in validation.scenario_stats:
        scenario_stats_lines.append(
            "| {name} | {minimum:.2f} | {median:.2f} | {p95:.2f} | {maximum:.2f} | {sym:.2f} | {diag} |".format(
                name=stats.scenario_name,
                minimum=stats.minimum,
                median=stats.median,
                p95=stats.p95,
                maximum=stats.maximum,
                sym=stats.mean_abs_symmetry_deviation,
                diag=stats.corrected_diagonal_entries,
            )
        )
    warning_lines = [f"- `{warning.category}`: {warning.message}" for warning in validation.warnings] or ["- None"]
    infeasible_lines = [f"- `{office_id}`" for office_id in infeasible_offices] or ["- None"]
    feasibility_table = _markdown_table(preprocess.office_feasibility)
    content = "\n".join(
        [
            "# Validation Report",
            "",
            "## Summary",
            f"- Office count: {len(validation.canonical_order)}",
            f"- Scenario inventory: {', '.join(config.scenario_names)}",
            f"- Active scenario profile: {config.active_scenario_profile}",
            f"- Canonical ordering used: {', '.join(validation.canonical_order)}",
            "",
            "## Corrections and Warnings",
            *warning_lines,
            "",
            "## Weight Profile",
            f"- Original weights: `{validation.original_weights}`",
            f"- Normalized weights: `{validation.normalized_weights}`",
            "",
            "## Scenario Stats",
            "| Scenario | Min | Median | P95 | Max | Mean Abs Symmetry Deviation | Corrected Diagonal Entries |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            *scenario_stats_lines,
            "",
            "## Feasibility Diagnostics",
            f"- SLA minutes: {config.sla_minutes}",
            f"- Globally infeasible offices: {len(infeasible_offices)}",
            *infeasible_lines,
            "",
            feasibility_table,
        ]
    )
    path.write_text(content + "\n", encoding="utf-8")
    return path


def write_preprocess_artifacts(output_dir: Path, preprocess: PreprocessResult) -> None:
    preprocess.office_feasibility.to_csv(output_dir / "office_feasibility.csv", index=False)
    preprocess.d_avg.to_csv(output_dir / "d_avg.csv")
    preprocess.d_max.to_csv(output_dir / "d_max.csv")
    preprocess.feasibility_mask.to_csv(output_dir / "feasibility_mask.csv")


def write_optimization_outputs(output_dir: Path, results: list[OptimizationResult]) -> None:
    selected_rows = []
    for result in results:
        for site in result.selected_sites:
            selected_rows.append({"k": result.k, "selected_site": site})
        assignment_path = output_dir / f"assignments_k_{result.k}.csv"
        if result.assignments is not None:
            result.assignments.to_csv(assignment_path, index=False)
        else:
            assignment_path.write_text(
                "office_id,assigned_spare,tier,avg_drive_minutes,worst_case_drive_minutes\n",
                encoding="utf-8",
            )
    import pandas as pd

    pd.DataFrame(selected_rows, columns=["k", "selected_site"]).to_csv(
        output_dir / "selected_sites_by_k.csv", index=False
    )


def write_metrics(output_dir: Path, metrics) -> Path:
    path = output_dir / "metrics_by_k.csv"
    metrics.to_csv(path, index=False)
    return path


def write_recommendation_report(
    output_dir: Path,
    recommendation: RecommendationResult,
    metrics,
) -> Path:
    path = output_dir / "recommendation.md"
    feasible_metrics = metrics.loc[
        metrics["solver_status"].isin(["OPTIMAL", "FEASIBLE", "TIME_LIMIT_WITH_INCUMBENT"])
    ]
    selected_row = None
    if recommendation.recommended_k is not None:
        matches = feasible_metrics.loc[feasible_metrics["k"] == recommendation.recommended_k]
        if not matches.empty:
            selected_row = matches.iloc[0]
    alternative_lines = [f"- k={value}" for value in recommendation.alternatives] or ["- None"]
    note_lines = [f"- {note}" for note in recommendation.notes] or ["- None"]
    selected_lines = ["- No recommendation available"]
    if selected_row is not None:
        selected_lines = [
            f"- Recommended k: {int(selected_row['k'])}",
            f"- Objective: {selected_row['objective']:.2f}",
            f"- Tier 1 average drive: {selected_row['tier1_avg_drive']:.2f}",
            f"- Tier 2 average drive: {selected_row['tier2_avg_drive']:.2f}",
            f"- Overall worst-case drive: {selected_row['overall_worst_case_drive']:.2f}",
        ]
    anomalous = metrics.loc[metrics["monotonicity_anomaly_flag"] == True, "k"].tolist()
    anomalous_lines = [f"- k={value}" for value in anomalous] or ["- None"]
    content = "\n".join(
        [
            "# Recommendation",
            "",
            "Optimization finds the best solution for each k. This recommendation applies business rules on top of those optimal per-k solutions.",
            "",
            "## Selected Recommendation",
            *selected_lines,
            f"- Rule used: `{recommendation.chosen_rule}`",
            "",
            "## Alternatives",
            *alternative_lines,
            "",
            "## Notes",
            *note_lines,
            "",
            "## Infeasible or Anomalous k Values",
            *anomalous_lines,
        ]
    )
    path.write_text(content + "\n", encoding="utf-8")
    return path


def _git_commit_hash() -> str | None:
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return None
    return output.strip() or None


def _markdown_table(frame) -> str:
    columns = list(frame.columns)
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    rows = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in frame.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows])
