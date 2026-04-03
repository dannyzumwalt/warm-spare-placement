from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import yaml

from warm_spare.geocode import Geocoder, create_geocoder, geocode_offices
from warm_spare.io import ValidationError, load_offices_frame
from warm_spare.models import (
    AppConfig,
    DriveTimeElement,
    MarketConfig,
    MatrixBuildResult,
    MatrixBuilderConfig,
    ScenarioDefinition,
)


class ProviderError(RuntimeError):
    pass


class DriveTimeProvider:
    def fetch_matrix(
        self,
        origins: list[tuple[str, str]],
        destinations: list[tuple[str, str]],
        scenario: ScenarioDefinition,
    ) -> list[DriveTimeElement]:
        raise NotImplementedError


class GoogleDistanceMatrixProvider(DriveTimeProvider):
    def __init__(self, api_key: str) -> None:
        try:
            import googlemaps  # type: ignore
        except ImportError as exc:
            raise ProviderError(
                "googlemaps package is required for build-matrix. Install dependencies with `pip install -r requirements.txt`."
            ) from exc
        self._gmaps = googlemaps.Client(key=api_key)

    def fetch_matrix(
        self,
        origins: list[tuple[str, str]],
        destinations: list[tuple[str, str]],
        scenario: ScenarioDefinition,
    ) -> list[DriveTimeElement]:
        params: dict[str, object] = {
            "origins": [address for _, address in origins],
            "destinations": [address for _, address in destinations],
            "mode": scenario.mode,
        }
        if scenario.departure_policy == "now":
            params["departure_time"] = datetime.now(timezone.utc)
            if scenario.traffic_model:
                params["traffic_model"] = scenario.traffic_model
        response = self._gmaps.distance_matrix(**params)
        rows = response.get("rows", [])
        results: list[DriveTimeElement] = []
        origin_addresses = response.get("origin_addresses", [])
        destination_addresses = response.get("destination_addresses", [])
        for origin_idx, row in enumerate(rows):
            elements = row.get("elements", [])
            for dest_idx, element in enumerate(elements):
                status = element.get("status", "UNKNOWN")
                if status == "OK":
                    duration_payload = element.get("duration_in_traffic") or element.get("duration") or {}
                    seconds = float(duration_payload.get("value", 0.0))
                    duration_minutes = seconds / 60.0
                    raw_text = duration_payload.get("text")
                else:
                    duration_minutes = math.nan
                    raw_text = None
                results.append(
                    DriveTimeElement(
                        origin_id=origins[origin_idx][0],
                        destination_id=destinations[dest_idx][0],
                        duration_minutes=duration_minutes,
                        status=status,
                        raw_duration_text=raw_text,
                        normalized_origin=origin_addresses[origin_idx] if origin_idx < len(origin_addresses) else None,
                        normalized_destination=destination_addresses[dest_idx] if dest_idx < len(destination_addresses) else None,
                    )
                )
        return results


def create_provider(builder_config: MatrixBuilderConfig) -> DriveTimeProvider:
    if builder_config.provider != "google_distance_matrix":
        raise ProviderError(f"Unsupported matrix builder provider: {builder_config.provider}")
    api_key = os.environ.get(builder_config.api_key_env_var)
    if not api_key:
        raise ProviderError(
            f"Environment variable {builder_config.api_key_env_var} must be set for build-matrix"
        )
    return GoogleDistanceMatrixProvider(api_key)


