"""Read the IRNP File Geodatabase and prepare station-area road GeoJSON.

This is an offline ETL dependency only. The Spatial Agent runtime does not
require pyogrio, Shapely, NumPy or pyproj.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pyogrio
    import shapely
    from pyogrio import raw
    from pyproj import Transformer
    from shapely.geometry import Point, mapping
    from shapely.ops import transform as transform_geometry
except ImportError as exc:  # pragma: no cover
    raise SystemExit("ETL dependencies are required: pyogrio, shapely and pyproj") from exc

STATIONS_WGS84 = {
    "MK": (114.1683, 22.3227),
    "CB": (114.1851, 22.2802),
    "CL": (114.1581, 22.2818),
}

FIELDS = [
    "STREET_ENAME",
    "STREET_CNAME",
    "ST_CODE",
    "ROUTE_NUM",
    "TRAVEL_DIRECTION",
    "LAST_UPD_DATE_V",
]


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"-99", "–９９", "－９９", "NaT"} else text


def convert_gdb(
    input_gdb: Path,
    output_geojson: Path,
    audit_json: Path,
    radius_km: float = 4.0,
    simplify_m: float = 4.0,
) -> dict[str, Any]:
    info = pyogrio.read_info(input_gdb, layer="CENTERLINE")
    source_count = int(info["features"])
    source_crs = str(info["crs"])
    if source_crs.upper() != "EPSG:2326":
        raise ValueError(f"Expected EPSG:2326 CENTERLINE data, got {source_crs}")

    to_hk = Transformer.from_crs("EPSG:4326", "EPSG:2326", always_xy=True)
    to_wgs = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)
    stations_hk = {key: to_hk.transform(*coords) for key, coords in STATIONS_WGS84.items()}
    threshold_m = radius_km * 1000.0
    xs = [point[0] for point in stations_hk.values()]
    ys = [point[1] for point in stations_hk.values()]
    bbox = (min(xs) - threshold_m, min(ys) - threshold_m, max(xs) + threshold_m, max(ys) + threshold_m)

    metadata, fids, geometry_wkb, field_arrays = raw.read(
        input_gdb,
        layer="CENTERLINE",
        columns=FIELDS,
        bbox=bbox,
        return_fids=True,
    )
    geometries = shapely.from_wkb(geometry_wkb)
    fields = {name: values for name, values in zip(metadata["fields"], field_arrays)}
    station_points = {key: Point(*coords) for key, coords in stations_hk.items()}
    features: list[dict[str, Any]] = []
    invalid_count = 0

    for index, geometry in enumerate(geometries):
        if geometry is None or geometry.is_empty:
            invalid_count += 1
            continue
        near_stations = [
            station_id
            for station_id, station_point in station_points.items()
            if geometry.distance(station_point) <= threshold_m
        ]
        if not near_stations:
            continue
        object_id = str(int(fids[index]))
        simplified = geometry.simplify(simplify_m, preserve_topology=True)
        wgs_geometry = transform_geometry(to_wgs.transform, simplified)
        street_name = _text(fields["STREET_ENAME"][index])
        street_name_zh = _text(fields["STREET_CNAME"][index])
        features.append(
            {
                "type": "Feature",
                "id": object_id,
                "geometry": mapping(wgs_geometry),
                "properties": {
                    "object_id": object_id,
                    "road_id": object_id,
                    "street_code": _text(fields["ST_CODE"][index]),
                    "road_name": street_name.title() if street_name else (street_name_zh or "Unnamed road"),
                    "road_name_zh": street_name_zh,
                    "road_type": "road_centreline",
                    "route_number": _text(fields["ROUTE_NUM"][index]),
                    "travel_direction": _text(fields["TRAVEL_DIRECTION"][index]),
                    "near_stations": near_stations,
                    "source_updated_at": _text(fields["LAST_UPD_DATE_V"][index]),
                },
            }
        )

    features.sort(key=lambda feature: int(feature["id"]))
    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    collection = {
        "type": "FeatureCollection",
        "name": "road_network",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    output_geojson.write_text(
        json.dumps(collection, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(input_gdb),
        "source_layer": "CENTERLINE",
        "source_crs": source_crs,
        "output_crs": "EPSG:4326",
        "station_filter_radius_km": radius_km,
        "simplification_tolerance_m": simplify_m,
        "source_feature_count": source_count,
        "bbox_candidate_count": len(geometries),
        "invalid_feature_count": invalid_count,
        "kept_feature_count": len(features),
    }
    audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def main() -> None:
    package_dir = Path(__file__).resolve().parents[1]
    repo_root = package_dir.parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=repo_root / "task" / "data" / "RdNet_IRNP.gdb")
    parser.add_argument("--output-dir", type=Path, default=package_dir / "data" / "processed")
    parser.add_argument("--radius-km", type=float, default=4.0)
    parser.add_argument("--simplify-m", type=float, default=4.0)
    args = parser.parse_args()
    audit = convert_gdb(
        args.input,
        args.output_dir / "road_network.geojson",
        args.output_dir / "road_import_audit.json",
        args.radius_km,
        args.simplify_m,
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
