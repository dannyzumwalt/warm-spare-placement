from __future__ import annotations

import io
import math
import os
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from warm_spare.models import (
    AppConfig,
    OptimizationResult,
    PreprocessResult,
    RecommendationResult,
    SpareSiteMapDetail,
)


TIER_MARKERS = {1: "^", 2: "s", 3: "o", 4: "X"}
# Google Maps Static API standard size limit is 640x640 before scale is applied.
# Use a legal landscape rectangle so Google returns real additional horizontal coverage
# instead of us stretching a square basemap.
STATIC_MAP_SIZE = (640, 460)
STATIC_MAP_SCALE = 2
STATIC_MAPTYPE = "roadmap"
# Keep padding tight enough to preserve a useful zoom level while still protecting
# edge points and warm-spare labels from clipping.
MAP_PADDING_PX = 40
MAP_MAX_ZOOM = 16
MAP_MIN_ZOOM = 3


def generate_recommendation_map(
    config: AppConfig,
    preprocess: PreprocessResult,
    optimization_results: list[OptimizationResult],
    recommendation: RecommendationResult,
    output_dir: Path,
) -> tuple[Path | None, list[SpareSiteMapDetail], list[str]]:
    warnings: list[str] = []
    detail_maps: list[SpareSiteMapDetail] = []
    coordinates_path = config.paths.office_coordinates_csv
    if not coordinates_path:
        return None, detail_maps, warnings

    coordinates_file = Path(coordinates_path)
    if not coordinates_file.exists():
        warnings.append(f"Mapping skipped because office coordinates file was not found: {coordinates_file}")
        return None, detail_maps, warnings

    if recommendation.recommended_k is None:
        warnings.append("Mapping skipped because no recommended k was available.")
        return None, detail_maps, warnings

    selected_result = next((result for result in optimization_results if result.k == recommendation.recommended_k), None)
    if selected_result is None or selected_result.assignments is None:
        warnings.append("Mapping skipped because recommended assignments were not available.")
        return None, detail_maps, warnings

    coordinates = pd.read_csv(coordinates_file)
    required_columns = {"office_id", "latitude", "longitude", "geocode_status"}
    if not required_columns.issubset(coordinates.columns):
        warnings.append("Mapping skipped because office coordinates file is missing required columns.")
        return None, detail_maps, warnings

    merged = (
        preprocess.offices.merge(selected_result.assignments[["office_id", "assigned_spare"]], on="office_id", how="left")
        .merge(coordinates, on="office_id", how="left")
    )
    merged["latitude"] = pd.to_numeric(merged["latitude"], errors="coerce")
    merged["longitude"] = pd.to_numeric(merged["longitude"], errors="coerce")
    selected_sites = set(selected_result.selected_sites)
    missing_selected = merged.loc[
        merged["office_id"].isin(selected_sites)
        & (merged["latitude"].isna() | merged["longitude"].isna() | (merged["geocode_status"] != "OK")),
        "office_id",
    ].astype(str).tolist()
    if missing_selected:
        warnings.append(
            "Mapping skipped because recommended spare sites were missing coordinates: "
            + ", ".join(missing_selected)
        )
        return None, detail_maps, warnings

    omitted_non_selected = int(
        merged.loc[
            ~merged["office_id"].isin(selected_sites)
            & (merged["latitude"].isna() | merged["longitude"].isna() | (merged["geocode_status"] != "OK"))
        ].shape[0]
    )
    if omitted_non_selected:
        warnings.append(
            f"Map omitted {omitted_non_selected} non-spare offices because their coordinates were unavailable."
        )

    plottable = merged.loc[
        merged["latitude"].notna() & merged["longitude"].notna() & (merged["geocode_status"] == "OK")
    ].copy()
    if plottable.empty:
        warnings.append("Mapping skipped because no offices had usable coordinates.")
        return None, detail_maps, warnings

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
        from matplotlib.lines import Line2D  # type: ignore
    except ImportError:
        warnings.append(
            "Mapping skipped because matplotlib is not installed. Install with `pip install -r requirements.txt`."
        )
        return None, detail_maps, warnings

    logical_map_width = STATIC_MAP_SIZE[0]
    logical_map_height = STATIC_MAP_SIZE[1]
    map_width = logical_map_width * STATIC_MAP_SCALE
    map_height = logical_map_height * STATIC_MAP_SCALE
    center_lat, center_lng, zoom = _fit_static_map(
        plottable["latitude"].astype(float).tolist(),
        plottable["longitude"].astype(float).tolist(),
        logical_map_width,
        logical_map_height,
        padding_px=MAP_PADDING_PX / STATIC_MAP_SCALE,
    )
    map_image, basemap_warning = _fetch_google_static_basemap(
        center_lat=center_lat,
        center_lng=center_lng,
        zoom=zoom,
    )
    if basemap_warning:
        warnings.append(basemap_warning)

    center_world_x, center_world_y = _latlng_to_world(center_lat, center_lng, zoom)
    plottable["map_x"] = plottable.apply(
        lambda row: (
            (_latlng_to_world(float(row.latitude), float(row.longitude), zoom)[0] - center_world_x) * STATIC_MAP_SCALE
            + map_width / 2.0
        ),
        axis=1,
    )
    plottable["map_y"] = plottable.apply(
        lambda row: (
            (_latlng_to_world(float(row.latitude), float(row.longitude), zoom)[1] - center_world_y) * STATIC_MAP_SCALE
            + map_height / 2.0
        ),
        axis=1,
    )

    assigned_spares = [site for site in selected_result.selected_sites if site in plottable["assigned_spare"].dropna().unique()]
    cmap = plt.get_cmap("tab20")
    color_lookup = {site: cmap(index % 20) for index, site in enumerate(assigned_spares)}
    default_color = "#777777"

    fig, ax = plt.subplots(figsize=(11.0, 8.0))
    if map_image is not None:
        ax.imshow(map_image, extent=(0, map_width, map_height, 0), zorder=0)
    else:
        ax.set_facecolor("#f7f7f7")

    for tier, marker in TIER_MARKERS.items():
        tier_frame = plottable.loc[plottable["tier"] == tier]
        if tier_frame.empty:
            continue
        colors = [color_lookup.get(spare_id, default_color) for spare_id in tier_frame["assigned_spare"]]
        ax.scatter(
            tier_frame["map_x"],
            tier_frame["map_y"],
            c=colors,
            marker=marker,
            s=55,
            edgecolors="white",
            linewidths=0.6,
            alpha=0.9,
            zorder=2,
        )

    spare_frame = plottable.loc[plottable["office_id"].isin(selected_sites)].copy()
    ax.scatter(
        spare_frame["map_x"],
        spare_frame["map_y"],
        marker="*",
        s=340,
        c=[color_lookup.get(site, default_color) for site in spare_frame["office_id"]],
        edgecolors="black",
        linewidths=1.0,
        zorder=4,
    )
    for row in spare_frame.itertuples(index=False):
        offset_x = 8 if float(row.map_x) < map_width * 0.82 else -10
        offset_y = -10 if float(row.map_y) > map_height * 0.12 else 10
        ha = "left" if offset_x > 0 else "right"
        va = "bottom" if offset_y < 0 else "top"
        ax.annotate(
            str(row.office_id),
            (float(row.map_x), float(row.map_y)),
            xytext=(offset_x, offset_y),
            textcoords="offset points",
            fontsize=8,
            weight="bold",
            ha=ha,
            va=va,
            bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
            zorder=5,
            clip_on=False,
        )

    tier_handles = [
        Line2D([0], [0], marker=marker, color="w", markerfacecolor="#666666", markeredgecolor="white", markersize=8, linestyle="")
        for marker in TIER_MARKERS.values()
    ]
    tier_labels = [f"Tier {tier}" for tier in TIER_MARKERS]
    spare_handle = Line2D([0], [0], marker="*", color="black", markerfacecolor="white", markersize=12, linestyle="")
    first_legend = ax.legend(
        tier_handles + [spare_handle],
        tier_labels + ["Selected warm spare"],
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        title="Marker meaning",
        borderaxespad=0.0,
    )
    ax.add_artist(first_legend)
    if assigned_spares:
        assignment_handles = [
            Line2D([0], [0], marker="o", color="w", markerfacecolor=color_lookup[site], markersize=8, linestyle="")
            for site in assigned_spares
        ]
        assignment_labels = [f"Assigned to {site}" for site in assigned_spares]
        ax.legend(
            assignment_handles,
            assignment_labels,
            loc="lower left",
            bbox_to_anchor=(1.01, 0.0),
            title="Assignment color",
            fontsize=8,
            borderaxespad=0.0,
        )

    ax.set_xlim(-20, map_width + 20)
    ax.set_ylim(map_height + 20, -20)
    ax.set_title(f"Recommended Warm Spare Coverage Map (k={recommendation.recommended_k})")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.subplots_adjust(right=0.78, left=0.04, top=0.93, bottom=0.05)

    output_path = output_dir / "recommended_sites_map.png"
    fig.savefig(output_path, dpi=180)
    plt.close(fig)
    if map_image is not None:
        _write_alignment_diagnostic(
            output_dir=output_dir,
            spare_frame=spare_frame,
            center_lat=center_lat,
            center_lng=center_lng,
            zoom=zoom,
            map_width=map_width,
            map_height=map_height,
            warnings=warnings,
        )
    detail_maps = _write_spare_detail_maps(
        plottable=plottable,
        selected_result=selected_result,
        preprocess=preprocess,
        output_dir=output_dir,
    )
    return output_path, detail_maps, warnings


