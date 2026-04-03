from __future__ import annotations

import base64
import html
import json
import math
import platform
import subprocess
from importlib import metadata
from pathlib import Path

import pandas as pd
import yaml

from warm_spare.io import hash_inputs
from warm_spare.models import (
    AppConfig,
    OptimizationResult,
    PreprocessResult,
    RecommendationResult,
    RunMetadata,
    SpareSiteMapDetail,
    ValidationResult,
)

PACKAGE_NAMES = ["numpy", "pandas", "ortools", "PyYAML", "matplotlib", "googlemaps"]
FEASIBLE_STATUSES = {"OPTIMAL", "FEASIBLE", "TIME_LIMIT_WITH_INCUMBENT"}


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
            "| {name} | {minimum:.2f} | {median:.2f} | {p95:.2f} | {maximum:.2f} | {gap:.2f} |".format(
                name=stats.scenario_name,
                minimum=stats.minimum,
                median=stats.median,
                p95=stats.p95,
                maximum=stats.maximum,
                gap=stats.mean_abs_directional_gap,
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
            f"- Candidate office count: {len(validation.candidate_order)}",
            f"- Scenario inventory: {', '.join(config.scenario_names)}",
            f"- Active scenario profile: {config.active_scenario_profile}",
            f"- Canonical office ordering: {', '.join(validation.canonical_order)}",
            f"- Canonical candidate ordering: {', '.join(validation.candidate_order)}",
            "",
            "## Corrections and Warnings",
            *warning_lines,
            "",
            "## Weight Profile",
            f"- Original weights: `{validation.original_weights}`",
            f"- Normalized weights: `{validation.normalized_weights}`",
            "",
            "## Scenario Stats",
            "| Scenario | Min | Median | P95 | Max | Mean Abs Directional Gap |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
            *scenario_stats_lines,
            "",
            "## Feasibility Diagnostics",
            f"- One-way SLA minutes: {config.sla_minutes}",
            f"- Round-trip SLA minutes: {config.effective_round_trip_sla_minutes()}",
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
    if preprocess.one_way_dmax is not None:
        preprocess.one_way_dmax.to_csv(output_dir / "one_way_dmax.csv")
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
                "office_id,assigned_spare,tier,avg_drive_minutes,worst_case_drive_minutes,worst_case_one_way_drive_minutes\n",
                encoding="utf-8",
            )
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
    metrics: pd.DataFrame,
    optimization_results: list[OptimizationResult],
    preprocess: PreprocessResult,
    *,
    short_report: bool = False,
    map_path: Path | None = None,
    spare_detail_maps: list[SpareSiteMapDetail] | None = None,
    map_warnings: list[str] | None = None,
) -> Path:
    path = output_dir / "recommendation.md"
    feasible_metrics = metrics.loc[metrics["solver_status"].isin(FEASIBLE_STATUSES)].sort_values("k").reset_index(drop=True)
    results_by_k = {result.k: result for result in optimization_results}
    selected_result = results_by_k.get(recommendation.recommended_k) if recommendation.recommended_k is not None else None
    selected_row = _metric_row_for_k(feasible_metrics, recommendation.recommended_k)
    recommended_sites_table = None
    if selected_result is not None and selected_result.assignments is not None:
        recommended_sites_table = _build_selected_site_table(
            selected_result,
            preprocess,
            results_by_k,
        )
        recommended_sites_table.to_csv(output_dir / "recommended_selected_sites.csv", index=False)

    if short_report:
        content = _short_recommendation_content(recommendation, feasible_metrics, selected_row)
    else:
        content = _full_recommendation_content(
            output_dir=output_dir,
            recommendation=recommendation,
            feasible_metrics=feasible_metrics,
            selected_row=selected_row,
            selected_result=selected_result,
            selected_site_table=recommended_sites_table,
            map_path=map_path,
            spare_detail_maps=spare_detail_maps or [],
            map_warnings=map_warnings or [],
        )
    path.write_text(content + "\n", encoding="utf-8")
    write_recommendation_html(output_dir, content, spare_detail_maps or [])
    return path


