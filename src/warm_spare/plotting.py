from __future__ import annotations

from pathlib import Path

import pandas as pd


def generate_plots(metrics: pd.DataFrame, output_dir: Path) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Plot generation requires the plotting dependencies. "
            "Install with `pip install -r requirements.txt`."
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    created.append(
        _line_chart(plt, metrics, output_dir / "objective_vs_k.png", "objective", "Objective vs k", "Objective")
    )
    created.append(
        _multi_line_chart(
            plt,
            metrics,
            output_dir / "tier_avg_vs_k.png",
            ["tier1_avg_drive", "tier2_avg_drive", "tier3_avg_drive", "tier4_avg_drive"],
            "Tier Average Drive Time vs k",
            "Minutes",
        )
    )
    created.append(
        _multi_line_chart(
            plt,
            metrics,
            output_dir / "worst_avg_vs_k.png",
            ["tier1_worst_avg_drive", "tier2_worst_avg_drive", "overall_worst_avg_drive"],
            "Worst Average Drive vs k",
            "Minutes",
        )
    )
    created.append(
        _multi_line_chart(
            plt,
            metrics,
            output_dir / "worst_case_vs_k.png",
            ["overall_worst_case_drive", "max_assigned_dmax"],
            "Worst Case Drive vs k",
            "Minutes",
        )
    )
    created.append(
        _multi_line_chart(
            plt,
            metrics,
            output_dir / "assignment_stability_vs_k.png",
            ["site_overlap_with_prev_k", "offices_reassigned_from_prev_k"],
            "Assignment Stability vs k",
            "Count",
        )
    )
    return created


def _line_chart(plt, metrics: pd.DataFrame, path: Path, column: str, title: str, ylabel: str) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(metrics["k"], metrics[column], marker="o")
    ax.set_title(title)
    ax.set_xlabel("k")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _multi_line_chart(
    plt,
    metrics: pd.DataFrame,
    path: Path,
    columns: list[str],
    title: str,
    ylabel: str,
) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for column in columns:
        if column in metrics.columns:
            ax.plot(metrics["k"], metrics[column], marker="o", label=column)
    ax.set_title(title)
    ax.set_xlabel("k")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