def build_matrix_dataset(
    config: AppConfig,
    market: MarketConfig,
    *,
    provider: DriveTimeProvider | None = None,
    geocoder: Geocoder | None = None,
    resolve_quarantine_from: str | Path | None = None,
    accept_quarantined_scenarios: set[str] | None = None,
    static_only: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> MatrixBuildResult:
    if config.matrix_builder is None:
        raise ValidationError("matrix_builder configuration is required for build-matrix")

    builder_config = config.matrix_builder
    provider = provider or create_provider(builder_config)
    geocoder = geocoder or create_geocoder(builder_config)
    progress = progress_callback or _default_progress_callback
    offices = load_offices_frame(Path(market.offices_csv), require_address=True)
    candidate_offices = offices.loc[offices["tier"].isin(market.eligible_spare_tiers)].copy()
    if candidate_offices.empty:
        raise ValidationError("No candidate offices remain after applying market eligible_spare_tiers")
    selected_scenarios = _select_build_scenarios(builder_config.scenarios, static_only=static_only)

    accepted_scenarios = set(builder_config.accepted_anomaly_scenarios)
    if accept_quarantined_scenarios:
        accepted_scenarios.update(accept_quarantined_scenarios)
    resolution_targets, resolution_cache_path = _load_resolution_targets(resolve_quarantine_from)

    output_dir = _create_matrix_build_output_dir(Path(market.output_root), market.market_id)
    scenarios_dir = output_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    cache_path = resolution_cache_path or _resolve_cache_path(builder_config.cache_db_path)
    if resolve_quarantine_from is not None and not cache_path.exists():
        raise ValidationError(
            f"Resolution run requires an existing cache DB, but none was found at {cache_path}"
        )
    cache = MatrixCache(cache_path)

    offices = offices.reset_index(drop=True)
    candidate_offices = candidate_offices.reset_index(drop=True)
    offices.to_csv(output_dir / "office_manifest.csv", index=False)
    candidate_offices.to_csv(output_dir / "candidate_sites.csv", index=False)
    progress(f"Geocoding {len(offices)} offices for report mapping")
    coordinates_frame, geocode_warnings = geocode_offices(
        offices,
        cache_path=_resolve_cache_path(builder_config.geocode_cache_db_path),
        geocoder=geocoder,
    )
    office_coordinates_path = output_dir / "office_coordinates.csv"
    coordinates_frame.to_csv(office_coordinates_path, index=False)
    if geocode_warnings:
        progress(f"Geocode warnings: {len(geocode_warnings)} offices did not resolve cleanly")
    else:
        progress("Geocoding complete: all offices resolved successfully")
    progress(
        "Starting build-matrix for "
        f"{market.market_id}: offices={len(offices)}, candidates={len(candidate_offices)}, "
        f"scenarios={', '.join(s.id for s in selected_scenarios)}, output_dir={output_dir}"
    )

    unresolved_rows: list[dict[str, object]] = []
    directional_gap_summary: dict[str, float] = {}
    scenario_row_stats: dict[str, dict[str, float]] = {}
    for scenario in selected_scenarios:
        refresh_pairs = resolution_targets.get(scenario.id, set())
        progress(f"[{scenario.id}] collecting office_to_candidate")
        office_to_candidate, otc_unresolved = _collect_directional_matrix(
            market=market,
            provider=provider,
            cache=cache,
            builder_config=builder_config,
            scenario=scenario,
            offices=offices,
            candidate_offices=candidate_offices,
            direction="office_to_candidate",
            refresh_pairs=refresh_pairs,
            progress_callback=progress,
        )
        progress(f"[{scenario.id}] collecting candidate_to_office")
        candidate_to_office, cto_unresolved = _collect_directional_matrix(
            market=market,
            provider=provider,
            cache=cache,
            builder_config=builder_config,
            scenario=scenario,
            offices=offices,
            candidate_offices=candidate_offices,
            direction="candidate_to_office",
            refresh_pairs=refresh_pairs,
            progress_callback=progress,
        )
        unresolved_rows.extend(otc_unresolved)
        unresolved_rows.extend(cto_unresolved)

        office_to_candidate.to_csv(scenarios_dir / f"{scenario.id}__office_to_candidate.csv")
        candidate_to_office.to_csv(scenarios_dir / f"{scenario.id}__candidate_to_office.csv")
        round_trip = office_to_candidate + candidate_to_office
        round_trip.to_csv(scenarios_dir / f"{scenario.id}__round_trip.csv")
        directional_gap_summary[scenario.id] = float(
            (office_to_candidate - candidate_to_office).abs().to_numpy(dtype=float).mean()
        )
        scenario_row_stats[scenario.id] = {
            "min": float(round_trip.to_numpy(dtype=float).min()),
            "max": float(round_trip.to_numpy(dtype=float).max()),
            "mean_directional_gap": directional_gap_summary[scenario.id],
        }
        progress(
            f"[{scenario.id}] wrote directional and round-trip matrices "
            f"(unresolved_pairs_so_far={len(_dedupe_unresolved(unresolved_rows))})"
        )

    unresolved_pairs_path: Path | None = None
    if unresolved_rows:
        unresolved_pairs_path = output_dir / "unresolved_pairs.csv"
        pd.DataFrame(unresolved_rows).to_csv(unresolved_pairs_path, index=False)

    anomaly_summary, quarantined_scenarios, anomaly_rows = _detect_anomalies(
        scenarios_dir=scenarios_dir,
        scenarios=selected_scenarios,
        offices=offices,
        anomaly_config=builder_config.anomaly,
        accepted_scenarios=accepted_scenarios,
    )
    effective_quarantined_scenarios = [
        scenario_id for scenario_id in quarantined_scenarios if scenario_id not in accepted_scenarios
    ]
    included_scenarios = [
        scenario.id
        for scenario in selected_scenarios
        if scenario.id not in effective_quarantined_scenarios
    ]
    analysis_config_path = _write_generated_analysis_config(
        base_config=config,
        market=market,
        output_dir=output_dir,
        included_scenarios=included_scenarios,
        office_coordinates_path=office_coordinates_path,
    )

    quarantined_pairs_path, quarantine_manifest_path = _write_quarantine_outputs(
        output_dir=output_dir,
        anomaly_rows=anomaly_rows,
        effective_quarantined_scenarios=effective_quarantined_scenarios,
        accepted_scenarios=accepted_scenarios,
        anomaly_config=builder_config.anomaly,
    )

    build_manifest_path = output_dir / "build_manifest.json"
    build_manifest_path.write_text(
        json.dumps(
            {
                "market": market.to_dict(),
                "scenarios": [asdict(scenario) for scenario in selected_scenarios],
                "quarantined_scenarios": effective_quarantined_scenarios,
                "accepted_quarantined_scenarios": sorted(
                    scenario_id for scenario_id in quarantined_scenarios if scenario_id in accepted_scenarios
                ),
                "unresolved_pair_count": len(unresolved_rows),
                "analysis_config_path": str(analysis_config_path),
                "cache_db_path": str(cache_path),
                "directional_gap_summary": directional_gap_summary,
                "scenario_row_stats": scenario_row_stats,
                "anomaly_summary": anomaly_summary,
                "quarantine_manifest_path": str(quarantine_manifest_path) if quarantine_manifest_path else None,
                "resolution_source": str(resolve_quarantine_from) if resolve_quarantine_from else None,
                "resolution_target_pair_count": int(
                    sum(len(targets) for targets in resolution_targets.values())
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    build_report_path = _write_build_report(
        output_dir=output_dir,
        market=market,
        scenarios=selected_scenarios,
        unresolved_rows=unresolved_rows,
        quarantined_scenarios=effective_quarantined_scenarios,
        accepted_quarantined_scenarios=sorted(
            scenario_id for scenario_id in quarantined_scenarios if scenario_id in accepted_scenarios
        ),
        directional_gap_summary=directional_gap_summary,
        anomaly_summary=anomaly_summary,
        analysis_config_path=analysis_config_path,
        office_coordinates_path=office_coordinates_path,
        cache_path=cache_path,
        geocode_warnings=geocode_warnings,
        quarantined_pairs_path=quarantined_pairs_path,
        quarantine_manifest_path=quarantine_manifest_path,
        resolution_source=resolve_quarantine_from,
    )
    progress(
        f"Completed build-matrix for {market.market_id}: success={len(unresolved_rows) == 0}, "
        f"quarantined={len(effective_quarantined_scenarios)}, unresolved_pairs={len(unresolved_rows)}"
    )
    return MatrixBuildResult(
        output_dir=output_dir,
        analysis_config_path=analysis_config_path,
        office_coordinates_path=office_coordinates_path,
        unresolved_pairs_path=unresolved_pairs_path,
        quarantined_pairs_path=quarantined_pairs_path,
        quarantine_manifest_path=quarantine_manifest_path,
        build_report_path=build_report_path,
        build_manifest_path=build_manifest_path,
        quarantined_scenarios=effective_quarantined_scenarios,
        unresolved_pair_count=len(unresolved_rows),
        success=len(unresolved_rows) == 0,
    )


class MatrixCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS matrix_cache (
                market_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                origin_id TEXT NOT NULL,
                destination_id TEXT NOT NULL,
                duration_minutes REAL,
                status TEXT NOT NULL,
                raw_duration_text TEXT,
                normalized_origin TEXT,
                normalized_destination TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (market_id, scenario_id, direction, origin_id, destination_id)
            )
            """
        )
        self.connection.commit()

    def get_success(
        self,
        market_id: str,
        scenario_id: str,
        direction: str,
        origin_id: str,
        destination_id: str,
    ) -> float | None:
        row = self.connection.execute(
            """
            SELECT duration_minutes
            FROM matrix_cache
            WHERE market_id = ? AND scenario_id = ? AND direction = ? AND origin_id = ? AND destination_id = ? AND status = 'OK'
            """,
            (market_id, scenario_id, direction, origin_id, destination_id),
        ).fetchone()
        if row is None:
            return None
        return float(row[0])

    def upsert(
        self,
        market_id: str,
        scenario_id: str,
        direction: str,
        element: DriveTimeElement,
        attempt_count: int,
        last_error: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO matrix_cache (
                market_id, scenario_id, direction, origin_id, destination_id, duration_minutes, status,
                raw_duration_text, normalized_origin, normalized_destination, attempt_count, last_error, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_id, scenario_id, direction, origin_id, destination_id)
            DO UPDATE SET
                duration_minutes=excluded.duration_minutes,
                status=excluded.status,
                raw_duration_text=excluded.raw_duration_text,
                normalized_origin=excluded.normalized_origin,
                normalized_destination=excluded.normalized_destination,
                attempt_count=excluded.attempt_count,
                last_error=excluded.last_error,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                market_id,
                scenario_id,
                direction,
                element.origin_id,
                element.destination_id,
                None if math.isnan(element.duration_minutes) else float(element.duration_minutes),
                element.status,
                element.raw_duration_text,
                element.normalized_origin,
                element.normalized_destination,
                int(attempt_count),
                last_error,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()


def _collect_directional_matrix(
    *,
    market: MarketConfig,
    provider: DriveTimeProvider,
    cache: MatrixCache,
    builder_config: MatrixBuilderConfig,
    scenario: ScenarioDefinition,
    offices: pd.DataFrame,
    candidate_offices: pd.DataFrame,
    direction: str,
    refresh_pairs: set[tuple[str, str]],
    progress_callback: Callable[[str], None] | None,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    office_ids = offices["office_id"].astype(str).tolist()
    candidate_ids = candidate_offices["office_id"].astype(str).tolist()
    matrix = pd.DataFrame(index=office_ids, columns=candidate_ids, dtype=float)
    office_lookup = offices.set_index("office_id")["address"].to_dict()
    candidate_lookup = candidate_offices.set_index("office_id")["address"].to_dict()
    unresolved_rows: list[dict[str, object]] = []
    if direction == "office_to_candidate":
        chunk_size = min(
            builder_config.batch_limits.max_destinations_per_request,
            builder_config.batch_limits.max_elements_per_request,
        )
    else:
        chunk_size = min(
            builder_config.batch_limits.max_origins_per_request,
            builder_config.batch_limits.max_elements_per_request,
        )

    total_pairs = len(office_ids) * len(candidate_ids)
    progress_every = max(1, len(office_ids) // 10)
    for office_idx, office_id in enumerate(office_ids, start=1):
        chunks = list(_chunk_pairs(candidate_ids, chunk_size))
        for chunk_ids in chunks:
            if direction == "office_to_candidate":
                missing_pairs = [
                    candidate_id
                    for candidate_id in chunk_ids
                    if (office_id, candidate_id) in refresh_pairs
                    or cache.get_success(market.market_id, scenario.id, direction, office_id, candidate_id) is None
                ]
                if not missing_pairs:
                    for candidate_id in chunk_ids:
                        matrix.loc[office_id, candidate_id] = cache.get_success(
                            market.market_id, scenario.id, direction, office_id, candidate_id
                        )
                    continue
                request_origins = [(office_id, office_lookup[office_id])]
                request_destinations = [(candidate_id, candidate_lookup[candidate_id]) for candidate_id in missing_pairs]
            else:
                missing_pairs = [
                    candidate_id
                    for candidate_id in chunk_ids
                    if (office_id, candidate_id) in refresh_pairs
                    or cache.get_success(market.market_id, scenario.id, direction, candidate_id, office_id) is None
                ]
                if not missing_pairs:
                    for candidate_id in chunk_ids:
                        matrix.loc[office_id, candidate_id] = cache.get_success(
                            market.market_id, scenario.id, direction, candidate_id, office_id
                        )
                    continue
                request_origins = [(candidate_id, candidate_lookup[candidate_id]) for candidate_id in missing_pairs]
                request_destinations = [(office_id, office_lookup[office_id])]

            unresolved_rows.extend(
                _request_with_retries(
                    market=market,
                    provider=provider,
                    cache=cache,
                    builder_config=builder_config,
                    scenario=scenario,
                    direction=direction,
                    request_origins=request_origins,
                    request_destinations=request_destinations,
                )
            )
            for candidate_id in chunk_ids:
                if direction == "office_to_candidate":
                    matrix.loc[office_id, candidate_id] = cache.get_success(
                        market.market_id, scenario.id, direction, office_id, candidate_id
                    )
                else:
                    matrix.loc[office_id, candidate_id] = cache.get_success(
                        market.market_id, scenario.id, direction, candidate_id, office_id
                    )
        if progress_callback and (office_idx == len(office_ids) or office_idx % progress_every == 0):
            filled_pairs = int(matrix.notna().to_numpy().sum())
            progress_callback(
                f"[{scenario.id}] {direction}: offices={office_idx}/{len(office_ids)}, "
                f"filled_pairs={filled_pairs}/{total_pairs}"
            )

    if matrix.isna().any().any():
        for office_id in office_ids:
            for candidate_id in candidate_ids:
                if pd.isna(matrix.loc[office_id, candidate_id]):
                    unresolved_rows.append(
                        {
                            "market_id": market.market_id,
                            "scenario_id": scenario.id,
                            "direction": direction,
                            "origin_id": office_id if direction == "office_to_candidate" else candidate_id,
                            "destination_id": candidate_id if direction == "office_to_candidate" else office_id,
                            "error": "Missing value after collection",
                        }
                    )
    return matrix.astype(float), _dedupe_unresolved(unresolved_rows)


def _request_with_retries(
    *,
    market: MarketConfig,
    provider: DriveTimeProvider,
    cache: MatrixCache,
    builder_config: MatrixBuilderConfig,
    scenario: ScenarioDefinition,
    direction: str,
    request_origins: list[tuple[str, str]],
    request_destinations: list[tuple[str, str]],
) -> list[dict[str, object]]:
    unresolved: dict[tuple[str, str], dict[str, object]] = {
        (origin_id, destination_id): {
            "market_id": market.market_id,
            "scenario_id": scenario.id,
            "direction": direction,
            "origin_id": origin_id,
            "destination_id": destination_id,
            "error": "Not attempted",
        }
        for origin_id, _ in request_origins
        for destination_id, _ in request_destinations
    }
    max_attempts = builder_config.retry_policy.max_attempts
    for attempt in range(1, max_attempts + 1):
        try:
            results = provider.fetch_matrix(request_origins, request_destinations, scenario)
        except Exception as exc:
            _sleep_backoff(builder_config, attempt)
            for origin_id, _ in request_origins:
                for destination_id, _ in request_destinations:
                    cache.upsert(
                        market.market_id,
                        scenario.id,
                        direction,
                        DriveTimeElement(
                            origin_id=origin_id,
                            destination_id=destination_id,
                            duration_minutes=math.nan,
                            status="ERROR",
                            raw_duration_text=None,
                            normalized_origin=None,
                            normalized_destination=None,
                        ),
                        attempt,
                        str(exc),
                    )
                    unresolved[(origin_id, destination_id)]["error"] = str(exc)
            continue

        failed_pairs: list[tuple[str, str]] = []
        for element in results:
            if element.status == "OK" and not math.isnan(element.duration_minutes):
                cache.upsert(market.market_id, scenario.id, direction, element, attempt, None)
                unresolved.pop((element.origin_id, element.destination_id), None)
            else:
                cache.upsert(market.market_id, scenario.id, direction, element, attempt, element.status)
                unresolved[(element.origin_id, element.destination_id)]["error"] = element.status
                failed_pairs.append((element.origin_id, element.destination_id))
        if not unresolved:
            return []
        request_origins = [
            pair for pair in request_origins if any(pair[0] == failed_origin for failed_origin, _ in failed_pairs)
        ]
        request_destinations = [
            pair
            for pair in request_destinations
            if any(pair[0] == failed_destination for _, failed_destination in failed_pairs)
        ]
        _sleep_backoff(builder_config, attempt)
    return list(unresolved.values())


def _sleep_backoff(builder_config: MatrixBuilderConfig, attempt: int) -> None:
    retry = builder_config.retry_policy
    base = min(retry.max_backoff_seconds, retry.initial_backoff_seconds * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0, retry.jitter_seconds)
    if base + jitter > 0:
        time.sleep(base + jitter)


def _chunk_pairs(values: list[str], chunk_size: int) -> Iterable[list[str]]:
    for idx in range(0, len(values), chunk_size):
        yield values[idx : idx + chunk_size]


def _default_progress_callback(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _select_build_scenarios(
    scenarios: list[ScenarioDefinition],
    *,
    static_only: bool,
) -> list[ScenarioDefinition]:
    if not static_only:
        return list(scenarios)
    static_scenarios = [scenario for scenario in scenarios if scenario.departure_policy == "none"]
    if not static_scenarios:
        raise ValidationError("No static scenarios are configured, so --static-only cannot be used")
    return static_scenarios


def _dedupe_unresolved(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: dict[tuple[object, ...], dict[str, object]] = {}
    for row in rows:
        key = (
            row["market_id"],
            row["scenario_id"],
            row["direction"],
            row["origin_id"],
            row["destination_id"],
        )
        deduped[key] = row
    return list(deduped.values())


def _detect_anomalies(
    *,
    scenarios_dir: Path,
    scenarios: list[ScenarioDefinition],
    offices: pd.DataFrame,
    anomaly_config,
    accepted_scenarios: set[str],
) -> tuple[dict[str, dict[str, float | str | bool]], list[str], list[dict[str, object]]]:
    static_scenarios = [scenario for scenario in scenarios if scenario.departure_policy == "none"]
    realtime_scenarios = [scenario for scenario in scenarios if scenario.departure_policy == "now"]
    if realtime_scenarios and not static_scenarios:
        raise ValidationError("At least one static baseline scenario is required when realtime scenarios are configured")
    if not realtime_scenarios:
        return {}, [], []

    baseline_path = scenarios_dir / f"{static_scenarios[0].id}__round_trip.csv"
    baseline = pd.read_csv(baseline_path, index_col=0)
    tier_lookup = offices.set_index("office_id")["tier"].to_dict()
    anomaly_summary: dict[str, dict[str, float | str | bool]] = {}
    quarantined: list[str] = []
    anomaly_rows: list[dict[str, object]] = []

    for scenario in realtime_scenarios:
        current = pd.read_csv(scenarios_dir / f"{scenario.id}__round_trip.csv", index_col=0)
        delta_frame = current - baseline
        delta_values = delta_frame.to_numpy(dtype=float).flatten()
        delta_stddev = float(pd.Series(delta_values).std(ddof=0)) if delta_values.size else 0.0
        sigma_minutes = float(anomaly_config.pair_zscore_threshold) * delta_stddev
        flagged_pairs = 0
        total_pairs = int(current.size)
        tier12_pairs = 0
        tier12_flagged = 0
        scenario_rows: list[dict[str, object]] = []

        for office_id in current.index:
            for candidate_id in current.columns:
                baseline_value = float(baseline.loc[office_id, candidate_id])
                current_value = float(current.loc[office_id, candidate_id])
                delta_minutes = current_value - baseline_value
                absolute_threshold = float(anomaly_config.pair_abs_minutes)
                percent_threshold = baseline_value * float(anomaly_config.pair_pct_of_static)
                threshold = max(
                    absolute_threshold,
                    percent_threshold,
                    sigma_minutes,
                )
                abs_delta = abs(delta_minutes)
                zscore = abs_delta / delta_stddev if delta_stddev > 0 else (0.0 if abs_delta == 0 else float("inf"))
                is_flagged = abs_delta > threshold
                if is_flagged:
                    flagged_pairs += 1
                    scenario_rows.append(
                        {
                            "scenario_id": scenario.id,
                            "office_id": office_id,
                            "candidate_id": candidate_id,
                            "office_tier": int(tier_lookup[office_id]),
                            "baseline_round_trip_minutes": baseline_value,
                            "observed_round_trip_minutes": current_value,
                            "delta_minutes": delta_minutes,
                            "threshold_minutes": threshold,
                            "absolute_threshold_minutes": absolute_threshold,
                            "percent_threshold_minutes": percent_threshold,
                            "sigma_threshold_minutes": sigma_minutes,
                            "zscore": zscore,
                        }
                    )
                if int(tier_lookup[office_id]) in {1, 2}:
                    tier12_pairs += 1
                    if is_flagged:
                        tier12_flagged += 1

        pair_fraction = flagged_pairs / total_pairs if total_pairs else 0.0
        tier12_fraction = tier12_flagged / tier12_pairs if tier12_pairs else 0.0
        is_broad_anomaly = (
            pair_fraction > float(anomaly_config.scenario_pair_fraction)
            or tier12_fraction > float(anomaly_config.scenario_tier12_pair_fraction)
        )
        accepted = scenario.id in accepted_scenarios
        classification = "within_static_band"
        if flagged_pairs > 0 and is_broad_anomaly:
            classification = "accepted_anomaly" if accepted else "broad_scenario_anomaly"
        elif flagged_pairs > 0:
            classification = "plausible_live_congestion"
        quarantine_applied = bool(anomaly_config.quarantine_by_default and is_broad_anomaly and not accepted)
        anomaly_summary[scenario.id] = {
            "flagged_pairs": float(flagged_pairs),
            "total_pairs": float(total_pairs),
            "pair_fraction": pair_fraction,
            "tier12_flagged_pairs": float(tier12_flagged),
            "tier12_total_pairs": float(tier12_pairs),
            "tier12_fraction": tier12_fraction,
            "delta_stddev_minutes": delta_stddev,
            "sigma_threshold_minutes": sigma_minutes,
            "classification": classification,
            "accepted": accepted,
            "is_broad_anomaly": is_broad_anomaly,
            "quarantine_applied": quarantine_applied,
        }
        for row in scenario_rows:
            row["scenario_classification"] = classification
            row["scenario_accepted"] = accepted
            row["scenario_quarantine_applied"] = quarantine_applied
        anomaly_rows.extend(scenario_rows)
        if anomaly_config.quarantine_by_default and is_broad_anomaly:
            quarantined.append(scenario.id)

    return anomaly_summary, quarantined, anomaly_rows


def _write_generated_analysis_config(
    *,
    base_config: AppConfig,
    market: MarketConfig,
    output_dir: Path,
    included_scenarios: list[str],
    office_coordinates_path: Path,
) -> Path:
    config_dict = base_config.to_dict()
    config_dict["paths"]["offices_csv"] = market.offices_csv
    config_dict["paths"]["scenarios_dir"] = str(output_dir / "scenarios")
    config_dict["paths"]["output_root"] = market.output_root
    config_dict["paths"]["office_coordinates_csv"] = str(office_coordinates_path)
    config_dict["scenario_names"] = included_scenarios
    config_dict["candidate_tiers"] = list(market.eligible_spare_tiers)
    filtered_profiles: dict[str, dict[str, float]] = {}
    for profile_name, weights in config_dict["scenario_weight_profiles"].items():
        filtered_profiles[profile_name] = {
            scenario_name: weight
            for scenario_name, weight in weights.items()
            if scenario_name in included_scenarios
        }
    config_dict["scenario_weight_profiles"] = filtered_profiles
    config_dict.pop("matrix_builder", None)
    path = output_dir / "analysis_config.yaml"
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config_dict, handle, sort_keys=False)
    return path


def _write_quarantine_outputs(
    *,
    output_dir: Path,
    anomaly_rows: list[dict[str, object]],
    effective_quarantined_scenarios: list[str],
    accepted_scenarios: set[str],
    anomaly_config,
) -> tuple[Path | None, Path | None]:
    if not anomaly_rows:
        return None, None

    frame = pd.DataFrame(anomaly_rows)
    quarantined_frame = frame.loc[frame["scenario_id"].isin(effective_quarantined_scenarios)].copy()
    quarantined_pairs_path: Path | None = None
    if not quarantined_frame.empty:
        quarantined_pairs_path = output_dir / "quarantined_pairs.csv"
        quarantined_frame.to_csv(quarantined_pairs_path, index=False)

    scenario_targets = {
        scenario_id: [
            {"office_id": row.office_id, "candidate_id": row.candidate_id}
            for row in group.itertuples(index=False)
        ]
        for scenario_id, group in quarantined_frame.groupby("scenario_id")
    }
    scenario_pair_counts = {
        str(scenario_id): int(len(group))
        for scenario_id, group in frame.groupby("scenario_id")
    }
    manifest = {
        "quarantined_scenarios": effective_quarantined_scenarios,
        "accepted_quarantined_scenarios": sorted(scenario_id for scenario_id in accepted_scenarios if scenario_id in frame["scenario_id"].unique()),
        "scenario_targets": scenario_targets,
        "scenario_pair_counts": scenario_pair_counts,
        "threshold_policy": {
            "pair_abs_minutes": float(anomaly_config.pair_abs_minutes),
            "pair_pct_of_static": float(anomaly_config.pair_pct_of_static),
            "pair_zscore_threshold": float(anomaly_config.pair_zscore_threshold),
            "scenario_pair_fraction": float(anomaly_config.scenario_pair_fraction),
            "scenario_tier12_pair_fraction": float(anomaly_config.scenario_tier12_pair_fraction),
        },
    }
    quarantine_manifest_path = output_dir / "quarantine_manifest.json"
    quarantine_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return quarantined_pairs_path, quarantine_manifest_path


def _write_build_report(
    *,
    output_dir: Path,
    market: MarketConfig,
    scenarios: list[ScenarioDefinition],
    unresolved_rows: list[dict[str, object]],
    quarantined_scenarios: list[str],
    accepted_quarantined_scenarios: list[str],
    directional_gap_summary: dict[str, float],
    anomaly_summary: dict[str, dict[str, float | str | bool]],
    analysis_config_path: Path,
    office_coordinates_path: Path,
    cache_path: Path,
    geocode_warnings: list[str],
    quarantined_pairs_path: Path | None,
    quarantine_manifest_path: Path | None,
    resolution_source: str | Path | None,
) -> Path:
    lines = [
        "# Matrix Build Report",
        "",
        "## Summary",
        f"- Market: {market.market_id} ({market.label})",
        f"- Scenarios requested: {', '.join(scenario.id for scenario in scenarios)}",
        f"- Quarantined scenarios: {', '.join(quarantined_scenarios) if quarantined_scenarios else 'None'}",
        f"- Accepted quarantined scenarios: {', '.join(accepted_quarantined_scenarios) if accepted_quarantined_scenarios else 'None'}",
        f"- Unresolved pair count: {len(unresolved_rows)}",
        f"- Generated analysis config: {analysis_config_path}",
        f"- Office coordinates: {office_coordinates_path}",
        f"- Cache DB: {cache_path}",
        f"- Quarantined pairs report: {quarantined_pairs_path if quarantined_pairs_path else 'None'}",
        f"- Quarantine manifest: {quarantine_manifest_path if quarantine_manifest_path else 'None'}",
        f"- Resolution source: {resolution_source if resolution_source else 'None'}",
        "",
        "## Geocoding",
        f"- Office coordinates artifact: {office_coordinates_path}",
        f"- Geocode warning count: {len(geocode_warnings)}",
    ]
    if geocode_warnings:
        lines.extend(f"- {warning}" for warning in geocode_warnings[:20])
        if len(geocode_warnings) > 20:
            lines.append(f"- ... and {len(geocode_warnings) - 20} more")

    lines.extend([
        "",
        "## Directional Asymmetry",
    ])
    for scenario_id, value in directional_gap_summary.items():
        lines.append(f"- {scenario_id}: mean absolute directional gap = {value:.2f} minutes")

    lines.extend(["", "## Anomaly Thresholds"])
    if anomaly_summary:
        sample = next(iter(anomaly_summary.values()))
        lines.append(
            "- Pair anomalies use the larger of: 30 minutes, 50% of static baseline, or 3 standard deviations of scenario delta minutes."
        )
        lines.append(
            "- Scenario quarantine triggers when flagged pairs exceed 5% overall or 2% of Tier 1/2 origin pairs."
        )
        if isinstance(sample.get("sigma_threshold_minutes"), float):
            lines.append("- Static baseline is treated as the expected center of the distribution for z-score screening.")
    else:
        lines.append("- No realtime anomaly comparisons were required")

    lines.extend(["", "## Anomaly Classification"])
    if anomaly_summary:
        for scenario_id, stats in anomaly_summary.items():
            lines.append(
                f"- {scenario_id}: class={stats['classification']}, flagged={stats['flagged_pairs']:.0f}/{stats['total_pairs']:.0f} "
                f"({stats['pair_fraction']:.2%}), tier1/2 fraction={stats['tier12_fraction']:.2%}, "
                f"delta stddev={stats['delta_stddev_minutes']:.2f} minutes"
            )
    else:
        lines.append("- No realtime anomaly comparisons were required")

    lines.extend(["", "## Scenario Classes"])
    if anomaly_summary:
        class_groups = {
            "within_static_band": "Within static band",
            "plausible_live_congestion": "Plausible live congestion",
            "broad_scenario_anomaly": "Broad scenario anomaly",
            "accepted_anomaly": "Accepted anomaly",
        }
        for class_name, label in class_groups.items():
            scenario_ids = [
                scenario_id
                for scenario_id, stats in anomaly_summary.items()
                if stats["classification"] == class_name
            ]
            if scenario_ids:
                lines.append(f"- {label}: {', '.join(sorted(scenario_ids))}")
    else:
        lines.append("- No realtime anomaly comparisons were required")

    lines.extend(["", "## Request/API Failures"])
    if unresolved_rows:
        grouped: dict[tuple[str, str], int] = {}
        for row in unresolved_rows:
            key = (str(row["scenario_id"]), str(row["direction"]))
            grouped[key] = grouped.get(key, 0) + 1
        lines.append("- Build is incomplete; unresolved pairs remain and rerun will resume from cached successes.")
        for (scenario_id, direction), count in sorted(grouped.items()):
            lines.append(f"- {scenario_id} / {direction}: {count} unresolved request or element failures")
    else:
        lines.append("- All required pairs resolved successfully.")

    lines.extend(["", "## Quarantine Targets"])
    if quarantine_manifest_path:
        with quarantine_manifest_path.open("r", encoding="utf-8") as handle:
            quarantine_manifest = json.load(handle)
        lines.append(
            "- Quarantined sites are recorded as office/candidate pairs so a later resolution run can refresh only those targets."
        )
        scenario_targets = quarantine_manifest.get("scenario_targets", {})
        if scenario_targets:
            for scenario_id, targets in sorted(scenario_targets.items()):
                lines.append(f"- {scenario_id}: {len(targets)} office/candidate pairs targeted for refresh")
        else:
            lines.append("- No active quarantine targets remain.")
    else:
        lines.append("- No quarantined site targets were written.")

    lines.extend(["", "## Operational Interpretation"])
    if any(stats["classification"] == "plausible_live_congestion" for stats in anomaly_summary.values()):
        lines.append("- Plausible live congestion: localized deviations within the expected scenario-wide band.")
    if quarantined_scenarios:
        lines.append("- Broad scenario anomaly: scenario-wide deviations exceeded quarantine thresholds and were excluded by default.")
    if accepted_quarantined_scenarios:
        lines.append("- Accepted anomaly: analyst explicitly allowed quarantined scenarios into the generated analysis config.")
    if unresolved_rows:
        lines.append("- Request/API failure: provider instability or unresolved element statuses prevented a complete build.")
    if not anomaly_summary and not unresolved_rows:
        lines.append("- No anomaly or request issues detected.")

    path = output_dir / "build_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _create_matrix_build_output_dir(root: Path, market_id: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = root / "matrix_builds" / f"{timestamp}_{market_id}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _resolve_cache_path(cache_db_path: str) -> Path:
    return Path(cache_db_path).expanduser().resolve()


def _load_resolution_targets(
    source: str | Path | None,
) -> tuple[dict[str, set[tuple[str, str]]], Path | None]:
    if source is None:
        return {}, None
    path = Path(source).expanduser()
    manifest_path = path
    build_manifest_path = path.parent / "build_manifest.json"
    if path.is_dir():
        manifest_path = path / "quarantine_manifest.json"
        build_manifest_path = path / "build_manifest.json"
    if not manifest_path.exists():
        raise ValidationError(f"Resolution source does not exist: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    scenario_targets = payload.get("scenario_targets", {})
    resolved: dict[str, set[tuple[str, str]]] = {}
    for scenario_id, targets in scenario_targets.items():
        resolved[str(scenario_id)] = {
            (str(item["office_id"]), str(item["candidate_id"]))
            for item in targets
        }
    cache_db_path: Path | None = None
    if build_manifest_path.exists():
        with build_manifest_path.open("r", encoding="utf-8") as handle:
            build_manifest = json.load(handle)
        raw_cache_db_path = build_manifest.get("cache_db_path")
        if raw_cache_db_path:
            cache_db_path = Path(str(raw_cache_db_path)).expanduser().resolve()
    return resolved, cache_db_path