def write_recommendation_html(
    output_dir: Path,
    markdown_content: str,
    spare_detail_maps: list[SpareSiteMapDetail],
) -> Path:
    body = _markdown_to_html(markdown_content, output_dir)
    detail_html = _spare_detail_maps_html(output_dir, spare_detail_maps)
    html_content = "\n".join(
        [
            "<!DOCTYPE html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\">",
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
            "<title>Warm Spare Recommendation</title>",
            "<style>",
            "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: #f7f7f5; color: #1f2933; }",
            ".page { max-width: 1100px; margin: 0 auto; padding: 40px 32px 64px; }",
            "h1, h2, h3 { color: #102a43; }",
            "h1 { font-size: 2.1rem; margin-bottom: 1rem; }",
            "h2 { font-size: 1.4rem; margin-top: 2.2rem; border-bottom: 1px solid #d9e2ec; padding-bottom: 0.35rem; }",
            "h3 { font-size: 1.1rem; margin-top: 1.4rem; }",
            "p, li { line-height: 1.55; font-size: 1rem; }",
            "ul { padding-left: 1.35rem; }",
            "code { background: #e9eef2; padding: 0.12rem 0.32rem; border-radius: 4px; font-size: 0.95em; }",
            "table { width: 100%; border-collapse: collapse; margin: 1rem 0 1.4rem; background: white; }",
            "th, td { border: 1px solid #d9e2ec; padding: 0.55rem 0.7rem; text-align: left; vertical-align: top; }",
            "th { background: #f0f4f8; }",
            ".image-block { margin: 1rem 0 1.8rem; }",
            ".image-block img { width: 100%; max-width: 100%; height: auto; border: 1px solid #d9e2ec; background: white; }",
            ".detail-grid { display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; margin: 1rem 0 2rem; align-items: start; }",
            ".detail-card { background: white; border: 1px solid #d9e2ec; border-radius: 8px; padding: 16px; }",
            ".detail-card h3 { margin-top: 0; }",
            ".detail-image img { width: 100%; height: auto; border: 1px solid #d9e2ec; background: white; }",
            ".muted { color: #52606d; }",
            "@media (max-width: 900px) { .detail-grid { grid-template-columns: 1fr; } }",
            "a { color: #0b6efd; text-decoration: none; }",
            "a:hover { text-decoration: underline; }",
            "</style>",
            "</head>",
            "<body>",
            "<div class=\"page\">",
            body,
            detail_html,
            "</div>",
            "</body>",
            "</html>",
        ]
    )
    path = output_dir / "recommendation.html"
    path.write_text(html_content + "\n", encoding="utf-8")
    return path