def _write_spare_detail_maps(
    *,
    plottable: pd.DataFrame,
    selected_result: OptimizationResult,
    preprocess: PreprocessResult,
    output_dir: Path,
) -> list[SpareSiteMapDetail]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
        from matplotlib.lines import Line2D  # type: ignore
    except ImportError:
        return []

    detail_maps: list[SpareSiteMapDetail] = []
    offices_lookup = preprocess.offices.set_index("office_id")
    logical_map_width = STATIC_MAP_SIZE[0]
    logical_map_height = STATIC_MAP_SIZE[1]
    map_width = logical_map_width * STATIC_MAP_SCALE
    map_height = logical_map_height * STATIC_MAP_SCALE

    for spare_site in selected_result.selected_sites:
        detail_frame = plottable.loc[
            (plottable["assigned_spare"] == spare_site) | (plottable["office_id"] == spare_site)
        ].copy()
        if detail_frame.empty:
            continue
        center_lat, center_lng, zoom = _fit_static_map(
            detail_frame["latitude"].astype(float).tolist(),
            detail_frame["longitude"].astype(float).tolist(),
            logical_map_width,
            logical_map_height,
            padding_px=MAP_PADDING_PX / STATIC_MAP_SCALE,
        )
        map_image, _ = _fetch_google_static_basemap(
            center_lat=center_lat,
            center_lng=center_lng,
            zoom=zoom,
        )
        center_world_x, center_world_y = _latlng_to_world(center_lat, center_lng, zoom)
        detail_frame["map_x"] = detail_frame.apply(
            lambda row: (
                (_latlng_to_world(float(row.latitude), float(row.longitude), zoom)[0] - center_world_x)
                * STATIC_MAP_SCALE
                + map_width / 2.0
            ),
            axis=1,
        )
        detail_frame["map_y"] = detail_frame.apply(
            lambda row: (
                (_latlng_to_world(float(row.latitude), float(row.longitude), zoom)[1] - center_world_y)
                * STATIC_MAP_SCALE
                + map_height / 2.0
            ),
            axis=1,
        )

        fig, ax = plt.subplots(figsize=(9.0, 6.5))
        if map_image is not None:
            ax.imshow(map_image, extent=(0, map_width, map_height, 0), zorder=0)
        else:
            ax.set_facecolor("#f7f7f7")

        for tier, marker in TIER_MARKERS.items():
            tier_frame = detail_frame.loc[
                (detail_frame["tier"] == tier) & (detail_frame["office_id"] != spare_site)
            ]
            if tier_frame.empty:
                continue
            ax.scatter(
                tier_frame["map_x"],
                tier_frame["map_y"],
                c="#2b6cb0",
                marker=marker,
                s=65,
                edgecolors="white",
                linewidths=0.7,
                alpha=0.92,
                zorder=2,
            )

        spare_frame = detail_frame.loc[detail_frame["office_id"] == spare_site].copy()
        ax.scatter(
            spare_frame["map_x"],
            spare_frame["map_y"],
            marker="*",
            s=360,
            c="#f59e0b",
            edgecolors="black",
            linewidths=1.0,
            zorder=4,
        )
        for row in spare_frame.itertuples(index=False):
            ax.annotate(
                str(row.office_id),
                (float(row.map_x), float(row.map_y)),
                xytext=(8, -10),
                textcoords="offset points",
                fontsize=8,
                weight="bold",
                ha="left",
                va="bottom",
                bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
                zorder=5,
                clip_on=False,
            )

        tier_handles = [
            Line2D([0], [0], marker=marker, color="w", markerfacecolor="#666666", markeredgecolor="white", markersize=8, linestyle="")
            for marker in TIER_MARKERS.values()
        ]
        tier_labels = [f"Tier {tier}" for tier in TIER_MARKERS]
        spare_handle = Line2D([0], [0], marker="*", color="black", markerfacecolor="#f59e0b", markersize=12, linestyle="")
        ax.legend(
            tier_handles + [spare_handle],
            tier_labels + ["Selected warm spare"],
            loc="upper left",
            bbox_to_anchor=(1.01, 1.0),
            title="Marker meaning",
            borderaxespad=0.0,
            fontsize=8,
        )

        ax.set_xlim(-20, map_width + 20)
        ax.set_ylim(map_height + 20, -20)
        ax.set_title(f"Spare Coverage Detail: {spare_site}")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        fig.subplots_adjust(right=0.78, left=0.04, top=0.92, bottom=0.05)

        filename = f"spare_detail_map_{spare_site}.png"
        path = output_dir / filename
        fig.savefig(path, dpi=180)
        plt.close(fig)

        office_row = offices_lookup.loc[spare_site] if spare_site in offices_lookup.index else None
        address = None if office_row is None else office_row.get("address")
        site_name = None if office_row is None else office_row.get("name")
        assigned = selected_result.assignments.loc[selected_result.assignments["assigned_spare"] == spare_site]
        tier_counts = {
            tier: int(assigned.loc[assigned["tier"] == tier].shape[0])
            for tier in [1, 2, 3, 4]
        }
        detail_maps.append(
            SpareSiteMapDetail(
                spare_site=spare_site,
                address=None if pd.isna(address) else str(address),
                site_name=None if pd.isna(site_name) else str(site_name),
                total_offices=int(len(assigned)),
                tier_counts=tier_counts,
                map_path=filename,
            )
        )
    return detail_maps


