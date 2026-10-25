"""Convert official Hong Kong Transport Department road centreline GML.

Only road segments within the requested distance of the configured stations are
retained. Source coordinates are EPSG:2326; output GeoJSON is WGS84.
"""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pyproj import Transformer
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pyproj is required: pip install 'pyproj>=3.6,<4'") from exc

STATIONS_WGS84 = {
    "MK": (114.1683, 22.3227),
    "CB": (114.1851, 22.2802),
    "CL": (114.1581, 22.2818),
}

NS = {
    "core": "http://www.opengis.net/citygml/2.0",
    "gen": "http://www.opengis.net/citygml/generics/2.0",
    "gml": "http://www.opengis.net/gml",
}


def _point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _polyline_distance(point: tuple[float, float], coordinates: list[tuple[float, float]]) -> float:
    if len(coordinates) == 1:
        return math.hypot(point[0] - coordinates[0][0], point[1] - coordinates[0][1])
    return min(
        _point_segment_distance(point[0], point[1], a[0], a[1], b[0], b[1])
        for a, b in zip(coordinates, coordinates[1:])
    )


def _perpendicular_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    return _point_segment_distance(point[0], point[1], start[0], start[1], end[0], end[1])


def _simplify(points: list[tuple[float, float]], tolerance_m: float) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    start, end = points[0], points[-1]
    distances = [_perpendicular_distance(point, start, end) for point in points[1:-1]]
    max_distance = max(distances, default=0.0)
    if max_distance <= tolerance_m:
        return [start, end]
    index = distances.index(max_distance) + 1
    left = _simplify(points[: index + 1], tolerance_m)
    right = _simplify(points[index:], tolerance_m)
    return left[:-1] + right


def _attribute(member: ET.Element, name: str) -> str:
    for tag in ("stringAttribute", "intAttribute", "doubleAttribute"):
        node = member.find(f".//gen:{tag}[@name='{name}']/gen:value", NS)
        if node is not None and node.text:
            return node.text.strip()
    return ""


def convert_roads(
    input_gml: Path,
    output_geojson: Path,
    audit_json: Path,
    radius_km: float = 4.0,
    simplify_m: float = 4.0,
) -> dict[str, Any]:
    to_hk = Transformer.from_crs("EPSG:4326", "EPSG:2326", always_xy=True)
    to_wgs = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)
    stations_hk = {key: to_hk.transform(*coords) for key, coords in STATIONS_WGS84.items()}
    threshold_m = radius_km * 1000.0
    features: list[dict[str, Any]] = []
    source_count = invalid_count = 0

    for _event, member in ET.iterparse(input_gml, events=("end",)):
        if member.tag != f"{{{NS['core']}}}cityObjectMember":
            continue
        source_count += 1
        pos = member.find(".//gml:posList", NS)
        try:
            values = [float(value) for value in (pos.text or "").split()] if pos is not None else []
            coordinates_hk = list(zip(values[0::2], values[1::2]))
        except ValueError:
            coordinates_hk = []
        if len(coordinates_hk) < 2:
            invalid_count += 1
            member.clear()
            continue
        near_stations = [
            station_id
            for station_id, point in stations_hk.items()
            if _polyline_distance(point, coordinates_hk) <= threshold_m
        ]
        if not near_stations:
            member.clear()
            continue
        simplified = _simplify(coordinates_hk, simplify_m)
        coordinates_wgs = [[round(lon, 7), round(lat, 7)] for lon, lat in (to_wgs.transform(x, y) for x, y in simplified)]
        object_id = _attribute(member, "OBJECTID")
        street_name = _attribute(member, "STREET_ENAME")
        street_name_zh = _attribute(member, "STREET_CNAME")
        if street_name in {"-99", "－９９"}:
            street_name = ""
        if street_name_zh in {"-99", "–９９", "－９９"}:
            street_name_zh = ""
        feature = {
            "type": "Feature",
            "id": object_id,
            "geometry": {"type": "LineString", "coordinates": coordinates_wgs},
            "properties": {
                "object_id": object_id,
                "road_id": object_id,
                "street_code": _attribute(member, "ST_CODE"),
                "road_name": street_name.title() if street_name else (street_name_zh or "Unnamed road"),
                "road_name_zh": street_name_zh,
                "road_type": "road_centreline",
                "route_number": _attribute(member, "ROUTE_NUM"),
                "travel_direction": _attribute(member, "TRAVEL_DIRECTION"),
                "near_stations": near_stations,
                "source_updated_at": _attribute(member, "LAST_UPD_DATE_V"),
            },
        }
        features.append(feature)
        member.clear()

    features.sort(key=lambda item: int(item["properties"]["object_id"] or 0))
    collection = {
        "type": "FeatureCollection",
        "name": "road_network",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    output_geojson.parent.mkdir(parents=True, exist_ok=True)
    output_geojson.write_text(json.dumps(collection, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(input_gml),
        "source_crs": "EPSG:2326",
        "output_crs": "EPSG:4326",
        "station_filter_radius_km": radius_km,
        "simplification_tolerance_m": simplify_m,
        "source_feature_count": source_count,
        "invalid_feature_count": invalid_count,
        "kept_feature_count": len(features),
    }
    audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def main() -> None:
    package_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=package_dir / "data" / "raw" / "CENTERLINE.gml")
    parser.add_argument("--output-dir", type=Path, default=package_dir / "data" / "processed")
    parser.add_argument("--radius-km", type=float, default=4.0)
    parser.add_argument("--simplify-m", type=float, default=4.0)
    args = parser.parse_args()
    audit = convert_roads(
        args.input,
        args.output_dir / "road_network.geojson",
        args.output_dir / "road_import_audit.json",
        args.radius_km,
        args.simplify_m,
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