def _short_recommendation_content(
    recommendation: RecommendationResult,
    feasible_metrics: pd.DataFrame,
    selected_row: pd.Series | None,
) -> str:
    alternative_lines = [f"- k={value}" for value in recommendation.alternatives] or ["- None"]
    note_lines = [f"- {note}" for note in recommendation.notes] or ["- None"]
    selected_lines = ["- No recommendation available"]
    if selected_row is not None:
        selected_lines = [
            f"- Recommended k: {int(selected_row['k'])}",
            f"- Total weighted travel burden: {_fmt_float(selected_row['objective'])}",
            f"- Tier 1 average round trip: {_fmt_float(selected_row['tier1_avg_drive'])}",
            f"- Tier 2 average round trip: {_fmt_float(selected_row['tier2_avg_drive'])}",
            f"- Overall worst-case round trip: {_fmt_float(selected_row['overall_worst_case_drive'])}",
        ]
    anomalous = feasible_metrics.loc[feasible_metrics["monotonicity_anomaly_flag"] == True, "k"].tolist()
    anomalous_lines = [f"- k={value}" for value in anomalous] or ["- None"]
    return "\n".join(
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


def _markdown_to_html(markdown_content: str, output_dir: Path) -> str:
    lines = markdown_content.splitlines()
    html_lines: list[str] = []
    list_open = False
    table_buffer: list[str] = []
    paragraph_buffer: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_buffer
        if paragraph_buffer:
            text = " ".join(part.strip() for part in paragraph_buffer if part.strip())
            html_lines.append(f"<p>{_format_inline(text, output_dir)}</p>")
            paragraph_buffer = []

    def flush_list() -> None:
        nonlocal list_open
        if list_open:
            html_lines.append("</ul>")
            list_open = False

    def flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        rows = [_split_table_row(row) for row in table_buffer]
        if len(rows) >= 2:
            header = rows[0]
            body_rows = rows[2:] if len(rows) > 2 else []
            html_lines.append("<table>")
            html_lines.append("<thead><tr>" + "".join(f"<th>{_format_inline(cell, output_dir)}</th>" for cell in header) + "</tr></thead>")
            if body_rows:
                html_lines.append("<tbody>")
                for row in body_rows:
                    html_lines.append("<tr>" + "".join(f"<td>{_format_inline(cell, output_dir)}</td>" for cell in row) + "</tr>")
                html_lines.append("</tbody>")
            html_lines.append("</table>")
        table_buffer = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            flush_list()
            table_buffer.append(stripped)
            continue
        flush_table()
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped.startswith("# "):
            flush_paragraph()
            flush_list()
            html_lines.append(f"<h1>{_format_inline(stripped[2:].strip(), output_dir)}</h1>")
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            flush_list()
            html_lines.append(f"<h2>{_format_inline(stripped[3:].strip(), output_dir)}</h2>")
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            flush_list()
            html_lines.append(f"<h3>{_format_inline(stripped[4:].strip(), output_dir)}</h3>")
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            if not list_open:
                html_lines.append("<ul>")
                list_open = True
            html_lines.append(f"<li>{_format_inline(stripped[2:].strip(), output_dir)}</li>")
            continue
        paragraph_buffer.append(stripped)

    flush_paragraph()
    flush_list()
    flush_table()
    return "\n".join(html_lines)


def _split_table_row(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip("|").split("|")]


def _format_inline(text: str, output_dir: Path) -> str:
    escaped = html.escape(text)
    escaped = _replace_code_spans(escaped)
    escaped = _replace_markdown_links_and_images(escaped, output_dir)
    return escaped


def _replace_code_spans(text: str) -> str:
    parts = text.split("`")
    if len(parts) == 1:
        return text
    rebuilt: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            rebuilt.append(f"<code>{part}</code>")
        else:
            rebuilt.append(part)
    return "".join(rebuilt)


def _replace_markdown_links_and_images(text: str, output_dir: Path) -> str:
    import re

    def image_repl(match: re.Match[str]) -> str:
        alt = html.escape(match.group(1))
        target = html.unescape(match.group(2))
        data_uri = _image_data_uri(output_dir / target)
        if data_uri is None:
            return match.group(0)
        return f'<div class="image-block"><img alt="{alt}" src="{data_uri}"></div>'

    def link_repl(match: re.Match[str]) -> str:
        label = html.escape(match.group(1))
        href = html.unescape(match.group(2))
        return f'<a href="{html.escape(href)}">{label}</a>'

    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image_repl, text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_repl, text)
    return text


