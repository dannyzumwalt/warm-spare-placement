from __future__ import annotations

import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from warm_spare.models import MatrixBuilderConfig


class GeocodeError(RuntimeError):
    pass


class Geocoder:
    def geocode(self, office_id: str, address: str) -> dict[str, object]:
        raise NotImplementedError


class GoogleGeocoder(Geocoder):
    def __init__(self, api_key: str) -> None:
        try:
            import googlemaps  # type: ignore
        except ImportError as exc:
            raise GeocodeError(
                "googlemaps package is required for geocoding. Install dependencies with `pip install -r requirements.txt`."
            ) from exc
        self._gmaps = googlemaps.Client(key=api_key)

    def geocode(self, office_id: str, address: str) -> dict[str, object]:
        response = self._gmaps.geocode(address)
        if not response:
            return {
                "office_id": office_id,
                "input_address": address,
                "normalized_address": None,
                "latitude": math.nan,
                "longitude": math.nan,
                "geocode_status": "ZERO_RESULTS",
            }
        top = response[0]
        location = ((top.get("geometry") or {}).get("location") or {})
        return {
            "office_id": office_id,
            "input_address": address,
            "normalized_address": top.get("formatted_address"),
            "latitude": float(location.get("lat", math.nan)),
            "longitude": float(location.get("lng", math.nan)),
            "geocode_status": "OK",
        }


class GeocodeCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS office_geocodes (
                office_id TEXT PRIMARY KEY,
                input_address TEXT NOT NULL,
                normalized_address TEXT,
                latitude REAL,
                longitude REAL,
                geocode_status TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def get(self, office_id: str, input_address: str) -> dict[str, object] | None:
        row = self.connection.execute(
            """
            SELECT office_id, input_address, normalized_address, latitude, longitude, geocode_status
            FROM office_geocodes
            WHERE office_id = ? AND input_address = ?
            """,
            (office_id, input_address),
        ).fetchone()
        if row is None:
            return None
        return {
            "office_id": row[0],
            "input_address": row[1],
            "normalized_address": row[2],
            "latitude": float(row[3]) if row[3] is not None else math.nan,
            "longitude": float(row[4]) if row[4] is not None else math.nan,
            "geocode_status": row[5],
        }

    def upsert(self, payload: dict[str, object]) -> None:
        self.connection.execute(
            """
            INSERT INTO office_geocodes (
                office_id, input_address, normalized_address, latitude, longitude, geocode_status, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(office_id)
            DO UPDATE SET
                input_address=excluded.input_address,
                normalized_address=excluded.normalized_address,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                geocode_status=excluded.geocode_status,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                str(payload["office_id"]),
                str(payload["input_address"]),
                payload.get("normalized_address"),
                None if pd.isna(payload.get("latitude")) else float(payload["latitude"]),
                None if pd.isna(payload.get("longitude")) else float(payload["longitude"]),
                str(payload["geocode_status"]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()


def create_geocoder(builder_config: MatrixBuilderConfig) -> Geocoder:
    api_key = os.environ.get(builder_config.api_key_env_var)
    if not api_key:
        raise GeocodeError(
            f"Environment variable {builder_config.api_key_env_var} must be set for geocoding"
        )
    return GoogleGeocoder(api_key)


def geocode_offices(
    offices: pd.DataFrame,
    *,
    cache_path: Path,
    geocoder: Geocoder,
) -> tuple[pd.DataFrame, list[str]]:
    cache = GeocodeCache(cache_path)
    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    for office in offices.itertuples(index=False):
        cached = cache.get(str(office.office_id), str(office.address))
        payload: dict[str, object]
        if cached is not None and str(cached["geocode_status"]) == "OK":
            payload = cached
        else:
            try:
                payload = geocoder.geocode(str(office.office_id), str(office.address))
            except Exception as exc:
                payload = {
                    "office_id": str(office.office_id),
                    "input_address": str(office.address),
                    "normalized_address": None,
                    "latitude": math.nan,
                    "longitude": math.nan,
                    "geocode_status": f"ERROR:{exc}",
                }
            cache.upsert(payload)
        if str(payload["geocode_status"]) != "OK":
            warnings.append(f"{payload['office_id']}: {payload['geocode_status']}")
        rows.append(
            {
                "office_id": str(office.office_id),
                "input_address": str(office.address),
                "normalized_address": payload.get("normalized_address"),
                "latitude": payload.get("latitude"),
                "longitude": payload.get("longitude"),
                "geocode_status": payload.get("geocode_status"),
            }
        )
    return pd.DataFrame(rows), warnings
