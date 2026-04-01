from __future__ import annotations

import argparse
import sys

from warm_spare.config import load_config
from warm_spare.evaluate import evaluate_results
from warm_spare.io import ValidationError, load_and_validate_inputs
from warm_spare.optimize import solve_all_k
from warm_spare.plotting import generate_plots
from warm_spare.preprocess import enforce_global_feasibility, preprocess_inputs
from warm_spare.recommend import recommend_k
from warm_spare.reporting import (
    collect_run_metadata,
    create_output_dir,
    write_metrics,
    write_optimization_outputs,
    write_preprocess_artifacts,
    write_recommendation_report,
    write_resolved_config,
    write_run_metadata,
    write_validation_report,
)


FULL_COMMANDS = {"optimize", "report", "run"}
PREPROCESS_COMMANDS = {"preprocess", *FULL_COMMANDS}
VALIDATE_COMMANDS = {"validate", *PREPROCESS_COMMANDS}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="warm-spare")
    parser.add_argument("command", choices=["validate", "preprocess", "optimize", "report", "run"])
    parser.add_argument("--config", default="config/default.yaml")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        output_dir = create_output_dir(config.paths.output_root, args.command)
        write_resolved_config(output_dir, config)
        write_run_metadata(output_dir, collect_run_metadata(config))

        validation = load_and_validate_inputs(config)
        preprocess = preprocess_inputs(config, validation)
        infeasible_offices = enforce_global_feasibility(preprocess)
        write_validation_report(output_dir, validation, preprocess, config, infeasible_offices)

        if args.command in PREPROCESS_COMMANDS:
            write_preprocess_artifacts(output_dir, preprocess)

        if infeasible_offices:
            print(
                "Global feasibility check failed. Offices with zero feasible spare candidates: "
                + ", ".join(infeasible_offices),
                file=sys.stderr,
            )
            return 2

        if args.command in FULL_COMMANDS:
            optimization_results = solve_all_k(config, preprocess)
            metrics = evaluate_results(preprocess, optimization_results, config.sla_minutes)
            recommendation = recommend_k(metrics, config.recommendation)
            write_optimization_outputs(output_dir, optimization_results)
            write_metrics(output_dir, metrics)
            write_recommendation_report(output_dir, recommendation, metrics)
            if config.artifacts.generate_plots:
                generate_plots(metrics, output_dir)

        print(output_dir)
        return 0
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Unhandled error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