def _image_data_uri(path: Path) -> str | None:
    if not path.exists():
        return None
    suffix = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
    }.get(suffix)
    if mime is None:
        return None
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _full_recommendation_content(
    *,
    output_dir: Path,
    recommendation: RecommendationResult,
    feasible_metrics: pd.DataFrame,
    selected_row: pd.Series | None,
    selected_result: OptimizationResult | None,
    selected_site_table: pd.DataFrame | None,
    map_path: Path | None,
    spare_detail_maps: list[SpareSiteMapDetail],
    map_warnings: list[str],
) -> str:
    if selected_row is None or selected_result is None or selected_result.assignments is None:
        return _short_recommendation_content(recommendation, feasible_metrics, selected_row)

    lower_neighbor = _neighbor_row(feasible_metrics, int(selected_row["k"]), direction=-1)
    higher_neighbor = _neighbor_row(feasible_metrics, int(selected_row["k"]), direction=1)
    summary_bullets = [
        f"- Recommended warm spare count: `k = {int(selected_row['k'])}`",
        f"- Recommended warm spare offices: {', '.join(f'`{site}`' for site in selected_result.selected_sites)}",
        f"- Total weighted travel burden: `{_fmt_float(selected_row['objective'])}`",
        f"- Overall worst-case round trip: `{_fmt_float(selected_row['overall_worst_case_drive'])}` minutes",
        f"- Worst typical assigned round trip: `{_fmt_float(selected_row['overall_worst_avg_drive'])}` minutes",
        f"- SLA violations: `{int(selected_row['sla_violations'])}`",
    ]
    decision_request = (
        "Approve the recommended warm spare site set for planning review and field validation."
    )
    selected_site_lines = _markdown_table(selected_site_table)
    chart_lines = _chart_section_lines(output_dir)
    selected_k = int(selected_row["k"])
    why_not_lines = [
        *_why_not_neighbor_lines(selected_row, lower_neighbor, recommendation, lower=True),
        *_why_not_neighbor_lines(selected_row, higher_neighbor, recommendation, lower=False),
    ]
    notes = [f"- {note}" for note in recommendation.notes] or ["- None"]
    if map_warnings:
        notes.extend(f"- {warning}" for warning in map_warnings)
    assignments_path = output_dir / f"assignments_k_{selected_k}.csv"
    site_csv_path = output_dir / "recommended_selected_sites.csv"
    map_section = _map_section_lines(map_path, spare_detail_maps)

    return "\n".join(
        [
            "# Market Recommendation Report",
            "",
            "## Executive Summary",
            "This report recommends a warm spare site count and the specific office IDs to use for this market. "
            "The model tests each candidate site count, finds the best placement for that count, and then compares the tradeoff between better coverage and adding more spare sites.",
            "",
            *summary_bullets,
            f"- Rule used for recommendation: `{recommendation.chosen_rule}`",
            f"- Decision request: {decision_request}",
            "",
            "## Recommended Site Set",
            "These are the offices recommended to serve as warm spare locations for the selected market result.",
            "",
            selected_site_lines,
            "",
            f"Supporting exports: [`recommended_selected_sites.csv`]({site_csv_path.name}) and [`assignments_k_{selected_k}.csv`]({assignments_path.name})",
            "",
            "## Why This Count Was Chosen",
            "The recommendation is based on where the benefit curve begins to flatten. Lower values of `k` leave too much travel burden concentrated on too few sites. Higher values still improve results, but at a slower rate and with a larger operational footprint.",
            "",
            f"- Compared with `k={selected_k - 1}`: {_comparison_line(lower_neighbor, selected_row)}",
            f"- Compared with `k={selected_k + 1}`: {_comparison_line(selected_row, higher_neighbor) if higher_neighbor is not None else 'No higher neighboring solution is available for comparison.'}",
            "",
            *chart_lines,
            "",
            *map_section,
            "",
            "## Why Neighboring Options Were Not Chosen",
            *why_not_lines,
            "",
            "## Operational Interpretation",
            "The total weighted travel burden is the model's comparison score. It is calculated by taking each office's assigned round-trip time, multiplying by that office's priority weight, and summing the results across the market. Lower is better. The score is not a direct business unit like dollars; it is used to compare one placement option to another.",
            "",
            f"- Recommended site count: `{selected_k}`",
            f"- Average load per spare: `{_fmt_float(selected_row['avg_load_per_spare'])}` offices",
            f"- Maximum load on any spare: `{_fmt_float(selected_row['max_load_per_spare'])}` offices",
            f"- Offices reassigned from `k={selected_k - 1}` to `k={selected_k}`: `{_fmt_float(selected_row['offices_reassigned_from_prev_k'])}`",
            "",
            "## Caveats and Notes",
            *notes,
            "- This report should be reviewed alongside the market's tier quality and routing-input quality before final approval.",
            "- If this run used provisional tiers or static-only traffic, treat the site set as a planning recommendation pending final validation.",
        ]
    )


