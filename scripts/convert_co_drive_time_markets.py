#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import csv
from pathlib import Path


DEFAULT_DESTINATIONS = Path("src/co_drive_time-master/destinations.py")
DEFAULT_ORIGINS = Path("src/co_drive_time-master/origins.py")
DEFAULT_OFFICES_DIR = Path("data/input/markets")
DEFAULT_MARKETS_DIR = Path("config/markets")
SKIP_ASSIGNMENTS = {"tier2s"}
MARKET_LABELS = {
    "atl": "Atlanta",
    "la": "Los Angeles",
    "okc": "Oklahoma City",
    "tulsa": "Tulsa",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert co_drive_time market dictionaries into v2 office CSVs and market YAML files."
    )
    parser.add_argument(
        "--destinations",
        default=str(DEFAULT_DESTINATIONS),
        help="Path to co_drive_time destinations.py",
    )
    parser.add_argument(
        "--origins",
        default=str(DEFAULT_ORIGINS),
        help="Path to co_drive_time origins.py",
    )
    parser.add_argument(
        "--markets",
        nargs="*",
        default=["atl", "la", "okc", "tulsa"],
        help="Market IDs to convert. Defaults to atl la okc tulsa.",
    )
    parser.add_argument(
        "--offices-dir",
        default=str(DEFAULT_OFFICES_DIR),
        help="Directory to write generated office CSV files.",
    )
    parser.add_argument(
        "--markets-dir",
        default=str(DEFAULT_MARKETS_DIR),
        help="Directory to write generated market YAML files.",
    )
    parser.add_argument(
        "--output-root",
        default="outputs",
        help="output_root value to place in generated market YAML files.",
    )
    return parser.parse_args()


def load_python_dicts(path: Path) -> dict[str, dict[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dictionaries: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            continue
        if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            dictionaries[name] = value
    return dictionaries


def build_office_rows(
    *,
    market_id: str,
    market_entries: dict[str, str],
    tier2_hints: set[str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for office_id, address in sorted(market_entries.items()):
        hinted_tier2 = office_id in tier2_hints
        rows.append(
            {
                "office_id": office_id,
                "address": address,
                "tier": 2 if hinted_tier2 else 3,
                "market": market_id,
                "tier_source": "co_drive_time_hint" if hinted_tier2 else "defaulted_to_3_for_conversion",
                "needs_tier_review": "true",
            }
        )
    return rows


def write_offices_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["office_id", "address", "tier", "market", "tier_source", "needs_tier_review"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_market_yaml(path: Path, *, market_id: str, offices_csv: Path, output_root: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    label = MARKET_LABELS.get(market_id, market_id.upper())
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"market_id: {market_id}\n")
        handle.write(f"label: {label}\n")
        handle.write(f"offices_csv: {offices_csv}\n")
        handle.write(f"output_root: {output_root}\n")
        handle.write("eligible_spare_tiers: [1, 2, 3]\n")


def main() -> int:
    args = parse_args()
    destinations_path = Path(args.destinations)
    origins_path = Path(args.origins)
    if not destinations_path.exists():
        raise FileNotFoundError(f"destinations file not found: {destinations_path}")
    if not origins_path.exists():
        raise FileNotFoundError(f"origins file not found: {origins_path}")

    dictionaries = load_python_dicts(destinations_path)
    origins = load_python_dicts(origins_path).get("origins", {})
    tier2_hints = set(dictionaries.get("tier2s", {}).keys()) | set(origins.keys())
    offices_dir = Path(args.offices_dir)
    markets_dir = Path(args.markets_dir)

    converted: list[str] = []
    for market_id in args.markets:
        if market_id in SKIP_ASSIGNMENTS:
            continue
        market_entries = dictionaries.get(market_id)
        if market_entries is None:
            raise KeyError(f"Market '{market_id}' was not found in {destinations_path}")
        offices_csv = offices_dir / f"{market_id}_offices.csv"
        market_yaml = markets_dir / f"{market_id}.yaml"
        rows = build_office_rows(
            market_id=market_id,
            market_entries=market_entries,
            tier2_hints=tier2_hints,
        )
        write_offices_csv(offices_csv, rows)
        write_market_yaml(
            market_yaml,
            market_id=market_id,
            offices_csv=offices_csv,
            output_root=args.output_root,
        )
        converted.append(f"{market_id}:{len(rows)}")

    print("Converted markets:", ", ".join(converted))
    print("Office CSVs:", offices_dir)
    print("Market YAMLs:", markets_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