def _fetch_google_static_basemap(
    *,
    center_lat: float,
    center_lng: float,
    zoom: int,
    marker_params: list[str] | None = None,
) -> tuple[object | None, str | None]:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return None, "Google Maps basemap was not requested because GOOGLE_MAPS_API_KEY was not set in the analysis environment."
    query_params: list[tuple[str, str]] = [
        ("center", f"{center_lat:.6f},{center_lng:.6f}"),
        ("zoom", str(int(zoom))),
        ("size", f"{STATIC_MAP_SIZE[0]}x{STATIC_MAP_SIZE[1]}"),
        ("scale", str(STATIC_MAP_SCALE)),
        ("maptype", STATIC_MAPTYPE),
        ("format", "png"),
        ("key", api_key),
    ]
    for marker in marker_params or []:
        query_params.append(("markers", marker))
    url = "https://maps.googleapis.com/maps/api/staticmap?" + urllib.parse.urlencode(query_params)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read()
    except Exception as exc:
        return None, f"Google Maps basemap request failed; using plain background instead: {exc}"
    try:
        import matplotlib.image as mpimg  # type: ignore
    except ImportError as exc:
        return None, f"Could not decode Google Maps basemap image because matplotlib image support is unavailable: {exc}"
    try:
        image = mpimg.imread(io.BytesIO(data), format="png")
    except Exception as exc:
        return None, f"Could not decode Google Maps basemap image; using plain background instead: {exc}"
    return image, None