def _build_selected_site_table(
    result: OptimizationResult,
    preprocess: PreprocessResult,
    results_by_k: dict[int, OptimizationResult],
) -> pd.DataFrame:
    assignments = result.assignments.copy()
    offices = preprocess.offices.set_index("office_id")
    rows: list[dict[str, object]] = []
    next_one_sites = set(results_by_k.get(result.k + 1).selected_sites) if results_by_k.get(result.k + 1) else set()
    next_two_sites = set(results_by_k.get(result.k + 2).selected_sites) if results_by_k.get(result.k + 2) else set()

    for site in result.selected_sites:
        site_assignments = assignments.loc[assignments["assigned_spare"] == site]
        site_tier = int(offices.loc[site, "tier"]) if site in offices.index else math.nan
        rows.append(
            {
                "selected_site": site,
                "site_tier": site_tier,
                "load_count": int(len(site_assignments)),
                "avg_assigned_round_trip_minutes": round(float(site_assignments["avg_drive_minutes"].mean()), 2),
                "max_assigned_round_trip_minutes": round(float(site_assignments["worst_case_drive_minutes"].max()), 2),
                "persists_in_k_plus_1": site in next_one_sites,
                "persists_in_k_plus_2": site in next_two_sites,
            }
        )
    return pd.DataFrame(rows)


def _metric_row_for_k(metrics: pd.DataFrame, k: int | None) -> pd.Series | None:
    if k is None:
        return None
    matches = metrics.loc[metrics["k"] == k]
    if matches.empty:
        return None
    return matches.iloc[0]


def _neighbor_row(metrics: pd.DataFrame, k: int, *, direction: int) -> pd.Series | None:
    target = k + direction
    return _metric_row_for_k(metrics, target)


def _comparison_line(from_row: pd.Series | None, to_row: pd.Series | None) -> str:
    if from_row is None or to_row is None:
        return "No direct comparison is available."
    burden_change = _pct_change(from_row["objective"], to_row["objective"])
    worst_case_change = _pct_change(from_row["overall_worst_case_drive"], to_row["overall_worst_case_drive"])
    return (
        f"total weighted travel burden changes by `{burden_change}` and overall worst-case round trip changes by `{worst_case_change}`."
    )


def _why_not_neighbor_lines(
    selected_row: pd.Series,
    neighbor_row: pd.Series | None,
    recommendation: RecommendationResult,
    *,
    lower: bool,
) -> list[str]:
    if neighbor_row is None:
        return ["- No neighboring comparison is available."]
    selected_k = int(selected_row["k"])
    neighbor_k = int(neighbor_row["k"])
    if lower:
        return [
            f"- `k={neighbor_k}` was not chosen because it carries a higher total weighted travel burden (`{_fmt_float(neighbor_row['objective'])}` vs `{_fmt_float(selected_row['objective'])}`) and a worse overall worst-case round trip (`{_fmt_float(neighbor_row['overall_worst_case_drive'])}` vs `{_fmt_float(selected_row['overall_worst_case_drive'])}` minutes).",
        ]
    guardrail_note = ""
    guardrail_hits = _parse_guardrail_hits(recommendation.notes)
    if neighbor_k in guardrail_hits:
        guardrail_note = " It was also screened out by the Tier 2 guardrail in this run."
    return [
        f"- `k={neighbor_k}` was not chosen because the recommendation stops at the first defensible elbow rather than automatically selecting a larger footprint. Moving from `k={selected_k}` to `k={neighbor_k}` improves total weighted travel burden from `{_fmt_float(selected_row['objective'])}` to `{_fmt_float(neighbor_row['objective'])}`, but it also commits to an additional spare site.{guardrail_note}",
    ]


def _parse_guardrail_hits(notes: list[str]) -> set[int]:
    for note in notes:
        if "removed these k values from consideration:" not in note:
            continue
        values = note.rsplit(":", 1)[-1]
        hits = set()
        for part in values.split(","):
            stripped = part.strip()
            if stripped.isdigit():
                hits.add(int(stripped))
        return hits
    return set()


