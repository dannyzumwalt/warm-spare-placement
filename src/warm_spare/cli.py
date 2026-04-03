from __future__ import annotations

import argparse
import sys

from warm_spare.config import ConfigError, load_config, resolve_market_config
from warm_spare.evaluate import evaluate_results
from warm_spare.geocode import GeocodeError
from warm_spare.io import ValidationError, load_and_validate_inputs
from warm_spare.mapping import generate_recommendation_map
from warm_spare.matrix_builder import ProviderError, build_matrix_dataset
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warm-spare",
        description="Validate inputs, build drive-time matrices, preprocess matrices, solve warm spare placement, and generate reports.",
        epilog=(
            "Commands: build-matrix=call provider and generate directional/round-trip datasets; "
            "validate=validation only; preprocess=validation plus preprocessing artifacts; "
            "optimize/report/run=full end-to-end analysis. Select a config with --config."
        ),
    )
    parser.add_argument(
        "command",
        choices=["build-matrix", "validate", "preprocess", "optimize", "report", "run"],
        help="Pipeline mode to execute.",
    )
    parser.add_argument(
        "--config",
        default="config/default.yaml",
        help="Path to the YAML config file. Defaults to config/default.yaml.",
    )
    parser.add_argument(
        "--market",
        help="Short market alias that resolves to config/markets/<market>.yaml for build-matrix.",
    )
    parser.add_argument(
        "--market-file",
        help="Explicit path to a market YAML file for build-matrix.",
    )
    parser.add_argument(
        "--resolve-quarantine-from",
        help=(
            "For build-matrix, rerun only the previously quarantined office/candidate pairs from a prior "
            "build directory or quarantine_manifest.json."
        ),
    )
    parser.add_argument(
        "--accept-quarantined-scenario",
        action="append",
        default=[],
        help=(
            "For build-matrix, keep a quarantined realtime scenario in the generated analysis config after "
            "review. Repeat the flag for multiple scenario IDs."
        ),
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help=(
            "For build-matrix, run only scenarios with departure_policy=none and skip realtime collection."
        ),
    )
    parser.add_argument(
        "--short-report",
        action="store_true",
        help=(
            "For optimize/report/run, generate a shorter recommendation summary instead of the full narrative report."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.command == "build-matrix":
            market = resolve_market_config(args.market, args.market_file)
            result = build_matrix_dataset(
                config,
                market,
                resolve_quarantine_from=args.resolve_quarantine_from,
                accept_quarantined_scenarios=set(args.accept_quarantined_scenario),
                static_only=bool(args.static_only),
            )
            print(result.output_dir)
            return 0 if result.success else 2

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
            metrics = evaluate_results(
                preprocess,
                optimization_results,
                config.sla_minutes,
                config.effective_round_trip_sla_minutes(),
            )
            recommendation = recommend_k(metrics, config.recommendation, config.spare_inventory)
            write_optimization_outputs(output_dir, optimization_results)
            write_metrics(output_dir, metrics)
            if config.artifacts.generate_plots:
                generate_plots(metrics, output_dir)
            map_path, spare_detail_maps, map_warnings = generate_recommendation_map(
                config,
                preprocess,
                optimization_results,
                recommendation,
                output_dir,
            )
            write_recommendation_report(
                output_dir,
                recommendation,
                metrics,
                optimization_results,
                preprocess,
                short_report=bool(args.short_report),
                map_path=map_path,
                spare_detail_maps=spare_detail_maps,
                map_warnings=map_warnings,
            )

        print(output_dir)
        return 0
    except (ValidationError, ConfigError, ProviderError, GeocodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Unhandled error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