def _fit_static_map(
    latitudes: list[float],
    longitudes: list[float],
    map_width: int,
    map_height: int,
    *,
        padding_px: float,
) -> tuple[float, float, int]:
    center_lat = (min(latitudes) + max(latitudes)) / 2.0
    center_lng = (min(longitudes) + max(longitudes)) / 2.0
    best_zoom = MAP_MIN_ZOOM
    for zoom in range(MAP_MAX_ZOOM, MAP_MIN_ZOOM - 1, -1):
        world_points = [_latlng_to_world(lat, lng, zoom) for lat, lng in zip(latitudes, longitudes)]
        xs = [point[0] for point in world_points]
        ys = [point[1] for point in world_points]
        center_world_x = (min(xs) + max(xs)) / 2.0
        center_world_y = (min(ys) + max(ys)) / 2.0
        half_width = map_width / 2.0 - float(padding_px)
        half_height = map_height / 2.0 - float(padding_px)
        if all(abs(x - center_world_x) <= half_width for x in xs) and all(abs(y - center_world_y) <= half_height for y in ys):
            center_lng, center_lat = _world_to_latlng(center_world_x, center_world_y, zoom)
            best_zoom = zoom
            break
    return center_lat, center_lng, best_zoom


def _latlng_to_world(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    scale = 256.0 * (2 ** zoom)
    x = (longitude + 180.0) / 360.0 * scale
    sin_lat = math.sin(math.radians(max(min(latitude, 85.05112878), -85.05112878)))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * scale
    return x, y


def _world_to_latlng(world_x: float, world_y: float, zoom: int) -> tuple[float, float]:
    scale = 256.0 * (2 ** zoom)
    longitude = world_x / scale * 360.0 - 180.0
    latitude = math.degrees(math.atan(math.sinh(math.pi - (2.0 * math.pi * world_y / scale))))
    return longitude, latitude


def _write_alignment_diagnostic(
    *,
    output_dir: Path,
    spare_frame: pd.DataFrame,
    center_lat: float,
    center_lng: float,
    zoom: int,
    map_width: int,
    map_height: int,
    warnings: list[str],
) -> None:
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    marker_specs: list[str] = []
    label_lookup: dict[str, str] = {}
    for index, row in enumerate(spare_frame.itertuples(index=False)):
        label = labels[index % len(labels)]
        label_lookup[str(row.office_id)] = label
        marker_specs.append(
            f"color:red|label:{label}|{float(row.latitude):.6f},{float(row.longitude):.6f}"
        )
    debug_image, warning = _fetch_google_static_basemap(
        center_lat=center_lat,
        center_lng=center_lng,
        zoom=zoom,
        marker_params=marker_specs,
    )
    if warning:
        warnings.append(f"Alignment diagnostic basemap failed: {warning}")
        return
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError as exc:
        warnings.append(f"Alignment diagnostic skipped because matplotlib is unavailable: {exc}")
        return

    fig, ax = plt.subplots(figsize=(11.0, 8.0))
    ax.imshow(debug_image, extent=(0, map_width, map_height, 0), zorder=0)
    ax.scatter(
        spare_frame["map_x"],
        spare_frame["map_y"],
        marker="o",
        s=260,
        facecolors="none",
        edgecolors="cyan",
        linewidths=2.0,
        zorder=3,
    )
    for row in spare_frame.itertuples(index=False):
        label = label_lookup[str(row.office_id)]
        ax.annotate(
            f"{label}={row.office_id}",
            (float(row.map_x), float(row.map_y)),
            xytext=(8, -12),
            textcoords="offset points",
            fontsize=8,
            weight="bold",
            color="cyan",
            bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "alpha": 0.85, "edgecolor": "none"},
            clip_on=False,
            zorder=4,
        )
    ax.set_title("Map Alignment Check: Google marker vs projected overlay")
    ax.text(
        0.01,
        0.01,
        "Red Google marker should sit inside cyan projected circle.",
        transform=ax.transAxes,
        fontsize=9,
        color="black",
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "alpha": 0.85, "edgecolor": "none"},
    )
    ax.set_xlim(-20, map_width + 20)
    ax.set_ylim(map_height + 20, -20)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    fig.savefig(output_dir / "recommended_sites_map_alignment_check.png", dpi=180)
    plt.close(fig)