def _chart_section_lines(output_dir: Path) -> list[str]:
    charts = [
        ("Objective curve", "objective_vs_k.png", "Shows how the total weighted travel burden falls as more spare sites are added."),
        ("Worst typical assigned drive", "worst_avg_vs_k.png", "Shows how the worst assigned typical round-trip burden changes across the tested site counts."),
        ("Worst-case drive", "worst_case_vs_k.png", "Shows tail-risk exposure for the worst assigned office under the tested site counts."),
    ]
    lines = ["## Evidence Charts"]
    for title, filename, description in charts:
        if (output_dir / filename).exists():
            lines.extend(
                [
                    f"### {title}",
                    description,
                    "",
                    f"![{title}]({filename})",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"### {title}",
                    description,
                    "",
                    f"- Chart not generated for this run: `{filename}`",
                    "",
                ]
            )
    return lines


def _map_section_lines(map_path: Path | None, spare_detail_maps: list[SpareSiteMapDetail]) -> list[str]:
    lines = [
        "## Recommended Coverage Map",
        "Color shows which recommended warm spare serves each office. Marker shape shows office tier. Star markers indicate the selected warm spare locations.",
        "",
    ]
    if map_path is not None and map_path.exists():
        lines.extend(
            [
                f"![Recommended Coverage Map]({map_path.name})",
                "",
            ]
        )
        alignment_path = map_path.parent / "recommended_sites_map_alignment_check.png"
        if alignment_path.exists():
            lines.append(f"Alignment check artifact: [`{alignment_path.name}`]({alignment_path.name})")
            lines.append("")
    else:
        lines.append("- Map was not generated for this run.")
        lines.append("")
    if spare_detail_maps:
        lines.extend(
            [
                "The HTML version of this report also includes one zoomed detail map per selected warm spare, along with the spare address and assigned-office tier counts.",
                "",
            ]
        )
    return lines


def _spare_detail_maps_html(output_dir: Path, spare_detail_maps: list[SpareSiteMapDetail]) -> str:
    if not spare_detail_maps:
        return ""
    sections = ["<section>", "<h2>Spare Site Detail Maps</h2>"]
    for detail in spare_detail_maps:
        data_uri = _image_data_uri(output_dir / detail.map_path)
        if data_uri is None:
            continue
        tier_lines = "".join(
            f"<li>Tier {tier}: <strong>{count}</strong></li>"
            for tier, count in [(1, detail.tier_counts.get(1, 0)), (2, detail.tier_counts.get(2, 0)), (3, detail.tier_counts.get(3, 0)), (4, detail.tier_counts.get(4, 0))]
        )
        title = html.escape(detail.site_name or detail.spare_site)
        site_id = html.escape(detail.spare_site)
        address = html.escape(detail.address or "n/a")
        sections.extend(
            [
                '<div class="detail-grid">',
                f'<div class="detail-image"><img alt="Spare detail map for {site_id}" src="{data_uri}"></div>',
                '<div class="detail-card">',
                f"<h3>{title}</h3>",
                f"<p><strong>Spare site:</strong> <code>{site_id}</code></p>",
                f'<p><strong>Address:</strong> <span class="muted">{address}</span></p>',
                f"<p><strong>Total assigned offices:</strong> {detail.total_offices}</p>",
                "<p><strong>Assigned offices by tier</strong></p>",
                f"<ul>{tier_lines}</ul>",
                "</div>",
                "</div>",
            ]
        )
    sections.append("</section>")
    return "\n".join(sections)


def _pct_change(start: float, end: float) -> str:
    if any(math.isnan(float(value)) for value in [start, end]):
        return "n/a"
    if float(start) == 0:
        return "n/a"
    change = (float(end) - float(start)) / float(start) * 100.0
    return f"{change:+.2f}%"


def _fmt_float(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if math.isnan(number):
        return "n/a"
    return f"{number:.2f}"


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
